"""保存設定・保護・動画だけの削除・動画なしカード編集の製品入口を検証する。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.config import AppConfig, save_app_config
from master_duel_recorder_lite.data_protection import DataProtectionService
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class RetentionGuiTest(unittest.TestCase):
    def run_gui(self, scenario, setup=None):
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
        from master_duel_recorder_lite import pyside_gui

        with tempfile.TemporaryDirectory(prefix="mdrl-retention-gui-") as temporary:
            root = Path(temporary)
            fixture = RecorderApplicationService(user_data_dir=root / "runtime")
            if setup is not None:
                setup(fixture)
            fixture.close()
            def inspect(app):
                window = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible())
                def pump(predicate, timeout=10):
                    deadline = time.monotonic() + timeout
                    while not predicate():
                        if time.monotonic() >= deadline:
                            return False
                        app.processEvents()
                        window._poll_background_tasks()
                        time.sleep(.005)
                    app.processEvents()
                    return True
                try:
                    self.assertTrue(pump(lambda: not window.startup_maintenance_running))
                    self.assertTrue(window.service.gui_startup_ready)
                    scenario(window, window.service, app, pump)
                finally:
                    self.assertTrue(pump(lambda: not window.background_tasks))
                    window.background_executor.shutdown(wait=True, cancel_futures=True)
                    window.settings_baseline = None
                    window.history_editor.baseline = None
                    for review in tuple(window.review_windows):
                        panel = getattr(review, "card_information_panel", None)
                        if panel is not None:
                            panel.reload()
                        review.close()
                    window.close_after_background = False
                    window.close()
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    app.processEvents()
                return 0
            with patch.dict(os.environ, {"QT_QPA_PLATFORM": os.environ.get("MDRL_TEST_QPA", "offscreen")}), patch.object(
                QApplication, "exec", inspect
            ), patch.object(QMessageBox, "information"), patch.object(QMessageBox, "warning"), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard
            ):
                self.assertEqual(pyside_gui.main(["--user-data-dir", str(root / "runtime")]), 0)

    @staticmethod
    def recording(service, identifier="one"):
        path = service.paths.recordings / f"{identifier}.mkv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"isolated movie fixture")
        stamp = datetime.now(timezone.utc)
        history = RecordingHistoryRepository.from_runtime_paths(service.paths)
        history.register_starting(recording_id=identifier, output_path=path, container="mkv", source="manual", created_at=stamp)
        history.finalize(identifier, RecordingResult(RecordingState.COMPLETED, path, 0, stamp,
                         stamp + timedelta(seconds=2), path.stat().st_size, None, ()))
        return path

    @staticmethod
    def select_recording(window, recording_id):
        from PySide6.QtCore import Qt
        window.show_page("history")
        table = window.widgets["history_table"]
        for row in range(table.rowCount()):
            view = window.history_views_by_row_id[str(table.item(row, 0).data(Qt.ItemDataRole.UserRole))]
            if view.recording_id == recording_id:
                table.selectRow(row)
                return
        raise AssertionError("recording row missing")

    def test_default_settings_save_latest_values_without_immediate_deletion(self):
        def scenario(window, service, app, pump):
            self.recording(service)
            window.show_page("settings")
            self.assertEqual(window.widgets["settings_recording_storage_max_gib"].text(), "0")
            self.assertEqual(window.widgets["settings_recording_retention_days"].text(), "0")
            self.assertFalse(window.widgets["settings_delete_source_after_upload"].isChecked())
            window.widgets["settings_recording_storage_max_gib"].setText("25")
            window.widgets["settings_recording_retention_days"].setText("30")
            window.widgets["settings_delete_source_after_upload"].setChecked(True)
            self.assertTrue(window.save_settings())
            config = service.load_config().config
            self.assertEqual((config.recording_storage_max_gib, config.recording_retention_days, config.upload_delete_source_after_upload), (25, 30, True))
            self.assertTrue((service.paths.recordings / "one.mkv").exists())
        self.run_gui(scenario)

    def test_unentered_recording_can_be_protected_and_manual_warning_cancel_keeps_everything(self):
        from PySide6.QtWidgets import QMessageBox
        def scenario(window, service, app, pump):
            path = self.recording(service)
            window._refresh_history()
            self.select_recording(window, "one")
            self.assertIsNone(window._selected_history_view().duel_record)
            check = window.widgets["history_video_protection"]
            check.setChecked(True)
            self.assertTrue(service.get_recording_video_state("one").protected)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(
                QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel
            ) as warning:
                window._delete_selected_video()
                warning.assert_called_once()
            self.assertTrue(path.exists())
            self.assertIsNotNone(service.get_history("one"))
            self.assertFalse(window.video_deletion_running)
        self.run_gui(scenario)

    def test_protected_manual_video_delete_keeps_card_information_and_shows_reason(self):
        from PySide6.QtWidgets import QMessageBox
        def scenario(window, service, app, pump):
            path = self.recording(service)
            duel = service.save_duel_record("one", DuelRecordValues(status="confirmed", result="win"), expected_revision=0)
            card = service.save_card_identity(name="Test card")
            service.save_duel_card_information(duel.duel_id, card.card_id, "self", "used", is_opener=True)
            before = service.get_duel_card_review(duel.duel_id)
            service.set_recording_video_protection("one", True)
            window._refresh_history()
            self.select_recording(window, "one")
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(
                QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes
            ):
                window._delete_selected_video()
                self.assertTrue(pump(lambda: not window.video_deletion_running))
            self.assertFalse(path.exists())
            self.assertTrue(service.get_recording_video_state("one").protected)
            self.assertEqual(service.get_duel_card_review(duel.duel_id), before)
            self.assertIn("手動削除", window.widgets["history_video_status"].text())
            self.assertFalse(window.widgets["history_play"].isEnabled())
            self.assertTrue(window.widgets["history_cards"].isEnabled())
            window._show_selected_duel_cards()
            panel = window.review_windows[-1].card_information_panel
            self.assertEqual(panel.duel_id, duel.duel_id)
        self.run_gui(scenario)

    def test_manual_duel_has_card_editor_without_recording(self):
        def scenario(window, service, app, pump):
            duel = service.create_manual_duel_record(DuelRecordValues(), occurred_at=datetime.now(timezone.utc))
            window.show_page("history")
            window.widgets["history_table"].selectRow(0)
            self.assertFalse(window.widgets["history_play"].isEnabled())
            self.assertFalse(window.widgets["history_video_delete"].isEnabled())
            self.assertTrue(window.widgets["history_cards"].isEnabled())
            window._show_selected_duel_cards()
            self.assertEqual(window.review_windows[-1].card_information_panel.duel_id, duel.duel_id)
        self.run_gui(scenario)

    def test_restore_success_resets_settings_baseline_and_failure_preserves_edit(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        def setup(service):
            RecordingHistoryRepository.from_runtime_paths(service.paths)
            save_app_config(paths=service.paths, config=AppConfig(recording_storage_max_gib=12, recording_retention_days=7,
                                                                  upload_delete_source_after_upload=True))
            DataProtectionService(service.paths).create_backup("fixture", protected=True)
            save_app_config(paths=service.paths, config=AppConfig())
        def scenario(window, service, app, pump):
            backup = DataProtectionService(service.paths).list_backups()[0]
            window.show_page("settings")
            window.widgets["settings_recording_storage_max_gib"].setText("99")
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(backup.path), "")), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ):
                window.restore_data_backup()
            self.assertEqual(window.widgets["settings_recording_storage_max_gib"].text(), "12")
            self.assertEqual(window.widgets["settings_recording_retention_days"].text(), "7")
            self.assertTrue(window.widgets["settings_delete_source_after_upload"].isChecked())
            self.assertFalse(window._settings_dirty())
            window.widgets["settings_recording_storage_max_gib"].setText("123")
            baseline = window.settings_baseline
            with patch.object(QFileDialog, "getOpenFileName", return_value=(str(backup.path), "")), patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ), patch.object(service, "restore_data_backup", side_effect=RuntimeError("fixture restore failure")):
                window.restore_data_backup()
            self.assertEqual(window.widgets["settings_recording_storage_max_gib"].text(), "123")
            self.assertEqual(window.settings_baseline, baseline)
        self.run_gui(scenario, setup)


if __name__ == "__main__":
    unittest.main()
