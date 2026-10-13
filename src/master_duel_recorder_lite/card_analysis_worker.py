"""DBを開かない、低優先度の録画解析子プロセス。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
import json
import os
from pathlib import Path
import sys

from .card_ocr import CardAnalysisError
from .card_video_analysis import analyze_video


@contextmanager
def worker_standard_streams():
    """windowed EXEでも、親が渡したpipeだけをworker実行中に復元する。"""
    original = (sys.stdout, sys.stderr)
    owned = []
    if os.name == "nt" and (sys.stdout is None or sys.stderr is None):
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.windll.kernel32
        kernel.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                           ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.DuplicateHandle.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        current = kernel.GetCurrentProcess()
        for name, value in (("stdout", -11), ("stderr", -12)):
            if getattr(sys, name) is not None:
                continue
            handle = kernel.GetStdHandle(value & 0xFFFFFFFF)
            if handle is None or handle == ctypes.c_void_p(-1).value:
                continue
            duplicate = wintypes.HANDLE()
            if not kernel.DuplicateHandle(current, handle, current, ctypes.byref(duplicate), 0, False, 2):
                continue
            try:
                fd = msvcrt.open_osfhandle(duplicate.value, os.O_WRONLY | os.O_BINARY)
            except OSError:
                kernel.CloseHandle(duplicate)
                raise
            try:
                stream = os.fdopen(fd, "w", encoding="utf-8", errors="backslashreplace", buffering=1, newline="\n")
            except OSError:
                os.close(fd)
                raise
            owned.append(stream)
            setattr(sys, name, stream)
    try:
        yield
    finally:
        sys.stdout, sys.stderr = original
        for stream in owned:
            stream.close()


class OwnerProcess:
    def __init__(self, pid: object) -> None:
        self.pid = pid
        self.handle = None
        if pid is None:
            return
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise CardAnalysisError("invalid_request", "解析所有プロセスのPIDが不正です")
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            self.kernel = ctypes.windll.kernel32
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel.CloseHandle.restype = wintypes.BOOL
            self.handle = self.kernel.OpenProcess(0x00100000, False, pid)
            if not self.handle:
                raise CardAnalysisError("analysis_cancelled", "解析の所有プロセスを確認できません")

    def cancelled(self) -> bool:
        if self.pid is None:
            return False
        if os.name == "nt":
            return self.kernel.WaitForSingleObject(self.handle, 0) != 258  # WAIT_TIMEOUT
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def lower_process_priority() -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.SetPriorityClass.restype = wintypes.BOOL
        if not kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00004000):  # BELOW_NORMAL_PRIORITY_CLASS
            raise ctypes.WinError()
    else:
        os.nice(5)


def main(argv: list[str] | None = None) -> int:
    try:
        with worker_standard_streams():
            return _run(argv)
    except BrokenPipeError:
        return 1


def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="使用カード解析の独立worker")
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args(argv)
    protocol = sys.stdout
    if protocol is None:
        return 1

    def emit(document: dict[str, object]) -> None:
        protocol.write(json.dumps(document, ensure_ascii=True, allow_nan=False) + "\n")
        protocol.flush()

    owner = outer_owner = None
    try:
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ[name] = "1"
        lower_process_priority()
        request = json.loads(args.request.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise CardAnalysisError("invalid_request", "解析requestはobjectで指定してください")
        owner = OwnerProcess(request.get("owner_pid"))
        if os.name == "nt" and (owner.pid is not None or getattr(sys, "frozen", False)):
            # onefileとWindows venvはlauncher/innerに分かれる。直親の終了にも従う。
            outer_pid = os.getppid()
            if outer_pid > 0 and outer_pid != owner.pid:
                outer_owner = OwnerProcess(outer_pid)

        def cancelled() -> bool:
            return owner.cancelled() or outer_owner is not None and outer_owner.cancelled()

        # ライブラリの表示をstdout protocolへ混ぜない。progressは保存済みstreamへ送る。
        with redirect_stdout(sys.stderr):
            result = analyze_video(request, progress_callback=emit, cancel_requested=cancelled)
        emit({"type": "result", **result})
        return 0
    except BrokenPipeError:
        return 1
    except CardAnalysisError as exc:
        emit({"type": "error", "code": exc.code, "message": str(exc)})
    except (OSError, ValueError, TypeError) as exc:
        emit({"type": "error", "code": "invalid_request_or_io", "message": str(exc)})
    except Exception as exc:
        emit({"type": "error", "code": "analysis_failed", "message": f"{type(exc).__name__}: {exc}"})
    finally:
        if outer_owner is not None:
            outer_owner.close()
        if owner is not None:
            owner.close()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
