from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import io
from pathlib import Path
import struct
import subprocess
import threading
import time
from typing import BinaryIO, Protocol

from .game_window import WindowSnapshot
from .windows_process import (
    configure_windows_process_errors,
    run_with_windows_retry,
    subprocess_creation_flags,
)


MAX_FRAME_BYTES = 50 * 1024 * 1024
DEFAULT_STREAM_WIDTH = 640


def frame_stream_restart_delay(attempt: int) -> float:
    if attempt < 0:
        raise ValueError("attempt must not be negative")
    return (0.5, 1.0, 2.0)[attempt] if attempt < 3 else 5.0


@dataclass(frozen=True)
class BinaryCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


BinaryCommandRunner = Callable[[Sequence[str], float], BinaryCommandResult]


@dataclass(frozen=True)
class FrameSample:
    captured_at: datetime
    window_handle: int
    window_title: str
    width: int
    height: int
    pixel_format: str
    data: bytes


@dataclass(frozen=True)
class FrameCaptureResult:
    sample: FrameSample | None
    error: str | None

    @property
    def succeeded(self) -> bool:
        return self.sample is not None and self.error is None


def run_binary_command(command: Sequence[str], timeout_seconds: float) -> BinaryCommandResult:
    configure_windows_process_errors()
    completed = run_with_windows_retry(
        lambda: subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess_creation_flags(),
        )
    )
    return BinaryCommandResult(completed.returncode, completed.stdout, completed.stderr)


