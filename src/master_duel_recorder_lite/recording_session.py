from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol, TextIO
import subprocess
import threading
import time


class RecordingState(str, Enum):
    CREATED = "created"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"


TERMINAL_STATES = {RecordingState.COMPLETED, RecordingState.FAILED}


class RecordingStateError(RuntimeError):
    """現在の状態では要求された録画操作を行えないときのエラーです。"""


class ProcessHandle(Protocol):
    stdin: TextIO | None
    stderr: TextIO | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[..., ProcessHandle]
Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]


def describe_process_returncode(returncode: int) -> str:
    unsigned = returncode & 0xFFFFFFFF
    signed = unsigned if unsigned < 0x80000000 else unsigned - 0x100000000
    hexadecimal = f"0x{unsigned:08X}"
    if returncode == signed and signed >= 0:
        return f"{returncode} ({hexadecimal})"
    return f"{returncode} / {signed} ({hexadecimal})"


@dataclass(frozen=True)
class RecordingResult:
    state: RecordingState
    output_path: Path
    returncode: int | None
    started_at: datetime | None
    ended_at: datetime
    size_bytes: int
    error: str | None
    diagnostics: tuple[str, ...]

    @property
    def succeeded(self) -> bool:
        return self.state is RecordingState.COMPLETED


def _default_process_factory(command: Sequence[str], **kwargs: object) -> ProcessHandle:
    return subprocess.Popen(list(command), **kwargs)  # type: ignore[arg-type,return-value]


