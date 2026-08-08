import struct
import unittest
from pathlib import Path

from master_duel_recorder_lite.frame_capture import (
    BinaryCommandResult,
    FfmpegWindowFrameCapture,
    parse_bmp_dimensions,
)
from master_duel_recorder_lite.game_window import WindowSnapshot


def bmp(width: int = 640, height: int = 480) -> bytes:
    header = bytearray(54)
    header[:2] = b"BM"
    struct.pack_into("<ii", header, 18, width, height)
    return bytes(header)


class FrameCaptureTest(unittest.TestCase):
    def test_japanese_window_title_is_one_argument_and_sample_stays_in_memory(self) -> None:
        captured_command: tuple[str, ...] | None = None

        def runner(command: tuple[str, ...], _timeout: float) -> BinaryCommandResult:
            nonlocal captured_command
            captured_command = tuple(command)
            return BinaryCommandResult(0, bmp(), b"")

        window = WindowSnapshot(123, 42, "遊戯王 マスターデュエル", True, False, 640, 480)
        result = FfmpegWindowFrameCapture(Path("ffmpeg.exe"), runner=runner).capture(window)

        self.assertTrue(result.succeeded)
        assert result.sample is not None
        self.assertEqual((result.sample.width, result.sample.height), (640, 480))
        self.assertEqual(result.sample.data, bmp())
        assert captured_command is not None
        self.assertIn("title=遊戯王 マスターデュエル", captured_command)

    def test_ffmpeg_failure_is_structured(self) -> None:
        window = WindowSnapshot(123, 42, "Master Duel", True, False, 640, 480)
        capture = FfmpegWindowFrameCapture(
            Path("ffmpeg.exe"),
            runner=lambda _command, _timeout: BinaryCommandResult(1, b"", b"window not found"),
        )

        result = capture.capture(window)

        self.assertFalse(result.succeeded)
        self.assertIn("window not found", result.error or "")

    def test_invalid_bmp_is_rejected(self) -> None:
        window = WindowSnapshot(123, 42, "Master Duel", True, False, 640, 480)
        capture = FfmpegWindowFrameCapture(
            Path("ffmpeg.exe"),
            runner=lambda _command, _timeout: BinaryCommandResult(0, b"not-a-bmp", b""),
        )

        result = capture.capture(window)

        self.assertFalse(result.succeeded)
        self.assertIn("BMP", result.error or "")

    def test_size_limit_is_enforced(self) -> None:
        window = WindowSnapshot(123, 42, "Master Duel", True, False, 640, 480)
        capture = FfmpegWindowFrameCapture(
            Path("ffmpeg.exe"),
            max_frame_bytes=54,
            runner=lambda _command, _timeout: BinaryCommandResult(0, bmp() + b"x", b""),
        )

        result = capture.capture(window)

        self.assertFalse(result.succeeded)
        self.assertIn("上限", result.error or "")

    def test_bmp_dimensions_support_top_down_images(self) -> None:
        self.assertEqual(parse_bmp_dimensions(bmp(1920, -1080)), (1920, 1080))


if __name__ == "__main__":
    unittest.main()
