// Derived from Microsoft's Application Loopback sample (MIT License).
// This implementation intentionally avoids WIL and Media Foundation so the
// distributed helper has no additional runtime dependency.

#define WIN32_LEAN_AND_MEAN
#include <Windows.h>
#include <Audioclient.h>
#include <audioclientactivationparams.h>
#include <mmdeviceapi.h>
#include <wrl/client.h>

#include <cstdint>
#include <cwchar>
#include <iostream>
#include <string>
#include <vector>

using Microsoft::WRL::ComPtr;

namespace {

constexpr DWORD kActivationTimeoutMs = 10000;
constexpr DWORD kPipeBufferBytes = 1024 * 1024;

class ActivationHandler final : public IActivateAudioInterfaceCompletionHandler,
                                public IAgileObject {
 public:
  ActivationHandler() : event_(CreateEventW(nullptr, TRUE, FALSE, nullptr)) {}

  ~ActivationHandler() {
    if (event_ != nullptr) {
      CloseHandle(event_);
    }
  }

  HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** object) override {
    if (object == nullptr) {
      return E_POINTER;
    }
    *object = nullptr;
    if (iid == __uuidof(IUnknown) ||
        iid == __uuidof(IActivateAudioInterfaceCompletionHandler)) {
      *object = static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
    } else if (iid == __uuidof(IAgileObject)) {
      *object = static_cast<IAgileObject*>(this);
    } else {
      return E_NOINTERFACE;
    }
    AddRef();
    return S_OK;
  }

  ULONG STDMETHODCALLTYPE AddRef() override {
    return static_cast<ULONG>(InterlockedIncrement(&references_));
  }

  ULONG STDMETHODCALLTYPE Release() override {
    const ULONG remaining =
        static_cast<ULONG>(InterlockedDecrement(&references_));
    if (remaining == 0) {
      delete this;
    }
    return remaining;
  }

  HRESULT STDMETHODCALLTYPE ActivateCompleted(
      IActivateAudioInterfaceAsyncOperation* operation) override {
    ComPtr<IUnknown> activated;
    HRESULT operation_result = E_UNEXPECTED;
    HRESULT result = operation->GetActivateResult(&operation_result, &activated);
    if (SUCCEEDED(result)) {
      result = operation_result;
    }
    if (SUCCEEDED(result)) {
      result = activated.As(&audio_client_);
    }
    result_ = result;
    SetEvent(event_);
    return S_OK;
  }

  HANDLE event() const { return event_; }
  HRESULT result() const { return result_; }
  ComPtr<IAudioClient> audio_client() const { return audio_client_; }

 private:
  volatile LONG references_ = 1;
  HANDLE event_ = nullptr;
  HRESULT result_ = E_PENDING;
  ComPtr<IAudioClient> audio_client_;
};

void Diagnostic(const wchar_t* event, HRESULT result = S_OK) {
  std::wcerr << L"event=" << event;
  if (result != S_OK) {
    std::wcerr << L" hresult=0x" << std::hex
               << static_cast<unsigned long>(result) << std::dec;
  }
  std::wcerr << std::endl;
}

bool StopRequested(HANDLE input) {
  if (input == nullptr || input == INVALID_HANDLE_VALUE) {
    return false;
  }
  DWORD available = 0;
  if (!PeekNamedPipe(input, nullptr, 0, nullptr, &available, nullptr)) {
    return GetLastError() == ERROR_BROKEN_PIPE;
  }
  if (available == 0) {
    return false;
  }
  char buffer[16] = {};
  DWORD read = 0;
  if (!ReadFile(input, buffer, sizeof(buffer), &read, nullptr)) {
    return true;
  }
  for (DWORD index = 0; index < read; ++index) {
    if (buffer[index] == 'q' || buffer[index] == 'Q') {
      return true;
    }
  }
  return false;
}

bool WriteAll(HANDLE output, const BYTE* data, DWORD size) {
  DWORD offset = 0;
  while (offset < size) {
    DWORD written = 0;
    if (!WriteFile(output, data + offset, size - offset, &written, nullptr) ||
        written == 0) {
      return false;
    }
    offset += written;
  }
  return true;
}

HRESULT ActivateForProcess(DWORD process_id, ComPtr<IAudioClient>* client) {
  AUDIOCLIENT_ACTIVATION_PARAMS parameters = {};
  parameters.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
  parameters.ProcessLoopbackParams.TargetProcessId = process_id;
  parameters.ProcessLoopbackParams.ProcessLoopbackMode =
      PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;

  PROPVARIANT activation = {};
  activation.vt = VT_BLOB;
  activation.blob.cbSize = sizeof(parameters);
  activation.blob.pBlobData = reinterpret_cast<BYTE*>(&parameters);

  ActivationHandler* handler = new ActivationHandler();
  if (handler->event() == nullptr) {
    handler->Release();
    return HRESULT_FROM_WIN32(GetLastError());
  }
  ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
  HRESULT result = ActivateAudioInterfaceAsync(
      VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, __uuidof(IAudioClient), &activation,
      handler, &operation);
  if (SUCCEEDED(result)) {
    const DWORD wait =
        WaitForSingleObject(handler->event(), kActivationTimeoutMs);
    if (wait == WAIT_OBJECT_0) {
      result = handler->result();
      if (SUCCEEDED(result)) {
        *client = handler->audio_client();
      }
    } else if (wait == WAIT_TIMEOUT) {
      result = HRESULT_FROM_WIN32(ERROR_TIMEOUT);
    } else {
      result = HRESULT_FROM_WIN32(GetLastError());
    }
  }
  handler->Release();
  return result;
}

