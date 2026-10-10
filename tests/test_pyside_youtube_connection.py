"""隔離した資格情報で、GUIからの再連携と失敗後の復旧操作を検証する。"""

import concurrent.futures
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.youtube_oauth import MemoryCredentialStore, YouTubeCredentials


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class YouTubeConnectionGuiTest(unittest.TestCase):
    def run_gui(self, scenario):
        from PySide6.QtWidgets import QApplication, QMainWindow
        from master_duel_recorder_lite import pyside_gui

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = MemoryCredentialStore()
            store.write(YouTubeCredentials("fake-client", "fake-secret", "old-refresh"))
            service = RecorderApplicationService(
                project_root=root, user_data_dir=root / "data", youtube_credential_store=store,
                youtube_oauth_environ={"MDRL_YOUTUBE_OAUTH_CLIENT_ID": "fake-client"},
            )

            def inspect(_app):
                app = QApplication.instance()
                window = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible())
                window.record_state_timer.stop()
                window.show_page("settings")
                try:
                    scenario(window, service, store, app)
                finally:
                    window.youtube_connection_running = False
                    window.history_editor.baseline = None
                    window.settings_baseline = None
                    window.close()
                    window.deleteLater()
                    app.processEvents()
                return 0

            with patch.dict(os.environ, {"QT_QPA_PLATFORM": os.environ.get("MDRL_TEST_QPA", "offscreen")}), patch.object(
                pyside_gui, "RecorderApplicationService", return_value=service
            ), patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(root / "data")]), 0)

    def assert_actions_restored(self, window):
        self.assertFalse(window.youtube_connection_running)
        for key in ("settings_youtube_connect", "settings_youtube_disconnect", "settings_youtube_refresh"):
            self.assertTrue(window.widgets[key].isEnabled(), key)

    def test_real_service_runs_in_background_and_competing_actions_are_blocked(self):
        def scenario(window, service, store, app):
            entered = threading.Event()
            release = threading.Event()
            thread_ids = []
            renewed = YouTubeCredentials("fake-client", "fake-secret", "new-refresh")

            def authorize(*args, **kwargs):
                thread_ids.append(threading.get_ident())
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test authorization timed out")
                return SimpleNamespace(credentials=renewed)

            with patch("master_duel_recorder_lite.application.load_distributed_oauth_client"), patch(
                "master_duel_recorder_lite.application.authorize_with_loopback", side_effect=authorize
            ) as auth, patch.object(service, "disconnect_youtube") as disconnect, patch.object(
                service, "upload_history_to_youtube"
            ) as upload:
                try:
                    window.widgets["settings_youtube_connect"].click()
                    self.assertTrue(entered.wait(2))
                    self.assertTrue(window.youtube_connection_running)
                    self.assertNotEqual(thread_ids[0], threading.get_ident())
                    for key in ("settings_youtube_connect", "settings_youtube_disconnect", "settings_youtube_refresh"):
                        self.assertFalse(window.widgets[key].isEnabled(), key)
                    window.connect_youtube()
                    window.disconnect_youtube()
                    window._start_youtube_upload(SimpleNamespace())
                    self.assertEqual(auth.call_count, 1)
                    disconnect.assert_not_called()
                    upload.assert_not_called()
                    waiting = window.widgets["settings_youtube_status"].text()
                    window.refresh_youtube_status()
                    window._refresh_youtube()
                    window.show_page("history")
                    window.show_page("settings")
                    self.assertEqual(window.widgets["settings_youtube_status"].text(), waiting)
                    self.assertFalse(window.close())
                    self.assertTrue(window.isVisible())
                    self.assertIn("約3分", window.widgets["settings_youtube_status"].text())
                finally:
                    release.set()
                    for _label, future, _callback in window.background_tasks:
                        future.result(timeout=2)
                    window._poll_background_tasks()
                self.assertEqual(store.read(), renewed)
                self.assert_actions_restored(window)
                self.assertIn("更新しました", window.widgets["settings_youtube_status"].text())
                self.assertIn(renewed.scope, window.widgets["settings_youtube_scope"].text())

        self.run_gui(scenario)

    def test_authorization_failure_preserves_old_credentials_and_restores_buttons(self):
        def scenario(window, service, store, app):
            original = store.read()
            with patch("master_duel_recorder_lite.application.load_distributed_oauth_client"), patch(
                "master_duel_recorder_lite.application.authorize_with_loopback",
                side_effect=RuntimeError("ブラウザ認証が拒否されました"),
            ):
                window.widgets["settings_youtube_connect"].click()
                for _label, future, _callback in window.background_tasks:
                    with self.assertRaises(Exception):
                        future.result(timeout=2)
                window._poll_background_tasks()
            self.assertEqual(store.read(), original)
            self.assert_actions_restored(window)
            self.assertIn("連携に失敗", window.widgets["settings_youtube_status"].text())
            self.assertIn("保持", window.widgets["settings_youtube_status"].text())

        self.run_gui(scenario)

    def test_submission_failure_restores_state_without_changing_credentials(self):
        def scenario(window, service, store, app):
            original = store.read()
            with patch.object(window.background_executor, "submit", side_effect=RuntimeError("worker stopped")):
                window.widgets["settings_youtube_connect"].click()
            self.assertEqual(store.read(), original)
            self.assert_actions_restored(window)
            self.assertEqual(window.background_tasks, [])
            self.assertIn("worker stopped", window.widgets["settings_youtube_status"].text())

        self.run_gui(scenario)

    def test_connection_result_error_does_not_report_success(self):
        def scenario(window, service, store, app):
            window.youtube_connection_running = True
            window._youtube_connection_completed(SimpleNamespace(
                state="error", message="資格情報の保存結果を読み取れません", can_connect=True,
            ))
            self.assertFalse(window.youtube_connection_running)
            self.assertTrue(window.widgets["settings_youtube_connect"].isEnabled())
            self.assertIn("確認できません", window.widgets["settings_youtube_status"].text())
            self.assertNotIn("更新しました", window.widgets["settings_youtube_status"].text())

        self.run_gui(scenario)

    def test_upload_failure_keeps_reauthorization_guidance_visible_in_settings(self):
        def scenario(window, service, store, app):
            window.youtube_upload_running = True
            window._set_youtube_background_status(True, "投稿中")
            with patch.object(service, "connect_youtube") as connect, patch.object(service, "disconnect_youtube") as disconnect:
                window.connect_youtube()
                window.disconnect_youtube()
                connect.assert_not_called()
                disconnect.assert_not_called()
            future = concurrent.futures.Future()
            future.set_exception(RuntimeError("設定 → YouTube →「連携する」で再認証してください。"))
            window.background_tasks.append(("YouTube投稿", future, window._youtube_upload_completed))
            window._poll_background_tasks()
            self.assertFalse(window.youtube_upload_running)
            self.assert_actions_restored(window)
            self.assertIn("再認証", window.widgets["settings_youtube_status"].text())
            self.assertIn("失敗", window.widgets["settings_youtube_status"].text())
            window._youtube_upload_completed(SimpleNamespace(
                upload=SimpleNamespace(state="failed", watch_url=None), message="再認証が必要です"
            ))
            self.assertIn("失敗", window.widgets["settings_youtube_status"].text())
            self.assertIn("再認証", window.widgets["settings_youtube_status"].text())

        self.run_gui(scenario)


if __name__ == "__main__":
    unittest.main()
