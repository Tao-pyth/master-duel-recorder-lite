"""実Qtで、起動・投稿後処理の待機中にも終了操作と表示を維持します。"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import ApplicationOperationError, RecorderApplicationService
from master_duel_recorder_lite.config import AppConfig, save_app_config
from master_duel_recorder_lite.runtime_paths import default_runtime_paths
from master_duel_recorder_lite.startup_maintenance import StartupMaintenanceResult
from master_duel_recorder_lite.youtube_oauth import MemoryCredentialStore


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class StartupGuiTest(unittest.TestCase):
    def run_gui(self, scenario, *, slow_startup=False, startup_error=False, analysis_enabled=False):
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
        from master_duel_recorder_lite import pyside_gui

        entered, release = threading.Event(), threading.Event()
        if not slow_startup:
            release.set()
        calls = []

        def maintenance(paths, *, progress):
            calls.append(threading.get_ident())
            progress("隔離した保存状態を確認しています")
            entered.set()
            if not release.wait(5):
                raise RuntimeError("fixture startup timed out")
            if startup_error:
                raise RuntimeError("fixture startup failure")
            return StartupMaintenanceResult(True, "保存チェック完了")

        with tempfile.TemporaryDirectory(prefix="mdrl-startup-gui-") as temporary:
            root = Path(temporary)
            paths = default_runtime_paths(user_data_dir=root / "runtime")
            paths.recordings.mkdir(parents=True)
            original = paths.recordings / "fixture.mkv"
            original.write_bytes(b"isolated-original-retained")
            before = hashlib.sha256(original.read_bytes()).hexdigest()
            save_app_config(paths=paths, config=replace(AppConfig(), card_analysis_enabled=analysis_enabled))
            with patch.object(RecorderApplicationService, "_ensure_card_manager") as manager:
                service = RecorderApplicationService(
                    project_root=root, user_data_dir=paths.root,
                    youtube_credential_store=MemoryCredentialStore(), require_gui_startup=True,
                )
                self.assertEqual(manager.call_count, 0)

                def inspect(app):
                    window = next(widget for widget in app.topLevelWidgets()
                                  if isinstance(widget, QMainWindow) and widget.isVisible()
                                  and getattr(widget, "service", None) is service)

                    def pump(predicate, *, timeout=5):
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
                        self.assertTrue(entered.wait(2))
                        scenario(window, service, app, pump, entered, release, calls, manager)
                    finally:
                        release.set()
                        if not pump(lambda: not window.background_tasks):
                            service.close()
                        window.background_executor.shutdown(wait=True, cancel_futures=True)
                        window._poll_background_tasks()
                        window.close_after_background = False
                        window.history_editor.baseline = None
                        window.settings_baseline = None
                        window.close()
                        window.deleteLater()
                        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                        app.processEvents()
                    return 0

                with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}), patch.object(
                    pyside_gui, "RecorderApplicationService", return_value=service
                ), patch("master_duel_recorder_lite.startup_maintenance.run_startup_maintenance", side_effect=maintenance), patch.object(
                    QApplication, "exec", inspect
                ), patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "information"), patch.object(
                    QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard
                ):
                    self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(paths.root)]), 0)
                self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), before)

    def test_startup_disables_recording_and_defers_analysis_until_one_completed_check(self):
        def scenario(window, service, app, pump, entered, release, calls, manager):
            self.assertTrue(window.startup_maintenance_running)
            self.assertFalse(window.centralWidget().isEnabled())
            self.assertFalse(window.widgets["record_start"].isEnabled())
            self.assertFalse(service.gui_startup_ready)
            manager.assert_not_called()
            with patch("master_duel_recorder_lite.application.prepare_recording") as recording:
                with self.assertRaises(ApplicationOperationError):
                    service.start_recording()
                recording.assert_not_called()
            release.set()
            self.assertTrue(pump(lambda: not window.startup_maintenance_running))
            self.assertTrue(service.gui_startup_ready)
            self.assertTrue(window.centralWidget().isEnabled())
            self.assertEqual(manager.call_count, 1)
            self.assertNotEqual(calls[0], threading.get_ident())
            self.assertIs(service.run_gui_startup_maintenance(), service._gui_startup_result)
            self.assertEqual(len(calls), 1)
            self.assertEqual(manager.call_count, 1)

        self.run_gui(scenario, slow_startup=True, analysis_enabled=True)

    def test_close_during_startup_keeps_qt_events_running_then_closes(self):
        from PySide6.QtCore import QTimer

        def scenario(window, service, app, pump, entered, release, calls, manager):
            heartbeat = []
            timer = QTimer(window)
            timer.setInterval(5)
            timer.timeout.connect(lambda: heartbeat.append(True))
            timer.start()
            self.assertFalse(window.close())
            self.assertTrue(window.isVisible())
            self.assertTrue(window.close_after_background)
            self.assertTrue(pump(lambda: len(heartbeat) >= 2))
            self.assertTrue(window.startup_maintenance_running)
            release.set()
            self.assertTrue(pump(lambda: not window.isVisible()))
            self.assertFalse(window.startup_maintenance_running)
            self.assertTrue(service._closed)

        self.run_gui(scenario, slow_startup=True)

    def test_startup_failure_still_terminates_pending_close(self):
        def scenario(window, service, app, pump, entered, release, calls, manager):
            self.assertFalse(window.close())
            release.set()
            self.assertTrue(pump(lambda: not window.isVisible()))
            self.assertFalse(window.startup_maintenance_running)
            self.assertFalse(service.gui_startup_ready)
            self.assertTrue(service._closed)
            self.assertEqual(window.widgets["nav_health_status"].text(), "保存処理を要確認")
            self.assertIn("fixture startup failure", window.widgets["nav_health_status"].toolTip())

        self.run_gui(scenario, slow_startup=True, startup_error=True)

    def upload_close_scenario(self, *, failure=False):
        from PySide6.QtCore import QTimer

        def scenario(window, service, app, pump, entered, release, calls, manager):
            self.assertTrue(pump(lambda: not window.startup_maintenance_running))
            upload_started, upload_release = threading.Event(), threading.Event()

            def upload(**_arguments):
                upload_started.set()
                if not upload_release.wait(5):
                    raise RuntimeError("fixture upload timed out")
                if failure:
                    raise RuntimeError("fixture upload failure")
                return SimpleNamespace(upload=SimpleNamespace(state="completed", watch_url="https://youtu.be/fixture"),
                                       message="公開範囲の注意。元動画は使用中のため再試行待ちです")

            heartbeat = []
            timer = QTimer(window)
            timer.setInterval(5)
            timer.timeout.connect(lambda: heartbeat.append(True))
            timer.start()
            dialog = SimpleNamespace(recording_id="fixture", title="人工投稿", description="", tags=(), privacy="private")
            with patch.object(service, "upload_history_to_youtube", side_effect=upload):
                try:
                    window._start_youtube_upload(dialog)
                    self.assertTrue(upload_started.wait(2))
                    self.assertFalse(window.close())
                    self.assertTrue(window.youtube_upload_running)
                    self.assertFalse(window.centralWidget().isEnabled())
                    self.assertTrue(pump(lambda: len(heartbeat) >= 2))
                    self.assertTrue(window.isVisible())
                    upload_release.set()
                    self.assertTrue(pump(lambda: not window.isVisible()))
                    self.assertFalse(window.youtube_upload_running)
                    self.assertTrue(service._closed)
                    if failure:
                        self.assertIn("fixture upload failure", window.widgets["settings_youtube_status"].text())
                    else:
                        self.assertIn("https://youtu.be/fixture", window.widgets["settings_youtube_status"].text())
                        self.assertIn("再試行待ち", window.widgets["settings_youtube_status"].text())
                finally:
                    upload_release.set()

        return scenario

    def test_upload_and_cleanup_terminal_result_closes_after_responsive_wait(self):
        self.run_gui(self.upload_close_scenario())

    def test_upload_failure_also_closes_after_responsive_wait(self):
        self.run_gui(self.upload_close_scenario(failure=True))


if __name__ == "__main__":
    unittest.main()