int Capture(DWORD process_id, const std::wstring& pipe_name) {
  HANDLE process = OpenProcess(SYNCHRONIZE, FALSE, process_id);
  if (process == nullptr) {
    Diagnostic(L"target_open_failed", HRESULT_FROM_WIN32(GetLastError()));
    return 4;
  }

  ComPtr<IAudioClient> audio_client;
  HRESULT result = ActivateForProcess(process_id, &audio_client);
  if (FAILED(result)) {
    Diagnostic(L"activation_failed", result);
    CloseHandle(process);
    return 3;
  }

  WAVEFORMATEX format = {};
  format.wFormatTag = WAVE_FORMAT_PCM;
  format.nChannels = 2;
  format.nSamplesPerSec = 48000;
  format.wBitsPerSample = 16;
  format.nBlockAlign =
      format.nChannels * format.wBitsPerSample / static_cast<WORD>(8);
  format.nAvgBytesPerSec = format.nSamplesPerSec * format.nBlockAlign;

  HANDLE samples_ready = CreateEventW(nullptr, FALSE, FALSE, nullptr);
  if (samples_ready == nullptr) {
    Diagnostic(L"event_failed", HRESULT_FROM_WIN32(GetLastError()));
    CloseHandle(process);
    return 6;
  }
  result = audio_client->Initialize(
      AUDCLNT_SHAREMODE_SHARED,
      AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
          AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM |
          AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
      0, 0, &format, nullptr);
  if (SUCCEEDED(result)) {
    result = audio_client->SetEventHandle(samples_ready);
  }
  ComPtr<IAudioCaptureClient> capture_client;
  if (SUCCEEDED(result)) {
    result = audio_client->GetService(IID_PPV_ARGS(&capture_client));
  }
  if (FAILED(result)) {
    Diagnostic(L"initialize_failed", result);
    CloseHandle(samples_ready);
    CloseHandle(process);
    return 6;
  }

  HANDLE pipe = CreateNamedPipeW(
      pipe_name.c_str(), PIPE_ACCESS_OUTBOUND | FILE_FLAG_OVERLAPPED,
      PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT, 1, kPipeBufferBytes,
      kPipeBufferBytes, 5000, nullptr);
  if (pipe == INVALID_HANDLE_VALUE) {
    Diagnostic(L"pipe_create_failed", HRESULT_FROM_WIN32(GetLastError()));
    CloseHandle(samples_ready);
    CloseHandle(process);
    return 5;
  }
  Diagnostic(L"ready");
  HANDLE input = GetStdHandle(STD_INPUT_HANDLE);
  HANDLE pipe_connected = CreateEventW(nullptr, TRUE, FALSE, nullptr);
  if (pipe_connected == nullptr) {
    Diagnostic(L"pipe_event_failed", HRESULT_FROM_WIN32(GetLastError()));
    CloseHandle(pipe);
    CloseHandle(samples_ready);
    CloseHandle(process);
    return 5;
  }
  OVERLAPPED connection = {};
  connection.hEvent = pipe_connected;
  BOOL connected = ConnectNamedPipe(pipe, &connection);
  DWORD connection_error = connected ? ERROR_SUCCESS : GetLastError();
  if (connected || connection_error == ERROR_PIPE_CONNECTED) {
    SetEvent(pipe_connected);
  } else if (!connected && connection_error != ERROR_IO_PENDING) {
    Diagnostic(L"pipe_connect_failed", HRESULT_FROM_WIN32(connection_error));
    CloseHandle(pipe_connected);
    CloseHandle(pipe);
    CloseHandle(samples_ready);
    CloseHandle(process);
    return 5;
  }
  while (true) {
    const DWORD connection_wait = WaitForSingleObject(pipe_connected, 100);
    if (connection_wait == WAIT_OBJECT_0) {
      break;
    }
    if (connection_wait == WAIT_FAILED) {
      CancelIoEx(pipe, &connection);
      Diagnostic(L"pipe_wait_failed", HRESULT_FROM_WIN32(GetLastError()));
      CloseHandle(pipe_connected);
      CloseHandle(pipe);
      CloseHandle(samples_ready);
      CloseHandle(process);
      return 5;
    }
    if (WaitForSingleObject(process, 0) == WAIT_OBJECT_0) {
      CancelIoEx(pipe, &connection);
      Diagnostic(L"target_ended");
      CloseHandle(pipe_connected);
      CloseHandle(pipe);
      CloseHandle(samples_ready);
      CloseHandle(process);
      return 4;
    }
    if (StopRequested(input)) {
      CancelIoEx(pipe, &connection);
      Diagnostic(L"stopping_before_capture");
      CloseHandle(pipe_connected);
      CloseHandle(pipe);
      CloseHandle(samples_ready);
      CloseHandle(process);
      return 0;
    }
  }
  CloseHandle(pipe_connected);

  result = audio_client->Start();
  if (FAILED(result)) {
    Diagnostic(L"start_failed", result);
    CloseHandle(pipe);
    CloseHandle(samples_ready);
    CloseHandle(process);
    return 6;
  }
  const ULONGLONG capture_started_at = GetTickCount64();
  std::uint64_t frames_written = 0;
  Diagnostic(L"capturing");
  int exit_code = 0;
  while (true) {
    if (WaitForSingleObject(process, 0) == WAIT_OBJECT_0) {
      Diagnostic(L"target_ended");
      exit_code = 4;
      break;
    }
    if (StopRequested(input)) {
      Diagnostic(L"stopping");
      break;
    }
    const DWORD wait = WaitForSingleObject(samples_ready, 20);
    if (wait != WAIT_OBJECT_0 && wait != WAIT_TIMEOUT) {
      Diagnostic(L"sample_wait_failed", HRESULT_FROM_WIN32(GetLastError()));
      exit_code = 6;
      break;
    }
    UINT32 frames = 0;
    while (SUCCEEDED(capture_client->GetNextPacketSize(&frames)) && frames > 0) {
      BYTE* data = nullptr;
      DWORD flags = 0;
      result = capture_client->GetBuffer(&data, &frames, &flags, nullptr, nullptr);
      if (FAILED(result)) {
        exit_code = 6;
        break;
      }
      const DWORD bytes = frames * format.nBlockAlign;
      std::vector<BYTE> silence;
      if ((flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0) {
        silence.assign(bytes, 0);
        data = silence.data();
      }
      const bool written = WriteAll(pipe, data, bytes);
      capture_client->ReleaseBuffer(frames);
      if (!written) {
        Diagnostic(L"pipe_disconnected");
        exit_code = 5;
        break;
      }
      frames_written += frames;
    }
    if (exit_code != 0) {
      break;
    }
    const ULONGLONG elapsed_ms = GetTickCount64() - capture_started_at;
    const std::uint64_t expected_frames =
        elapsed_ms * static_cast<std::uint64_t>(format.nSamplesPerSec) / 1000;
    if (expected_frames > frames_written) {
      const std::uint64_t missing_frames = expected_frames - frames_written;
      const std::uint64_t missing_bytes =
          missing_frames * static_cast<std::uint64_t>(format.nBlockAlign);
      if (missing_bytes > static_cast<std::uint64_t>(MAXDWORD)) {
        Diagnostic(L"silence_gap_too_large");
        exit_code = 6;
        break;
      }
      std::vector<BYTE> silence(static_cast<size_t>(missing_bytes), 0);
      if (!WriteAll(pipe, silence.data(), static_cast<DWORD>(missing_bytes))) {
        Diagnostic(L"pipe_disconnected");
        exit_code = 5;
        break;
      }
      frames_written = expected_frames;
    }
  }

  audio_client->Stop();
  FlushFileBuffers(pipe);
  DisconnectNamedPipe(pipe);
  CloseHandle(pipe);
  CloseHandle(samples_ready);
  CloseHandle(process);
  return exit_code;
}

void Usage() {
  std::wcerr << L"usage: mdrl-audio-loopback --pid <pid> --pipe <\\\\.\\pipe\\name>"
             << std::endl;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
  DWORD process_id = 0;
  std::wstring pipe_name;
  for (int index = 1; index < argc; ++index) {
    if (wcscmp(argv[index], L"--pid") == 0 && index + 1 < argc) {
      process_id = wcstoul(argv[++index], nullptr, 10);
    } else if (wcscmp(argv[index], L"--pipe") == 0 && index + 1 < argc) {
      pipe_name = argv[++index];
    } else if (wcscmp(argv[index], L"--probe") == 0) {
      std::wcerr << L"event=probe_ok format=s16le rate=48000 channels=2"
                 << std::endl;
      return 0;
    } else {
      Usage();
      return 2;
    }
  }
  if (process_id == 0 || pipe_name.rfind(L"\\\\.\\pipe\\", 0) != 0) {
    Usage();
    return 2;
  }
  HRESULT result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
  if (FAILED(result)) {
    Diagnostic(L"com_failed", result);
    return 3;
  }
  const int exit_code = Capture(process_id, pipe_name);
  CoUninitialize();
  return exit_code;
}
