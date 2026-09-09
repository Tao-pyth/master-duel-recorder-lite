import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.config import AppConfig, AppConfigError
from master_duel_recorder_lite.config_management import ConfigValueError, updated_config


class SettingsBatchTest(unittest.TestCase):
    def test_audio_changes_are_independent_of_key_order(self):
        for mode in ("device", "system"):
            for reverse in (False, True):
                with self.subTest(mode=mode, reverse=reverse), tempfile.TemporaryDirectory() as temporary:
                    service = RecorderApplicationService(user_data_dir=Path(temporary))
                    values = [("recorder.audio_mode", mode), ("recorder.audio_input", "audio=test")]
                    service.save_settings(dict(reversed(values) if reverse else values))
                    self.assertEqual(service.load_config().config.audio_mode, mode)
                    self.assertEqual(service.load_config().config.audio_input, "audio=test")
                    values = [("recorder.audio_input", ""), ("recorder.audio_mode", "none")]
                    service.save_settings(dict(reversed(values) if reverse else values))
                    self.assertEqual(service.load_config().config.audio_mode, "none")
                    self.assertEqual(service.load_config().config.audio_input, "")
        with self.assertRaises(ConfigValueError):
            updated_config(AppConfig(), "recorder.audio_mode", "device")

    def test_invalid_batches_and_save_failure_preserve_existing_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = RecorderApplicationService(user_data_dir=Path(temporary))
            service.save_settings({"recorder.frame_rate": "30"})
            service.save_settings({"recorder.frame_rate": "60"})
            config_path = service.load_config().config_path
            previous = config_path.with_name("app.toml.previous")
            before = (config_path.read_bytes(), previous.read_bytes())
            invalid = (
                {"recorder.frame_rate": "45", "recorder.audio_mode": "device"},
                {"recorder.frame_rate": "45", "unknown.key": "bad"},
                {"recorder.frame_rate": "not-a-number"},
            )
            for values in invalid:
                with self.subTest(values=values), self.assertRaises(ConfigValueError):
                    service.save_settings(values)
                self.assertEqual((config_path.read_bytes(), previous.read_bytes()), before)
            with patch("master_duel_recorder_lite.application.save_app_config", side_effect=OSError("denied")):
                with self.assertRaises(OSError):
                    service.save_settings({"recorder.frame_rate": "45"})
            self.assertEqual((config_path.read_bytes(), previous.read_bytes()), before)

    def test_final_replace_failure_keeps_current_config_and_valid_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = RecorderApplicationService(user_data_dir=Path(temporary))
            service.save_settings({"recorder.frame_rate": "30"})
            service.save_settings({"recorder.frame_rate": "60"})
            config_path = service.load_config().config_path
            original = config_path.read_bytes()
            replace_file = os.replace

            def fail_current(source, destination):
                if destination == config_path:
                    raise PermissionError("test: current config is locked")
                replace_file(source, destination)

            with patch("master_duel_recorder_lite.config.os.replace", side_effect=fail_current):
                with self.assertRaises(AppConfigError):
                    service.save_settings({"recorder.frame_rate": "45"})
            self.assertEqual(config_path.read_bytes(), original)
            self.assertEqual(config_path.with_name("app.toml.previous").read_bytes(), original)
            self.assertEqual(service.load_config().config.frame_rate, 60)


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class AudioSelectionWidgetTest(unittest.TestCase):
    def test_refresh_preserves_selection_and_dirty_state_through_save(self):
        from PySide6.QtWidgets import QApplication, QMainWindow
        from master_duel_recorder_lite import pyside_gui

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = RecorderApplicationService(project_root=root, user_data_dir=root / "data")
            service.save_settings({"recorder.audio_mode": "device", "recorder.audio_input": "audio=saved"})

            def inspect(_app):
                app = QApplication.instance()
                window = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible())
                try:
                    window.show_page("settings")
                    audio = window.widgets["settings_audio_input"]
                    status = window.widgets["settings_audio_status"]

                    def refresh(identifiers):
                        result = SimpleNamespace(inputs=tuple(SimpleNamespace(identifier=i) for i in identifiers))
                        with patch.object(window.service, "list_audio_inputs", return_value=result):
                            window.widgets["settings_audio_refresh"].click()

                    for identifiers in (("audio=other", "audio=saved", "audio=saved"), (), ("audio=saved",)):
                        refresh(identifiers)
                        self.assertEqual(audio.currentText(), "audio=saved")
                        self.assertFalse(window._settings_dirty())
                        self.assertEqual(audio.count(), len(set(audio.itemText(i) for i in range(audio.count()))))
                        self.assertEqual("未検出" in status.text(), not identifiers)
                    refresh(("audio=saved", "audio=unsaved"))
                    audio.setCurrentText("audio=unsaved")
                    refresh(())
                    self.assertEqual(audio.currentText(), "audio=unsaved")
                    self.assertTrue(window._settings_dirty())
                    self.assertEqual(service.load_config().config.audio_input, "audio=saved")
                    with patch.object(window.service, "list_audio_inputs", side_effect=OSError("offline")):
                        window.widgets["settings_audio_refresh"].click()
                    self.assertEqual(audio.currentText(), "audio=unsaved")
                    self.assertTrue(window._settings_dirty())
                    self.assertIn("offline", status.text())
                    self.assertTrue(window.save_settings())
                    window.load_settings()
                    self.assertEqual(audio.currentText(), "audio=unsaved")
                    self.assertFalse(window._settings_dirty())
                    self.assertEqual(service.load_config().config.audio_input, "audio=unsaved")

                    window.widgets["settings_audio_mode"].setCurrentIndex(3)
                    audio.setCurrentIndex(0)
                    self.assertTrue(window.save_settings())
                    window.widgets["settings_audio_mode"].setCurrentIndex(2)
                    audio.setCurrentText("audio=unsaved")
                    self.assertTrue(window.save_settings())
                    self.assertEqual(service.load_config().config.audio_mode, "device")
                finally:
                    window.settings_baseline = None
                    window.close()
                return 0

            with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}), patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(root / "data")]), 0)
