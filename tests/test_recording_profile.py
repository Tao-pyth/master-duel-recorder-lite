import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.config import AppConfig, load_app_config, save_app_config
from master_duel_recorder_lite.recording_profile import RecordingProfile, RecordingProfileError
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


class RecordingProfileTest(unittest.TestCase):
    def test_default_profile_uses_source_resolution(self) -> None:
        profile = RecordingProfile.from_config(AppConfig())

        self.assertEqual(profile.frame_rate, 30)
        self.assertIsNone(profile.width)
        self.assertIsNone(profile.height)
        self.assertEqual(profile.video_bitrate_kbps, 6000)
        self.assertFalse(profile.has_audio)
        self.assertEqual(profile.extension, ".mkv")

    def test_saved_config_round_trips_to_profile(self) -> None:
        config = AppConfig(
            recording_format="mp4",
            audio_input="マイク (USB Audio)",
            frame_rate=60,
            capture_width=1920,
            capture_height=1080,
            video_bitrate_kbps=12_000,
            audio_bitrate_kbps=256,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            save_app_config(paths=paths, config=config)

            loaded = load_app_config(user_data_dir=paths.root)
            profile = RecordingProfile.from_config(loaded.config)

        self.assertEqual(profile.recording_format, "mp4")
        self.assertEqual(profile.audio_input, "マイク (USB Audio)")
        self.assertEqual((profile.width, profile.height), (1920, 1080))
        self.assertEqual(profile.frame_rate, 60)
        self.assertEqual(profile.video_bitrate_kbps, 12_000)
        self.assertEqual(profile.audio_bitrate_kbps, 256)

    def test_partial_resolution_is_rejected(self) -> None:
        with self.assertRaises(RecordingProfileError):
            RecordingProfile(width=1920, height=None)

    def test_odd_resolution_is_rejected(self) -> None:
        with self.assertRaises(RecordingProfileError):
            RecordingProfile(width=1921, height=1080)

    def test_boolean_frame_rate_is_rejected(self) -> None:
        with self.assertRaises(RecordingProfileError):
            RecordingProfile(frame_rate=True)

    def test_non_ascii_encoder_is_rejected(self) -> None:
        with self.assertRaises(RecordingProfileError):
            RecordingProfile(video_encoder="エンコーダー")


if __name__ == "__main__":
    unittest.main()
