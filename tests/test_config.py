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
                    upload_privacy_status="unlisted",
                    auto_create_user_data=False,
                ),
            )

            loaded = load_app_config(user_data_dir=paths.root)

        self.assertTrue(loaded.config_loaded)
        self.assertEqual(loaded.config.ffmpeg_path, "C:/Tools/ffmpeg.exe")
        self.assertEqual(loaded.config.recording_format, "mp4")
        self.assertEqual(loaded.config.upload_privacy_status, "unlisted")
        self.assertFalse(loaded.config.auto_create_user_data)

    def test_invalid_privacy_status_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = paths.config / "app.toml"
            config_path.write_text('[upload]\nprivacy_status = "public"\n', encoding="utf-8")

            with self.assertRaises(AppConfigError):
                load_app_config(user_data_dir=paths.root)


if __name__ == "__main__":
    unittest.main()