class FfmpegWindowFrameCapture:
    def __init__(
        self,
        executable: Path,
        *,
        runner: BinaryCommandRunner = run_binary_command,
        timeout_seconds: float = 5.0,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds は0より大きい必要があります")
        if max_frame_bytes < 54:
            raise ValueError("max_frame_bytes は54以上である必要があります")
        self.executable = executable.resolve()
        self.runner = runner
        self.timeout_seconds = timeout_seconds
        self.max_frame_bytes = max_frame_bytes

    def capture(self, window: WindowSnapshot) -> FrameCaptureResult:
        if window.handle <= 0 or not window.title.strip():
            return FrameCaptureResult(None, "有効なウィンドウハンドルとタイトルが必要です")
        command = (
            str(self.executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "gdigrab",
            "-i",
            f"title={window.title.strip()}",
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "bmp",
            "pipe:1",
        )
        try:
            result = self.runner(command, self.timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return FrameCaptureResult(None, f"フレームを取得できません: {exc}")
        if result.returncode != 0:
            diagnostic = result.stderr.decode("utf-8", errors="replace").strip()[-1000:]
            suffix = f": {diagnostic}" if diagnostic else ""
            return FrameCaptureResult(None, f"FFmpegが終了コード{result.returncode}で失敗しました{suffix}")
        if len(result.stdout) > self.max_frame_bytes:
            return FrameCaptureResult(None, "取得フレームが上限サイズを超えました")

        dimensions = parse_bmp_dimensions(result.stdout)
        if dimensions is None:
            return FrameCaptureResult(None, "FFmpeg出力が有効なBMPフレームではありません")
        width, height = dimensions
        return FrameCaptureResult(
            sample=FrameSample(
                captured_at=datetime.now(timezone.utc),
                window_handle=window.handle,
                window_title=window.title,
                width=width,
                height=height,
                pixel_format="bmp",
                data=bytes(result.stdout),
            ),
            error=None,
        )


class FrameStreamProcess(Protocol):
    stdout: BinaryIO | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


FrameStreamProcessFactory = Callable[[Sequence[str]], FrameStreamProcess]
MonotonicClock = Callable[[], float]


def start_frame_stream_process(command: Sequence[str]) -> FrameStreamProcess:
    configure_windows_process_errors()
    return subprocess.Popen(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess_creation_flags(),
    )


class PersistentFfmpegRegionFrameCapture:
    """Master Duel client region captured by one long-lived FFmpeg process."""

    def __init__(
        self,
        executable: Path,
        *,
        maximum_fps: float = 2.0,
        output_width: int = DEFAULT_STREAM_WIDTH,
        stale_after_seconds: float = 3.0,
        process_factory: FrameStreamProcessFactory = start_frame_stream_process,
        monotonic: MonotonicClock = time.monotonic,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        if not 0 < maximum_fps <= 2:
            raise ValueError("maximum_fps must be greater than 0 and at most 2")
        if output_width < 320:
            raise ValueError("output_width must be at least 320")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be greater than 0")
        self.executable = executable.resolve()
        self.maximum_fps = maximum_fps
        self.output_width = output_width
        self.stale_after_seconds = stale_after_seconds
        self.process_factory = process_factory
        self.monotonic = monotonic
        self.max_frame_bytes = max_frame_bytes
        self._condition = threading.Condition()
        self._process: FrameStreamProcess | None = None
        self._reader: threading.Thread | None = None
        self._target: tuple[int, int, int, int, int] | None = None
        self._window: WindowSnapshot | None = None
        self._latest: FrameSample | None = None
        self._sequence = 0
        self._delivered_sequence = 0
        self._last_frame_at = 0.0
        self._last_error: str | None = None
        self._restart_attempt = 0
        self._next_restart_at = 0.0
        self._closed = False
        self.restart_count = 0
        self.generation = 0

    @property
    def active(self) -> bool:
        with self._condition:
            return self._process is not None and self._process.poll() is None

    @property
    def source_description(self) -> str:
        with self._condition:
            target = self._target
        if target is None:
            return "gdigrab desktop / Master Duel client region"
        _, left, top, width, height = target
        return f"gdigrab desktop ({left},{top} {width}x{height})"

    def capture(self, window: WindowSnapshot) -> FrameCaptureResult:
        if window.handle <= 0 or window.width <= 0 or window.height <= 0:
            return FrameCaptureResult(None, "Master Duel client coordinates are unavailable")
        target = (window.handle, window.left, window.top, window.width, window.height)
        with self._condition:
            if self._closed:
                return FrameCaptureResult(None, "frame stream is closed")
            target_changed = target != self._target
        if target_changed:
            self._replace_target(window)
        else:
            self._ensure_running()

        deadline = self.monotonic() + self.stale_after_seconds
        with self._condition:
            while not self._closed and self._sequence <= self._delivered_sequence:
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(min(remaining, 0.25))
            if self._sequence > self._delivered_sequence and self._latest is not None:
                self._delivered_sequence = self._sequence
                return FrameCaptureResult(self._latest, None)
            error = self._last_error or "no frame received for 3 seconds"
        self._schedule_restart()
        return FrameCaptureResult(None, error)

    def stop(self) -> None:
        with self._condition:
            self._closed = True
            process = self._process
            self._process = None
            self._condition.notify_all()
        self._stop_process(process)
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(1.0)
        self._reader = None

    close = stop

    def _replace_target(self, window: WindowSnapshot) -> None:
        with self._condition:
            previous = self._process
            is_restart = self._target is not None
            self._process = None
            self._target = (window.handle, window.left, window.top, window.width, window.height)
            self._window = window
            self._latest = None
            self._sequence = 0
            self._delivered_sequence = 0
            self._last_error = None
            self._restart_attempt = 0
            self._next_restart_at = 0.0
            self.generation += 1
        self._stop_process(previous)
        self._start_process(is_restart=is_restart)

    def _ensure_running(self) -> None:
        with self._condition:
            process = self._process
            last_frame_at = self._last_frame_at
            now = self.monotonic()
            needs_restart = process is None or process.poll() is not None
            if last_frame_at and now - last_frame_at >= self.stale_after_seconds:
                needs_restart = True
            ready = now >= self._next_restart_at
        if needs_restart and ready:
            self._start_process(is_restart=True)

    def _start_process(self, *, is_restart: bool) -> None:
        with self._condition:
            window = self._window
            previous = self._process
            if window is None or self._closed:
                return
            self._process = None
        self._stop_process(previous)
        command = self._build_command(window)
        try:
            process = self.process_factory(command)
        except OSError as exc:
            with self._condition:
                self._last_error = f"could not start FFmpeg frame stream: {exc}"
            self._schedule_restart()
            return
        with self._condition:
            if self._closed:
                should_stop = True
            else:
                should_stop = False
                self._process = process
                self._last_frame_at = self.monotonic()
                self._last_error = None
                if is_restart:
                    self.restart_count += 1
        if should_stop:
            self._stop_process(process)
            return
        reader = threading.Thread(
            target=self._read_frames,
            args=(process, window),
            name="mdrl-frame-stream",
            daemon=True,
        )
        self._reader = reader
        reader.start()

    def _read_frames(self, process: FrameStreamProcess, window: WindowSnapshot) -> None:
        stream = process.stdout
        if stream is None:
            self._mark_reader_failure(process, "FFmpeg stdout is unavailable")
            return
        try:
            while True:
                header = _read_exact(stream, 6)
                if header is None:
                    self._mark_reader_failure(process, "FFmpeg frame stream ended")
                    return
                if header[:2] != b"BM":
                    self._mark_reader_failure(process, "FFmpeg produced an invalid BMP stream")
                    return
                frame_size = struct.unpack_from("<I", header, 2)[0]
                if frame_size < 54 or frame_size > self.max_frame_bytes:
                    self._mark_reader_failure(process, "FFmpeg BMP frame size is invalid")
                    return
                remainder = _read_exact(stream, frame_size - len(header))
                if remainder is None:
                    self._mark_reader_failure(process, "FFmpeg BMP frame was truncated")
                    return
                data = header + remainder
                dimensions = parse_bmp_dimensions(data)
                if dimensions is None:
                    self._mark_reader_failure(process, "FFmpeg produced an invalid BMP frame")
                    return
                width, height = dimensions
                sample = FrameSample(
                    captured_at=datetime.now(timezone.utc),
                    window_handle=window.handle,
                    window_title="",
                    width=width,
                    height=height,
                    pixel_format="bmp",
                    data=data,
                )
                with self._condition:
                    if process is not self._process:
                        return
                    self._latest = sample
                    self._sequence += 1
                    self._last_frame_at = self.monotonic()
                    self._last_error = None
                    self._restart_attempt = 0
                    self._condition.notify_all()
        except (OSError, ValueError) as exc:
            self._mark_reader_failure(process, f"FFmpeg frame stream failed: {exc}")

    def _mark_reader_failure(self, process: FrameStreamProcess, message: str) -> None:
        with self._condition:
            if process is not self._process:
                return
            self._last_error = message
            self._condition.notify_all()

    def _schedule_restart(self) -> None:
        with self._condition:
            process = self._process
            self._process = None
            delay = frame_stream_restart_delay(self._restart_attempt)
            self._restart_attempt += 1
            self._next_restart_at = self.monotonic() + delay
            self._last_error = f"frame stream restart scheduled in {delay:g} seconds"
        self._stop_process(process)

    def _build_command(self, window: WindowSnapshot) -> tuple[str, ...]:
        filters = f"scale='min({self.output_width},iw)':-2"
        return (
            str(self.executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "gdigrab",
            "-draw_mouse",
            "0",
            "-framerate",
            f"{self.maximum_fps:g}",
            "-offset_x",
            str(window.left),
            "-offset_y",
            str(window.top),
            "-video_size",
            f"{window.width}x{window.height}",
            "-i",
            "desktop",
            "-vf",
            filters,
            "-an",
            "-f",
            "image2pipe",
            "-vcodec",
            "bmp",
            "pipe:1",
        )

    @staticmethod
    def _stop_process(process: FrameStreamProcess | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass


def _read_exact(stream: BinaryIO, size: int) -> bytes | None:
    if size == 0:
        return b""
    output = io.BytesIO()
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        output.write(chunk)
        remaining -= len(chunk)
    return output.getvalue()


def parse_bmp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 26 or data[:2] != b"BM":
        return None
    width, height = struct.unpack_from("<ii", data, 18)
    width = abs(width)
    height = abs(height)
    if width < 1 or height < 1:
        return None
    return width, height
