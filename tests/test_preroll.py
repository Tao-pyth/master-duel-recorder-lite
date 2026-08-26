import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from master_duel_recorder_lite.capture_targets import CaptureInput
from master_duel_recorder_lite.preroll import (
    FrozenPreroll,
    PrerollCaptureBuffer,
    PrerollRecordingSession,
    build_preroll_segment_command,
    new_preroll_buffer,
)
from master_duel_recorder_lite.recording_profile import RecordingProfile
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stderr = io.StringIO("diagnostic\n")
        self.returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeMainSession:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.state = RecordingState.CREATED
        self.started_at = datetime(2026, 8, 8, tzinfo=timezone.utc)
        self.result: RecordingResult | None = None
        self.diagnostics: tuple[str, ...] = ()
        self.audio_warning = None

    def add_diagnostic(self, line: str) -> None:
        self.diagnostics = (*self.diagnostics, line)

    def start(self) -> RecordingState:
        self.state = RecordingState.RECORDING
        return self.state

    def poll(self) -> RecordingState:
        return self.state

    def stop(self, *, timeout_seconds: float = 10.0) -> RecordingResult:
        self.output_path.write_bytes(b"main")
        ended = self.started_at + timedelta(seconds=5)
        self.state = RecordingState.COMPLETED
        self.result = RecordingResult(
            self.state,
            self.output_path,
            0,
            self.started_at,
            ended,
            4,
            None,
            self.diagnostics,
        )
        return self.result


class PrerollTest(unittest.TestCase):
    def test_segment_command_uses_wrap_and_silent_audio_for_audio_profile(self) -> None:
        command = build_preroll_segment_command(
            executable=Path("ffmpeg.exe"),
            profile=RecordingProfile(audio_mode="process"),
            capture_input=CaptureInput("gdigrab", "title=Master Duel"),
            output_pattern=Path("segment_%03d.mkv"),
            segment_count=6,
        )

        self.assertIn("-segment_wrap", command)
        self.assertIn("6", command)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", command)

    def test_capture_buffer_freezes_newest_segments_with_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            directory = Path(tmp_dir)
            clock = [0.0]
            process = FakeProcess()
            buffer = PrerollCaptureBuffer(
                command=("ffmpeg",),
                directory=directory,
                max_bytes=8,
                process_factory=lambda *args, **kwargs: process,
                monotonic_clock=lambda: clock[0],
            )
            buffer.start()
            for index, payload in enumerate((b"old", b"middle", b"new")):
                path = directory / f"segment_{index:03d}.mkv"
                path.write_bytes(payload)
                os.utime(path, (index + 1, index + 1))
            clock[0] = 2.5

            frozen = buffer.freeze()

        self.assertEqual([path.name for path in frozen.segments], ["segment_002.mkv"])
        self.assertEqual(frozen.offset_ms, 1000)
        self.assertIn("diagnostic", frozen.diagnostics)

    def test_capture_buffer_caps_segments_by_configured_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            directory = Path(tmp_dir)
            process = FakeProcess()
            clock = [0.0]
            buffer = PrerollCaptureBuffer(
                command=("ffmpeg",),
                directory=directory,
                max_bytes=1024,
                max_segments=2,
                process_factory=lambda *args, **kwargs: process,
                monotonic_clock=lambda: clock[0],
            )
            buffer.start()
            for index in range(4):
                path = directory / f"segment_{index:03d}.mkv"
                path.write_bytes(f"segment-{index}".encode("ascii"))
                os.utime(path, (index + 1, index + 1))
            clock[0] = 10.0

            frozen = buffer.freeze()

        self.assertEqual(
            [path.name for path in frozen.segments],
            ["segment_002.mkv", "segment_003.mkv"],
        )
        self.assertEqual(frozen.offset_ms, 2000)

    def test_new_preroll_buffer_uses_seconds_as_segment_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir))

            buffer = new_preroll_buffer(
                paths=paths,
                executable=Path("ffmpeg.exe"),
                profile=RecordingProfile(recording_format="mkv"),
                capture_input=CaptureInput("gdigrab", "title=Master Duel"),
                seconds=5,
                max_megabytes=64,
            )

        self.assertEqual(buffer.max_segments, 5)
        self.assertIn("-segment_wrap", buffer.command)
        self.assertIn("6", buffer.command)

    def test_preroll_recording_session_merges_segments_before_main_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            segment = root / "segment_000.mkv"
            segment.write_bytes(b"pre")
            main = root / "main.mkv"
            final = root / "final.mkv"
            session = PrerollRecordingSession(
                main_session=FakeMainSession(main),  # type: ignore[arg-type]
                main_output_path=main,
                final_output_path=final,
                frozen_preroll=FrozenPreroll((segment,), 1000),
                executable=Path("ffmpeg.exe"),
                recording_format="mkv",
                command_runner=lambda command: (Path(command[-1]).write_bytes(b"pre+main") and 0, ""),
            )

            result = session.stop()

            self.assertTrue(result.succeeded)
            self.assertEqual(result.output_path, final)
            self.assertEqual(result.size_bytes, 8)
            self.assertEqual(final.read_bytes(), b"pre+main")
            self.assertFalse(main.exists())
            self.assertFalse(segment.exists())

    def test_preroll_recording_session_falls_back_to_main_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            segment = root / "segment_000.mkv"
            segment.write_bytes(b"pre")
            main = root / "main.mkv"
            final = root / "final.mkv"
            session = PrerollRecordingSession(
                main_session=FakeMainSession(main),  # type: ignore[arg-type]
                main_output_path=main,
                final_output_path=final,
                frozen_preroll=FrozenPreroll((segment,), 1000),
                executable=Path("ffmpeg.exe"),
                recording_format="mkv",
                command_runner=lambda _command: (1, "concat failed"),
            )

            result = session.stop()

            self.assertTrue(result.succeeded)
            self.assertEqual(final.read_bytes(), b"main")
            self.assertFalse(segment.exists())
            self.assertIn("プリロール結合に失敗", result.diagnostics[-1])


if __name__ == "__main__":
    unittest.main()
