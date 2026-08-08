from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import struct
import subprocess

from .game_window import WindowSnapshot


MAX_FRAME_BYTES = 50 * 1024 * 1024


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
    completed = subprocess.run(
        list(command),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
        check=False,
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
        if not window.title.strip():
            return FrameCaptureResult(None, "タイトルのないウィンドウはFFmpegで取得できません")
        command = (
            str(self.executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "gdigrab",
            "-i",
            f"title={window.title}",
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


def parse_bmp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 26 or data[:2] != b"BM":
        return None
    width, height = struct.unpack_from("<ii", data, 18)
    width = abs(width)
    height = abs(height)
    if width < 1 or height < 1:
        return None
    return width, height
