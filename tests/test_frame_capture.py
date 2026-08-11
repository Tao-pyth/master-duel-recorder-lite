import struct
import threading
import unittest
from pathlib import Path

from master_duel_recorder_lite.frame_capture import (
    BinaryCommandResult,
    FfmpegWindowFrameCapture,
    PersistentFfmpegRegionFrameCapture,
    frame_stream_restart_delay,
    parse_bmp_dimensions,
)
from master_duel_recorder_lite.game_window import WindowSnapshot


def bmp(width: int = 640, height: int = 480) -> bytes:
    header = bytearray(54)
    header[:2] = b"BM"
    struct.pack_into("<ii", header, 18, width, height)
    return bytes(header)


def stream_bmp(width: int = 640, height: int = 480) -> bytes:
    frame = bytearray(bmp(width, height))
    struct.pack_into("<I", frame, 2, len(frame))
    return bytes(frame)


class FakeStreamProcess:
    def __init__(self, output: bytes) -> None:
        from io import BytesIO

        self.stdout = BytesIO(output)
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -1

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


class BlockingByteStream:
    def __init__(self, initial: bytes) -> None:
        self.buffer = bytearray(initial)
        self.closed = False
        self.condition = threading.Condition()

    def feed(self, data: bytes) -> None:
        with self.condition:
            self.buffer.extend(data)
            self.condition.notify_all()

    def read(self, size: int) -> bytes:
        with self.condition:
            while len(self.buffer) < size and not self.closed:
                self.condition.wait(0.1)
            if len(self.buffer) < size:
                return b""
            data = bytes(self.buffer[:size])
            del self.buffer[:size]
            return data

    def close(self) -> None:
        with self.condition:
            self.closed = True
            self.condition.notify_all()


class FakePersistentProcess(FakeStreamProcess):
    def __init__(self, output: bytes) -> None:
        self.stdout = BlockingByteStream(output)
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = 0
        self.stdout.close()

    def kill(self) -> None:
        self.returncode = -1
        self.stdout.close()


class FrameCaptureTest(unittest.TestCase):
    def test_restart_backoff_uses_three_short_retries_then_five_seconds(self) -> None:
        self.assertEqual(
            [frame_stream_restart_delay(attempt) for attempt in range(6)],
            [0.5, 1.0, 2.0, 5.0, 5.0, 5.0],
        )

    def test_window_title_is_one_argument_and_sample_stays_in_memory(self) -> None:
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

    def test_persistent_capture_uses_desktop_region_and_single_process(self) -> None:
        commands: list[tuple[str, ...]] = []

        processes: list[FakePersistentProcess] = []

        def factory(command: object) -> FakePersistentProcess:
            commands.append(tuple(command))  # type: ignore[arg-type]
            process = FakePersistentProcess(stream_bmp(640, 268))
            processes.append(process)
            return process

        capture = PersistentFfmpegRegionFrameCapture(
            Path("ffmpeg.exe"),
            process_factory=factory,
            stale_after_seconds=0.5,
        )
        window = WindowSnapshot(123, 42, "Master Duel", True, False, 3440, 1440, -3440, 0)
        first = capture.capture(window)
        processes[0].stdout.feed(stream_bmp(640, 268))
        second = capture.capture(window)
        capture.stop()

        self.assertTrue(first.succeeded)
        self.assertTrue(second.succeeded)
        self.assertEqual(len(commands), 1)
        self.assertIn("desktop", commands[0])
        self.assertNotIn("title=Master Duel", commands[0])
        self.assertEqual(commands[0][commands[0].index("-offset_x") + 1], "-3440")
        self.assertEqual(commands[0][commands[0].index("-video_size") + 1], "3440x1440")
        self.assertIn("min(640,iw)", commands[0][commands[0].index("-vf") + 1])

    def test_persistent_capture_restarts_when_client_region_moves(self) -> None:
        commands: list[tuple[str, ...]] = []

        def factory(command: object) -> FakeStreamProcess:
            commands.append(tuple(command))  # type: ignore[arg-type]
            return FakeStreamProcess(stream_bmp())

        capture = PersistentFfmpegRegionFrameCapture(
            Path("ffmpeg.exe"),
            process_factory=factory,
            stale_after_seconds=0.5,
        )
        capture.capture(WindowSnapshot(123, 42, "Master Duel", True, False, 640, 480, 0, 0))
        capture.capture(WindowSnapshot(123, 42, "Master Duel", True, False, 640, 480, 100, 50))
        capture.stop()

        self.assertEqual(len(commands), 2)
        self.assertEqual(capture.restart_count, 1)
        self.assertEqual(capture.generation, 2)
        self.assertEqual(commands[1][commands[1].index("-offset_x") + 1], "100")
        self.assertEqual(commands[1][commands[1].index("-offset_y") + 1], "50")


if __name__ == "__main__":
    unittest.main()
