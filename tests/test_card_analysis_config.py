from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from master_duel_recorder_lite.config import (
    AppConfig,
    AppConfigError,
    load_app_config,
    save_app_config,
)
from master_duel_recorder_lite.config_management import updated_config_values
from master_duel_recorder_lite.runtime_paths import default_runtime_paths


class CardAnalysisConfigTests(unittest.TestCase):
    def test_old_file_is_read_without_modification_or_automatic_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = default_runtime_paths(user_data_dir=Path(temporary))
            paths.config.mkdir()
            path = paths.config / "app.toml"
            before = b'[recorder]\nframe_rate = 60\n[interaction]\ntray_enabled = false\n'
            path.write_bytes(before)
            config = load_app_config(user_data_dir=paths.root).config
            self.assertFalse(config.card_analysis_enabled)
            self.assertEqual(config.card_analysis_fps, 2.0)
            self.assertEqual(config.frame_rate, 60)
            self.assertFalse(config.tray_enabled)
            self.assertEqual(path.read_bytes(), before)

    def test_batch_setting_survives_save_reload_and_keeps_other_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = default_runtime_paths(user_data_dir=Path(temporary))
            original = replace(AppConfig(), frame_rate=60, tray_enabled=False)
            edited = updated_config_values(
                original,
                {"card_analysis.enabled": "true", "card_analysis.fps": "1.5"},
            )
            save_app_config(paths=paths, config=edited)
            loaded = load_app_config(user_data_dir=paths.root).config
            self.assertEqual(loaded, edited)
            self.assertEqual(loaded.frame_rate, original.frame_rate)
            self.assertEqual(loaded.tray_enabled, original.tray_enabled)

    def test_invalid_analysis_options_never_replace_saved_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = default_runtime_paths(user_data_dir=Path(temporary))
            save_app_config(paths=paths, config=AppConfig())
            path = paths.config / "app.toml"
            before = path.read_bytes()
            cases = [
                {"card_analysis_enabled": "true"},
                *({"card_analysis_fps": value} for value in (True, 0.0, 0.2, 2.1, float("nan"), float("inf"))),
            ]
            for values in cases:
                with self.subTest(values=values):
                    with self.assertRaises((ValueError, AppConfigError)):
                        save_app_config(paths=paths, config=replace(AppConfig(), **values))
                    self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
