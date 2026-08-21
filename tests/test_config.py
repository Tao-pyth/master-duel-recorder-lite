import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.config import AppConfig, AppConfigError, load_app_config, save_app_config
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


class AppConfigTest(unittest.TestCase):
    def test_missing_config_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            loaded = load_app_config(user_data_dir=Path(tmp_dir) / "user_data")

        self.assertFalse(loaded.config_loaded)
        self.assertEqual(loaded.config.ffmpeg_path, "ffmpeg")
        self.assertEqual(loaded.config.recording_format, "mkv")
        self.assertEqual(loaded.config.screen_input, "desktop")
        self.assertEqual(loaded.config.screen_input_format, "gdigrab")
        self.assertEqual(loaded.config.audio_input, "")
        self.assertEqual(loaded.config.audio_input_format, "dshow")
        self.assertEqual(loaded.config.video_encoder, "libx264")
        self.assertEqual(loaded.config.game_process_name, "masterduel.exe")
        self.assertTrue(loaded.config.auto_start_recording)
        self.assertTrue(loaded.config.auto_stop_recording)
        self.assertEqual(loaded.config.start_confirmations, 3)
        self.assertEqual(loaded.config.stop_confirmations, 5)
        self.assertTrue(loaded.config.visual_detection_enabled)
        self.assertEqual(loaded.config.visual_detection_maximum_fps, 2.0)
        self.assertEqual(loaded.config.visual_detection_language, "auto")
        self.assertEqual(loaded.config.visual_detection_minimum_confidence, 0.70)
        self.assertEqual(loaded.config.upload_privacy_status, "private")

    def test_save_and_load_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            save_app_config(
                paths=paths,
                config=AppConfig(
                    ffmpeg_path="C:/Tools/ffmpeg.exe",
                    recording_format="mp4",
                    screen_input="desktop",
                    screen_input_format="gdigrab",
                    audio_input="マイク (Realtek Audio)",
                    audio_input_format="dshow",
                    video_encoder="h264_nvenc",
                    frame_rate=60,
                    capture_width=1920,
                    capture_height=1080,
                    video_bitrate_kbps=12_000,
                    audio_bitrate_kbps=256,
                    audio_gain_db=-2.5,
                    audio_sample_rate=44_100,
                    audio_channels=1,
                    game_process_name="masterduel-test.exe",
                    game_window_title_contains="Master Duel",
                    auto_start_recording=False,
                    auto_stop_recording=True,
                    start_confirmations=2,
                    stop_confirmations=4,
                    detection_minimum_confidence=0.7,
                    detection_poll_interval_seconds=0.5,
                    detection_cooldown_seconds=20.0,
                    visual_detection_enabled=True,
                    visual_detection_maximum_fps=1.5,
                    visual_detection_language="ja",
                    visual_detection_minimum_confidence=0.8,
                    upload_privacy_status="unlisted",
                    auto_create_user_data=False,
                ),
            )

            loaded = load_app_config(user_data_dir=paths.root)

        self.assertTrue(loaded.config_loaded)
        self.assertEqual(loaded.config.ffmpeg_path, "C:/Tools/ffmpeg.exe")
        self.assertEqual(loaded.config.recording_format, "mp4")
        self.assertEqual(loaded.config.audio_input, "マイク (Realtek Audio)")
        self.assertEqual(loaded.config.video_encoder, "h264_nvenc")
        self.assertEqual(loaded.config.frame_rate, 60)
        self.assertEqual(loaded.config.capture_width, 1920)
        self.assertEqual(loaded.config.capture_height, 1080)
        self.assertEqual(loaded.config.video_bitrate_kbps, 12_000)
        self.assertEqual(loaded.config.audio_bitrate_kbps, 256)
        self.assertEqual(loaded.config.audio_gain_db, -2.5)
        self.assertEqual(loaded.config.audio_sample_rate, 44_100)
        self.assertEqual(loaded.config.audio_channels, 1)
        self.assertEqual(loaded.config.game_process_name, "masterduel-test.exe")
        self.assertEqual(loaded.config.game_window_title_contains, "Master Duel")
        self.assertFalse(loaded.config.auto_start_recording)
        self.assertEqual(loaded.config.start_confirmations, 2)
        self.assertEqual(loaded.config.stop_confirmations, 4)
        self.assertEqual(loaded.config.detection_minimum_confidence, 0.7)
        self.assertEqual(loaded.config.detection_poll_interval_seconds, 0.5)
        self.assertEqual(loaded.config.detection_cooldown_seconds, 20.0)
        self.assertTrue(loaded.config.visual_detection_enabled)
        self.assertEqual(loaded.config.visual_detection_maximum_fps, 1.5)
        self.assertEqual(loaded.config.visual_detection_language, "ja")
        self.assertEqual(loaded.config.visual_detection_minimum_confidence, 0.8)
        self.assertEqual(loaded.config.upload_privacy_status, "unlisted")
        self.assertFalse(loaded.config.auto_create_user_data)

    def test_public_privacy_status_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[upload]\nprivacy_status = "public"\n', encoding="utf-8")

            loaded = load_app_config(user_data_dir=paths.root)

        self.assertEqual(loaded.config.upload_privacy_status, "public")

    def test_invalid_privacy_status_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[upload]\nprivacy_status = "friends"\n', encoding="utf-8")

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)

    def test_old_config_uses_new_recording_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[recorder]\nrecording_format = "mkv"\n', encoding="utf-8")

            loaded = load_app_config(user_data_dir=paths.root)

        self.assertEqual(loaded.config.screen_input, "desktop")
        self.assertEqual(loaded.config.screen_input_format, "gdigrab")
        self.assertEqual(loaded.config.audio_input, "")
        self.assertEqual(loaded.config.video_encoder, "libx264")

    def test_invalid_screen_input_format_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[recorder]\nscreen_input_format = "x11grab"\n', encoding="utf-8")

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)

    def test_blank_required_recording_value_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[recorder]\nscreen_input = "  "\n', encoding="utf-8")

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)

    def test_non_ascii_encoder_name_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[recorder]\nvideo_encoder = "エンコーダー"\n', encoding="utf-8")

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)

    def test_partial_resolution_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[recorder]\ncapture_width = 1920\n', encoding="utf-8")

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)

    def test_out_of_range_frame_rate_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[recorder]\nframe_rate = 0\n', encoding="utf-8")

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)

    def test_invalid_game_process_name_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[detection]\ngame_process_name = "master duel.exe"\n', encoding="utf-8")

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)

    def test_out_of_range_detection_interval_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[detection]\npoll_interval_seconds = 0.0\n', encoding="utf-8")

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)

    def test_visual_detection_limits_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text(
                '[detection]\nvisual_maximum_fps = 2.1\nvisual_minimum_confidence = 0.69\n',
                encoding="utf-8",
            )

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)

    def test_save_rejects_boolean_recording_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)

            with self.assertRaises(ValueError):
                save_app_config(paths=paths, config=AppConfig(frame_rate=True))

    def test_control_characters_round_trip_through_toml(self) -> None:
        title = 'Master "Duel"\nWindow\tTitle\\Suffix'
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            save_app_config(
                paths=paths,
                config=AppConfig(game_window_title_contains=title),
            )

            loaded = load_app_config(user_data_dir=paths.root)

        self.assertEqual(loaded.config.game_window_title_contains, title)


if __name__ == "__main__":
    unittest.main()
