from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from typing import Protocol, TextIO
import uuid

from .windows_process import configure_windows_process_errors, subprocess_creation_flags


MINIMUM_PROCESS_LOOPBACK_BUILD = 20348
HELPER_NAME = "mdrl-audio-loopback.exe"


class ProcessLoopbackError(RuntimeError):
    """プロセス単体音声を初期化または停止できない場合のエラーです。"""


@dataclass(frozen=True)
class ProcessLoopbackCapability:
    supported: bool
    windows_build: int
    helper_path: Path | None
    message: str


class HelperProcess(Protocol):
    stdin: TextIO | None
    stderr: TextIO | None

    def poll(self) -> int | None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def kill(self) -> None: ...


def process_loopback_capability(
    *, helper_path: Path | None = None, windows_build: int | None = None
) -> ProcessLoopbackCapability:
    build = windows_build if windows_build is not None else _windows_build()
    helper = (helper_path or find_process_loopback_helper()).resolve()
    if platform.system() != "Windows":
        return ProcessLoopbackCapability(False, build, None, "Windows専用機能です")
    if build < MINIMUM_PROCESS_LOOPBACK_BUILD:
        return ProcessLoopbackCapability(
            False,
            build,
            helper if helper.is_file() else None,
            f"Windows build {MINIMUM_PROCESS_LOOPBACK_BUILD}以上が必要です（現在{build}）",
        )
    if not helper.is_file():
        return ProcessLoopbackCapability(
            False, build, None, f"音声ヘルパーが見つかりません: {helper}"
        )
    return ProcessLoopbackCapability(
        True,
        build,
        helper,
        f"Master Duel単体音声を利用できます（Windows build {build}）",
    )


def find_process_loopback_helper() -> Path:
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return root / "native" / HELPER_NAME
    project_root = Path(__file__).resolve().parents[2]
    return project_root / "native" / "audio_loopback" / "bin" / HELPER_NAME


def new_audio_pipe_name(recording_id: str) -> str:
    safe = "".join(char for char in recording_id if char.isascii() and char.isalnum())
    return rf"\\.\pipe\mdrl-audio-{safe[:32]}-{uuid.uuid4().hex}"


class ProcessLoopbackController:
    def __init__(
        self,
        *,
        helper_path: Path,
        process_id: int,
        pipe_name: str,
        startup_timeout_seconds: float = 10.0,
    ) -> None:
        if process_id <= 0:
            raise ValueError("process_idは1以上である必要があります")
        if not pipe_name.startswith("\\\\.\\pipe\\"):
            raise ValueError("pipe_nameはWindows名前付きパイプである必要があります")
        self.helper_path = helper_path.resolve()
        self.process_id = process_id
        self.pipe_name = pipe_name
        self.startup_timeout_seconds = startup_timeout_seconds
        self._process: HelperProcess | None = None
        self._ready = threading.Event()
        self._capturing = threading.Event()
        self._diagnostics: deque[str] = deque(maxlen=100)
        self._reader: threading.Thread | None = None
        self._warning: str | None = None

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    @property
    def warning(self) -> str | None:
        return self._warning

    def start(self) -> None:
        if self._process is not None:
            if self._process.poll() is None and self._ready.is_set():
                return
            raise ProcessLoopbackError("音声ヘルパーはすでに終了しています")
        if not self.helper_path.is_file():
            raise ProcessLoopbackError(
                f"音声ヘルパーが見つかりません: {self.helper_path}"
            )
        configure_windows_process_errors()
        try:
            self._process = subprocess.Popen(
                [
                    str(self.helper_path),
                    "--pid",
                    str(self.process_id),
                    "--pipe",
                    self.pipe_name,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess_creation_flags(),
            )
        except OSError as exc:
            raise ProcessLoopbackError(f"音声ヘルパーを開始できません: {exc}") from exc
        self._start_reader()
        deadline = time.monotonic() + self.startup_timeout_seconds
        while not self._ready.wait(timeout=0.05):
            returncode = self._process.poll()
            if returncode is not None:
                raise ProcessLoopbackError(
                    f"音声ヘルパーが初期化前に終了しました: {returncode}: "
                    f"{self._last_diagnostic()}"
                )
            if time.monotonic() >= deadline:
                self.stop()
                raise ProcessLoopbackError(
                    "音声ヘルパーの初期化が10秒で完了しませんでした"
                )

    def poll(self) -> str | None:
        if self._process is None:
            return self._warning
        returncode = self._process.poll()
        if returncode is not None and self._warning is None:
            self._warning = (
                f"Master Duel単体音声が終了しました（終了コード{returncode}）: "
                f"{self._last_diagnostic()}。映像録画は継続します"
            )
        return self._warning

    def wait_until_capturing(self, timeout_seconds: float) -> bool:
        if timeout_seconds <= 0:
            raise ValueError("timeout_secondsは0より大きい必要があります")
        return self._capturing.wait(timeout=timeout_seconds)

    def request_stop(self) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            return
        try:
            process.stdin.write("q\n")
            process.stdin.flush()
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self.request_stop()
                process.wait(timeout=3.0)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=2.0)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=1.0)
        for stream in (process.stdin, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def _start_reader(self) -> None:
        assert self._process is not None

        def read() -> None:
            assert self._process is not None
            if self._process.stderr is None:
                return
            try:
                for raw in self._process.stderr:
                    line = raw.strip()
                    if not line:
                        continue
                    self._diagnostics.append(line[:1000])
                    if line.startswith("event=ready"):
                        self._ready.set()
                    elif line.startswith("event=capturing"):
                        self._capturing.set()
            except (OSError, ValueError) as exc:
                self._diagnostics.append(f"音声診断を読み取れません: {exc}")

        self._reader = threading.Thread(
            target=read, name="mdrl-process-audio-stderr", daemon=True
        )
        self._reader.start()

    def _last_diagnostic(self) -> str:
        return self._diagnostics[-1] if self._diagnostics else "診断なし"


def _windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError):
        return 0
