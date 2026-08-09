import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.config import AppConfig, AppConfigError, load_app_config, save_app_config
from master_duel_recorder_lite.config_management import (
    ConfigValueError,
    config_value,
    config_values,
    updated_config,
)
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


class ConfigManagementTest(unittest.TestCase):
    def test_lists_only_supported_non_secret_keys(self) -> None:
        values = config_values(AppConfig())

        self.assertEqual(values["recorder.frame_rate"], 30)
        self.assertEqual(values["upload.privacy_status"], "private")
        self.assertEqual(values["detection.visual_maximum_fps"], 2.0)
        self.assertFalse(any("token" in key or "secret" in key or "key" in key for key in values))

    def test_updates_typed_value_and_validates_whole_config(self) -> None:
        config = updated_config(AppConfig(), "recorder.frame_rate", "60")
        config = updated_config(config, "detection.auto_start_recording", "false")
        config = updated_config(config, "upload.privacy_status", " UNLISTED ")
        config = updated_config(config, "detection.visual_minimum_confidence", "0.8")

        self.assertEqual(config.frame_rate, 60)
        self.assertFalse(config.auto_start_recording)
        self.assertEqual(config.upload_privacy_status, "unlisted")
        self.assertEqual(config.visual_detection_minimum_confidence, 0.8)

        with self.assertRaises(ConfigValueError):
            updated_config(config, "recorder.frame_rate", "0")

    def test_rejects_unknown_and_secret_like_keys(self) -> None:
        with self.assertRaises(ConfigValueError):
            config_value(AppConfig(), "upload.oauth_token")
        with self.assertRaises(ConfigValueError):
            updated_config(AppConfig(), "upload.oauth_token", "secret")

    def test_atomic_save_keeps_previous_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = save_app_config(paths=paths, config=AppConfig(frame_rate=30))
            original = config_path.read_bytes()
            save_app_config(paths=paths, config=AppConfig(frame_rate=60))

            previous = config_path.with_name("app.toml.previous")
            previous_bytes = previous.read_bytes()
            restored = load_app_config(user_data_dir=paths.root)

        self.assertEqual(previous_bytes, original)
        self.assertEqual(restored.config.frame_rate, 60)

    def test_non_overwriting_initialization_preserves_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            config_path = save_app_config(paths=paths, config=AppConfig(frame_rate=60))
            original = config_path.read_bytes()

            with self.assertRaises(AppConfigError):
                save_app_config(paths=paths, config=AppConfig(), overwrite=False)

            preserved = config_path.read_bytes()

        self.assertEqual(preserved, original)


if __name__ == "__main__":
    unittest.main()