class RecordingSession:
    def __init__(
        self,
        *,
        command: Sequence[str],
        output_path: Path,
        process_factory: ProcessFactory = _default_process_factory,
        startup_grace_seconds: float = 0.25,
        diagnostic_line_limit: int = 100,
        output_stall_timeout_seconds: float = 30.0,
        clock: Clock | None = None,
        monotonic_clock: MonotonicClock | None = None,
    ) -> None:
        if startup_grace_seconds < 0:
            raise ValueError("startup_grace_seconds は0以上である必要があります")
        if diagnostic_line_limit < 1:
            raise ValueError("diagnostic_line_limit は1以上である必要があります")
        if output_stall_timeout_seconds <= 0:
            raise ValueError("output_stall_timeout_seconds は0より大きい必要があります")
        self.command = tuple(command)
        self.output_path = output_path.resolve()
        self.state = RecordingState.CREATED
        self._process_factory = process_factory
        self._startup_grace_seconds = startup_grace_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._output_stall_timeout_seconds = output_stall_timeout_seconds
        self._process: ProcessHandle | None = None
        self._diagnostics: deque[str] = deque(maxlen=diagnostic_line_limit)
        self._diagnostic_lock = threading.Lock()
        self._stderr_thread: threading.Thread | None = None
        self._started_at: datetime | None = None
        self._result: RecordingResult | None = None
        self._last_output_size = 0
        self._last_output_growth_at: float | None = None

    @property
    def result(self) -> RecordingResult | None:
        return self._result

    @property
    def started_at(self) -> datetime | None:
        return self._started_at

    @property
    def diagnostics(self) -> tuple[str, ...]:
        with self._diagnostic_lock:
            return tuple(self._diagnostics)

    def add_diagnostic(self, line: str) -> None:
        normalized = line.strip()
        if normalized:
            self._append_diagnostic(normalized[:1000])

    def start(self) -> RecordingState:
        if self.state is not RecordingState.CREATED:
            raise RecordingStateError(f"録画はcreated状態からだけ開始できます: {self.state.value}")
        self.state = RecordingState.STARTING
        self._started_at = self._clock()

        if not self.output_path.parent.is_dir():
            self._fail("録画保存先ディレクトリが存在しません", returncode=None)
            return self.state
        if self.output_path.exists():
            self._fail("録画保存先が既に存在します", returncode=None)
            return self.state

        try:
            self._process = self._process_factory(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self._fail(f"FFmpegを開始できません: {exc}", returncode=None)
            return self.state

        self._start_stderr_reader()
        deadline = time.monotonic() + self._startup_grace_seconds
        while True:
            try:
                returncode = self._process.poll()
            except (OSError, ValueError) as exc:
                self._fail(f"FFmpegの開始状態を確認できません: {exc}", returncode=None)
                return self.state
            if returncode is not None:
                self._finalize(returncode, early_exit=True)
                return self.state
            if time.monotonic() >= deadline:
                break
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))

        self.state = RecordingState.RECORDING
        self._last_output_size = self._output_size()
        self._last_output_growth_at = self._monotonic_clock()
        return self.state

    def poll(self) -> RecordingState:
        if self.state in TERMINAL_STATES or self._process is None:
            return self.state
        try:
            returncode = self._process.poll()
        except (OSError, ValueError) as exc:
            self._fail(f"FFmpegの実行状態を確認できません: {exc}", returncode=None)
            return self.state
        if returncode is not None:
            self._finalize(returncode, early_exit=self.state is RecordingState.STARTING)
        elif self.state is RecordingState.RECORDING:
            self._check_output_growth()
        return self.state

    def stop(self, *, timeout_seconds: float = 10.0) -> RecordingResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds は0より大きい必要があります")
        if self.state in TERMINAL_STATES:
            assert self._result is not None
            return self._result
        if self.state is RecordingState.CREATED or self._process is None:
            raise RecordingStateError("開始していない録画は停止できません")

        self.state = RecordingState.STOPPING
        try:
            if self._process.stdin is None:
                raise OSError("FFmpegの標準入力を利用できません")
            self._process.stdin.write("q\n")
            self._process.stdin.flush()
        except (OSError, ValueError) as exc:
            self._append_diagnostic(f"正常停止要求を送信できません: {exc}")

        try:
            returncode = self._process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            try:
                self._process.kill()
            except OSError as exc:
                self._append_diagnostic(f"FFmpegを強制終了できません: {exc}")
            try:
                returncode = self._process.wait(timeout=5.0)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                returncode = None
            self._fail("正常停止がタイムアウトしたためFFmpegを強制終了しました", returncode=returncode)
            assert self._result is not None
            return self._result
        except (OSError, ValueError) as exc:
            try:
                self._process.kill()
            except OSError as kill_exc:
                self._append_diagnostic(f"FFmpegを強制終了できません: {kill_exc}")
            self._fail(f"FFmpegの終了を確認できません: {exc}", returncode=None)
            assert self._result is not None
            return self._result

        self._finalize(returncode, early_exit=False)
        assert self._result is not None
        return self._result

    def _start_stderr_reader(self) -> None:
        assert self._process is not None
        if self._process.stderr is None:
            return

        def read_stderr() -> None:
            assert self._process is not None
            assert self._process.stderr is not None
            try:
                for line in self._process.stderr:
                    normalized = line.strip()
                    if normalized:
                        self._append_diagnostic(normalized[:1000])
            except (OSError, ValueError) as exc:
                self._append_diagnostic(f"FFmpeg診断出力を読み取れません: {exc}")

        self._stderr_thread = threading.Thread(target=read_stderr, name="mdrl-ffmpeg-stderr", daemon=True)
        self._stderr_thread.start()

    def _append_diagnostic(self, line: str) -> None:
        with self._diagnostic_lock:
            self._diagnostics.append(line)

    def _output_size(self) -> int:
        try:
            return self.output_path.stat().st_size if self.output_path.is_file() else 0
        except OSError:
            return 0

    def _check_output_growth(self) -> None:
        assert self._process is not None
        now = self._monotonic_clock()
        size = self._output_size()
        if size > self._last_output_size:
            self._last_output_size = size
            self._last_output_growth_at = now
            return
        if self._last_output_growth_at is None:
            self._last_output_growth_at = now
            return
        if now - self._last_output_growth_at < self._output_stall_timeout_seconds:
            return
        self._append_diagnostic(
            f"出力サイズが{self._output_stall_timeout_seconds:g}秒間増加していません: {size} bytes"
        )
        try:
            self._process.kill()
            returncode = self._process.wait(timeout=5.0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            returncode = None
        self._fail("FFmpegの出力が停止したため録画を終了しました", returncode=returncode)

    def _finalize(self, returncode: int, *, early_exit: bool) -> None:
        self._join_stderr_reader()
        self._close_process_streams()
        size_bytes = self.output_path.stat().st_size if self.output_path.is_file() else 0
        if returncode != 0:
            suffix = f": {self.diagnostics[-1]}" if self.diagnostics else ""
            described = describe_process_returncode(returncode)
            self._fail(f"FFmpegが終了コード{described}で失敗しました{suffix}", returncode=returncode)
            return
        if size_bytes <= 0:
            reason = "録画開始直後にFFmpegが終了し、出力ファイルを確定できませんでした" if early_exit else "録画出力が存在しないか空です"
            self._fail(reason, returncode=returncode)
            return
        self.state = RecordingState.COMPLETED
        self._result = RecordingResult(
            state=self.state,
            output_path=self.output_path,
            returncode=returncode,
            started_at=self._started_at,
            ended_at=self._clock(),
            size_bytes=size_bytes,
            error=None,
            diagnostics=self.diagnostics,
        )

    def _fail(self, error: str, *, returncode: int | None) -> None:
        self._join_stderr_reader()
        self._close_process_streams()
        self.state = RecordingState.FAILED
        size_bytes = self.output_path.stat().st_size if self.output_path.is_file() else 0
        self._result = RecordingResult(
            state=self.state,
            output_path=self.output_path,
            returncode=returncode,
            started_at=self._started_at,
            ended_at=self._clock(),
            size_bytes=size_bytes,
            error=error,
            diagnostics=self.diagnostics,
        )

    def _join_stderr_reader(self) -> None:
        if self._stderr_thread is not None and self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=1.0)

    def _close_process_streams(self) -> None:
        if self._process is None:
            return
        for stream in (self._process.stdin, self._process.stderr):
            if stream is None:
                continue
            try:
                stream.close()
            except (OSError, ValueError):
                pass
