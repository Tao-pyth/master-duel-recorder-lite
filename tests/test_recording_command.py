import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.capture_targets import CaptureInput
from master_duel_recorder_lite.recording_command import RecordingCommandError, build_recording_command
from master_duel_recorder_lite.recording_profile import RecordingProfile


class RecordingCommandTest(unittest.TestCase):
    def test_screen_only_command_is_an_argument_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "recordings"
            root.mkdir()
            output = root / "duel.mkv"
            command = build_recording_command(
                executable=Path("C:/ffmpeg/bin/ffmpeg.exe"),
                profile=RecordingProfile(),
                output_path=output,
                recordings_root=root,
            )

        self.assertIsInstance(command, tuple)
        self.assertIn("gdigrab", command)
        self.assertIn("desktop", command)
        self.assertIn("-an", command)
        self.assertIn("-n", command)
        self.assertEqual(command[-1], str(output.resolve()))

    def test_audio_name_with_spaces_and_japanese_is_one_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "recordings"
            root.mkdir()
            output = root / "duel.mp4"
            profile = RecordingProfile(
                recording_format="mp4",
                audio_input="マイク (USB Audio)",
                frame_rate=60,
                width=1920,
                height=1080,
                video_bitrate_kbps=12_000,
            )
            command = build_recording_command(
                executable=Path("C:/ffmpeg/bin/ffmpeg.exe"),
                profile=profile,
                output_path=output,
                recordings_root=root,
            )

        self.assertIn("audio=マイク (USB Audio)", command)
        self.assertIn("scale=1920:1080", command)
        self.assertIn("12000k", command)
        self.assertIn("+faststart", command)
        self.assertNotIn("-an", command)

    def test_output_outside_recordings_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "recordings"
            root.mkdir()
            with self.assertRaises(RecordingCommandError):
                build_recording_command(
                    executable=Path("C:/ffmpeg/bin/ffmpeg.exe"),
                    profile=RecordingProfile(),
                    output_path=Path(tmp_dir) / "outside.mkv",
                    recordings_root=root,
                )

    def test_existing_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "recordings"
            root.mkdir()
            output = root / "duel.mkv"
            output.write_bytes(b"existing")
            with self.assertRaises(RecordingCommandError):
                build_recording_command(
                    executable=Path("C:/ffmpeg/bin/ffmpeg.exe"),
                    profile=RecordingProfile(),
                    output_path=output,
                    recordings_root=root,
                )

    def test_window_target_uses_hwnd_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "recordings"
            root.mkdir()
            command = build_recording_command(
                executable=Path("C:/ffmpeg/bin/ffmpeg.exe"),
                profile=RecordingProfile(),
                capture_input=CaptureInput(
                    "gdigrab",
                    "title=Master Duel",
                    window_handle=4242,
                    window_title="Master Duel",
                ),
                output_path=root / "window.mkv",
                recordings_root=root,
            )

        self.assertIn("title=Master Duel", command)
        self.assertNotIn("desktop", command)

    def test_monitor_target_places_region_options_before_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "recordings"
            root.mkdir()
            command = build_recording_command(
                executable=Path("C:/ffmpeg/bin/ffmpeg.exe"),
                profile=RecordingProfile(),
                capture_input=CaptureInput(
                    "gdigrab",
                    "desktop",
                    ("-offset_x", "1920", "-offset_y", "0", "-video_size", "1920x1080"),
                ),
                output_path=root / "monitor.mkv",
                recordings_root=root,
            )

        self.assertLess(command.index("-offset_x"), command.index("-i"))
        self.assertIn("1920x1080", command)


if __name__ == "__main__":
    unittest.main()
