"""隔離した実GUI/CLIから、元動画なし投稿・終了待機・保護警告を検証します。"""
from __future__ import annotations

import base64
from contextlib import closing, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gui_test_helpers import close_gui_test_window, wait_for_gui_startup, wait_for_gui_tasks

from master_duel_recorder_lite import __main__ as cli
from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.config import AppConfig, save_app_config
from master_duel_recorder_lite.duel_card_repository import DuelCardRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.duel_timeline import DuelTimelineRepository
from master_duel_recorder_lite.ffmpeg import CommandResult, FfmpegVersion
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_retention import RecordingDeletionService, STAGING_DIRECTORY
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_export import UploadExporter
from master_duel_recorder_lite.upload_media import UploadMediaValidator
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_queue import UploadQueueState, UploadQueueStore
from master_duel_recorder_lite.video_lifecycle import VideoLifecycleRepository
from master_duel_recorder_lite.youtube_client import FakeYouTubeClient
from master_duel_recorder_lite.youtube_oauth import MemoryCredentialStore, YouTubeCredentials
from master_duel_recorder_lite.youtube_uploads import YouTubeUploadRepository, YouTubeUploadState


# 人工の黒色16x16、1秒、映像のみのH.264 MP4。ゲーム映像やユーザーデータは含みません。
ARTIFICIAL_MP4 = base64.b64decode(
    "AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAMVbW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAA+gAAQAAAQAAAAAA"
    "AAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAj90cmFr"
    "AAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAA+gAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAA"
    "AABAAAAAABAAAAAQAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAPoAAAAAAABAAAAAAG3bWRpYQAAACBtZGhkAAAAAAAAAAAAAAAA"
    "AABAAAAAQABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAABYm1pbmYAAAAUdm1oZAAAAAEA"
    "AAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAASJzdGJsAAAAvnN0c2QAAAAAAAAAAQAAAK5hdmMxAAAA"
    "AAAAAAEAAAAAAAAAAAAAAAAAAAAAABAAEABIAAAASAAAAAAAAAABFUxhdmM2MC4zMC4xMDEgbGlieDI2NAAAAAAAAAAAAAAAGP//AAAA"
    "NGF2Y0MBZAAK/+EAF2dkAAqs2V7ARAAAAwAEAAADAAg8SJZYAQAGaOvjyyLA/fj4AAAAABBwYXNwAAAAAQAAAAEAAAAUYnRydAAA"
    "AAAAABW4AAAVuAAAABhzdHRzAAAAAAAAAAEAAAABAABAAAAAABxzdHNjAAAAAAAAAAEAAAABAAAAAQAAAAEAAAAUc3RzegAAAAAAAAK3"
    "AAAAAQAAABRzdGNvAAAAAAAAAAEAAANFAAAAYnVkdGEAAABabWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAA"
    "AAAtaWxzdAAAACWpdG9vAAAAHWRhdGEAAAABAAAAAExhdmY2MC4xNS4xMDAAAAAIZnJlZQAAAr9tZGF0AAACnwYF//+b3EXpvebZ"
    "SLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NCAtIEguMjY0L01QRUctNCBBVkMgY29kZWMgLSBDb3B5bGVmdCAyMDAzLTIwMjMgLSBo"
    "dHRwOi8vd3d3LnZpZGVvbGFuLm9yZy94MjY0Lmh0bWwgLSBvcHRpb25zOiBjYWJhYz0xIHJlZj0zIGRlYmxvY2s9MTowOjAg"
    "YW5hbHlzZT0weDM6MHgxMTMgbWU9aGV4IHN1Ym1lPTcgcHN5PTEgcHN5X3JkPTEuMDA6MC4wMCBtaXhlZF9yZWY9MSBtZV9y"
    "YW5nZT0xNiBjaHJvbWFfbWU9MSB0cmVsbGlzPTEgOHg4ZGN0PTEgY3FtPTAgZGVhZHpvbmU9MjEsMTEgZmFzdF9wc2tpcD0x"
    "IGNocm9tYV9xcF9vZmZzZXQ9LTIgdGhyZWFkcz0xIGxvb2thaGVhZF90aHJlYWRzPTEgc2xpY2VkX3RocmVhZHM9MCBucj0w"
    "IGRlY2ltYXRlPTEgaW50ZXJsYWNlZD0wIGJsdXJheV9jb21wYXQ9MCBjb25zdHJhaW5lZF9pbnRyYT0wIGJmcmFtZXM9MyBi"
    "X3B5cmFtaWQ9MiBiX2FkYXB0PTEgYl9iaWFzPTAgZGlyZWN0PTEgd2VpZ2h0Yj0xIG9wZW5fZ29wPTAgd2VpZ2h0cD0yIGtl"
    "eWludD0yNTAga2V5aW50X21pbj0xIHNjZW5lY3V0PTQwIGludHJhX3JlZnJlc2g9MCByY19sb29rYWhlYWQ9NDAgcmM9Y3Jm"
    "IG1idHJlZT0xIGNyZj0yMy4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02OSBxcHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6"
    "MS4wMACAAAAAEGWIhAAV//73ye/Apuvb34E="
)
HAS_QT = importlib.util.find_spec("PySide6") is not None


class ProvenYouTubeClient(FakeYouTubeClient):
    """送信先だけを置換し、実際に読んだ人工MP4のSHA証拠を返します。"""

    def upload_video(self, **arguments):
        result = super().upload_video(**arguments)
        path = arguments["media_path"].resolve()
        return replace(result, media_path=path, media_size_bytes=path.stat().st_size,
                       media_sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class RetentionProductFlowTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mdrl-retention-product-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = default_runtime_paths(user_data_dir=self.root / "runtime")
        ensure_runtime_dirs(self.paths)
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        ffmpeg = self.root / "fixture-ffmpeg.exe"
        ffmpeg.write_bytes(b"command runner is isolated by fixture")
        discovery = SimpleNamespace(found=True, executable=ffmpeg, source="fixture", attempts=(),
                                    version=FfmpegVersion("fixture", (8, 0, 0), 60))
        self.commands = []

        def copy_runner(command, _timeout):
            self.commands.append(tuple(command))
            source = Path(command[command.index("-i") + 1])
            destination = Path(command[-1])
            self.assertTrue(source.is_relative_to(self.paths.recordings))
            self.assertTrue(destination.is_relative_to(self.paths.exports))
            destination.write_bytes(source.read_bytes())
            return CommandResult(0, "", "")

        def probe_runner(command, _timeout):
            media = Path(command[-1])
            self.assertTrue(media.is_relative_to(self.paths.root))
            self.assertEqual(media.read_bytes(), ARTIFICIAL_MP4)
            return CommandResult(0, json.dumps({"streams": [{"codec_type": "video"}],
                                                "format": {"format_name": "mov,mp4", "duration": "1"}}), "")

        # 外部プロセス境界だけを固定します。準備・validation・manifestのクラスは実装です。
        for module in ("application", "__main__"):
            prefix = f"master_duel_recorder_lite.{module}"
            for attribute, replacement in (
                ("discover_ffmpeg", lambda _configured, value=discovery: value),
                ("UploadExporter", partial(UploadExporter, runner=copy_runner)),
                ("UploadMediaValidator", partial(UploadMediaValidator, runner=probe_runner)),
            ):
                patcher = patch(f"{prefix}.{attribute}", replacement)
                patcher.start()
                self.addCleanup(patcher.stop)
        self.config = AppConfig(
            ffmpeg_path=str(ffmpeg), upload_delete_source_after_upload=True,
            setup_wizard_completed=True, tray_enabled=False, hotkeys_enabled=False,
            windows_notifications_enabled=False, auto_start_recording=False,
            auto_stop_recording=False, visual_detection_enabled=False,
        )
        save_app_config(paths=self.paths, config=self.config)
        self.store = MemoryCredentialStore()
        self.store.write(YouTubeCredentials("fixture-client", "fixture-secret", "fixture-refresh"))
        self.client = ProvenYouTubeClient()
        self.service = RecorderApplicationService(
            project_root=self.root, user_data_dir=self.paths.root,
            youtube_credential_store=self.store, youtube_client=self.client,
            youtube_oauth_environ={"MDRL_YOUTUBE_OAUTH_CLIENT_ID": "fixture-client"},
        )
        self.addCleanup(self.service.close)
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.lifecycle = VideoLifecycleRepository.from_runtime_paths(self.paths)
        self.uploads = YouTubeUploadRepository.from_runtime_paths(self.paths)
        self.queue = UploadQueueStore(self.paths)
        self.source = self.paths.recordings / "rec.mp4"
        self.source.write_bytes(ARTIFICIAL_MP4)
        stamp = datetime.now(timezone.utc)
        self.history.register_starting(recording_id="rec", output_path=self.source, container="mp4",
                                       source="manual", created_at=stamp)
        self.history.mark_recording("rec", started_at=stamp)
        self.history.finalize("rec", RecordingResult(
            state=RecordingState.COMPLETED, output_path=self.source, returncode=0,
            started_at=stamp, ended_at=stamp + timedelta(seconds=1), size_bytes=len(ARTIFICIAL_MP4),
            error=None, diagnostics=(),
        ))
        duel = DuelRecordRepository.from_runtime_paths(self.paths).save(
            "rec", DuelRecordValues(status="confirmed", result="win", tags=("保持",)), expected_revision=0,
        )
        self.duel_id = duel.duel_id
        cards = DuelCardRepository.from_runtime_paths(self.paths)
        used = cards.save_identity(name="人工使用カード")
        not_used = cards.save_identity(name="人工非使用カード")
        cards.save_information(duel.duel_id, used.card_id, "self", "used", is_opener=True)
        cards.save_information(duel.duel_id, not_used.card_id, "opponent", "not_used")
        self.card_review = cards.get_review(duel.duel_id)
        self.marker = DuelTimelineRepository.from_runtime_paths(self.paths).add(
            "rec", elapsed_ms=200, event_type="marker", label="動画マーカー",
        )

    def prepare(self):
        preparation = self.service._upload_preparation_service()
        item = preparation.enqueue(recording_id="rec", metadata=UploadMetadata("人工投稿"))
        results = preparation.process(item.queue_id)
        self.assertTrue(results[0].succeeded, results[0].message)
        prepared = self.queue.get(item.queue_id)
        self.assertEqual(prepared.state, UploadQueueState.COMPLETED)
        self.assertTrue(prepared.manifest_path)
        self.export = self.paths.root / prepared.export_path
        self.assertEqual(self.export.read_bytes(), ARTIFICIAL_MP4)
        self.assertEqual(len(self.commands), 1)
        return prepared

    def database_rows(self):
        with closing(sqlite3.connect(self.history.database_path)) as connection:
            connection.execute("BEGIN")
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            return {name: connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall() for (name,) in tables}

    def assert_saved_upload(self, *, identifier=None):
        saved = self.uploads.get(identifier) if identifier else self.uploads.completed_for_recording("rec")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(saved.watch_url, "https://youtu.be/fake-video-id")
        self.assertEqual(len(self.client.uploaded), 1)
        self.assertEqual(self.client.uploaded[0][1], self.export.resolve())
        self.assertEqual(self.export.read_bytes(), ARTIFICIAL_MP4)
        self.assertEqual(DuelCardRepository.from_runtime_paths(self.paths).get_review(self.duel_id), self.card_review)
        self.assertIsNotNone(self.history.get("rec"))
        return saved

    def run_cli(self, arguments):
        output = io.StringIO()
        with patch.object(cli, "WindowsCredentialStore", return_value=self.store), patch.object(
            cli, "HttpYouTubeClient", return_value=self.client,
        ), redirect_stdout(output):
            code = cli.main(["--project-root", str(self.root), "--user-data-dir", str(self.paths.root), *arguments])
        return code, output.getvalue()

    def run_gui(self, scenario):
        from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
        from master_duel_recorder_lite import pyside_gui

        def inspect(app):
            window = next(widget for widget in app.topLevelWidgets()
                          if isinstance(widget, QMainWindow) and widget.isVisible()
                          and getattr(widget, "service", None) is self.service)
            try:
                wait_for_gui_startup(app, window)
                window.show_page("history")
                table = window.widgets["history_table"]
                self.assertEqual(table.rowCount(), 1)
                table.selectRow(0)
                app.processEvents()
                wait_for_gui_tasks(app, window)
                self.assertEqual(window._selected_history_view().recording_id, "rec")
                scenario(window, app)
            finally:
                self.release.set()
                close_gui_test_window(app, window)
            return 0

        with patch.dict(os.environ, {"QT_QPA_PLATFORM": os.environ.get("MDRL_TEST_QPA", "offscreen")}), patch.object(
            pyside_gui, "RecorderApplicationService", return_value=self.service,
        ), patch.object(QApplication, "exec", inspect), patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes,
        ), patch.object(QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel):
            self.assertEqual(pyside_gui.main(["--project-root", str(self.root), "--user-data-dir", str(self.paths.root)]), 0)

    def pump(self, app, window, predicate, *, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            window._poll_background_tasks()
            if time.monotonic() > deadline:
                self.fail(f"GUIの待機が終わりません: tasks={[(label, future.done()) for label, future, _ in window.background_tasks]}")
            time.sleep(.005)
        app.processEvents()

    def test_cli_first_upload_without_original_reuses_real_completed_prepare_and_keeps_url(self):
        prepared = self.prepare()
        self.source.unlink()
        code, output = self.run_cli(["youtube", "upload", "rec", "--title", "人工初回投稿"])
        self.assertEqual(code, cli.EXIT_SUCCESS, output)
        saved = self.assert_saved_upload()
        self.assertEqual(saved.prepare_queue_id, prepared.queue_id)
        self.assertIn(saved.watch_url, output)
        self.assertEqual(len(self.commands), 1)
        self.assertEqual(self.lifecycle.pending_cleanup_requests(), ())
        self.assertEqual(self.lifecycle.pending_intents(), ())

    def test_cli_existing_waiting_upload_without_original_keeps_its_id_and_url(self):
        self.prepare()
        waiting = self.uploads.create(recording_id="rec", metadata=UploadMetadata("既存待機投稿"))
        self.source.unlink()
        code, output = self.run_cli(["youtube", "upload", "run"])
        self.assertEqual(code, cli.EXIT_SUCCESS, output)
        self.assert_saved_upload(identifier=waiting.upload_id)
        self.assertEqual(len(self.uploads.list()), 1)
        self.assertIn(waiting.upload_id, output)
        self.assertIn("https://youtu.be/fake-video-id", output)
        self.assertEqual(len(self.commands), 1)

    def test_cli_original_is_required_when_no_completed_mp4_exists(self):
        self.source.unlink()
        code, output = self.run_cli(["youtube", "upload", "rec", "--title", "準備不可"])
        self.assertEqual(code, cli.EXIT_ATTENTION, output)
        self.assertIn("録画ファイルが存在しないか空です", output)
        self.assertEqual(self.client.uploaded, [])
        self.assertEqual(self.commands, [])
        self.assertEqual(self.queue.list(), ())
        self.assertEqual(self.uploads.list()[0].state, YouTubeUploadState.FAILED)
        self.assertEqual(self.lifecycle.pending_intents(), ())
        self.assertEqual(DuelCardRepository.from_runtime_paths(self.paths).get_review(self.duel_id), self.card_review)

    @unittest.skipUnless(HAS_QT, "PySide6 is optional")
    def test_gui_first_upload_action_without_original_reuses_prepared_mp4_and_displays_url(self):
        self.prepare()
        self.source.unlink()

        def scenario(window, app):
            action = window.history_menu_actions["history_youtube"]
            self.assertTrue(action.isEnabled())
            action.trigger()
            self.assertTrue(window.youtube_upload_running)
            wait_for_gui_tasks(app, window)
            self.assertFalse(window.youtube_upload_running)
            saved = self.assert_saved_upload()
            self.assertIn(saved.watch_url, window.widgets["settings_youtube_status"].text())
            self.assertEqual(len(self.commands), 1)
            self.assertEqual(self.lifecycle.pending_cleanup_requests(), ())

        self.run_gui(scenario)

    @unittest.skipUnless(HAS_QT, "PySide6 is optional")
    def test_gui_close_waits_for_actual_upload_cleanup_unlink_and_keeps_qt_responsive(self):
        from PySide6.QtCore import QTimer

        self.prepare()
        entered = threading.Event()
        worker_threads = []
        original_init = RecordingDeletionService.__init__

        def before_unlink(phase, _identifier):
            if phase == "before_unlink":
                worker_threads.append(threading.get_ident())
                entered.set()
                if not self.release.wait(10):
                    raise RuntimeError("人工unlink待機がタイムアウトしました")

        def initialize(instance, *arguments, **keywords):
            original_init(instance, *arguments, **keywords)
            instance._fault_hook = before_unlink

        def scenario(window, app):
            heartbeat = []
            timer = QTimer(window)
            timer.setInterval(5)
            timer.timeout.connect(lambda: heartbeat.append(True))
            timer.start()
            try:
                window.history_menu_actions["history_youtube"].trigger()
                self.pump(app, window, entered.is_set)
                self.assertNotEqual(worker_threads[0], threading.get_ident())
                saved = self.assert_saved_upload()
                pending = self.lifecycle.pending_intents()
                self.assertEqual(len(pending), 1)
                self.assertEqual((pending[0].state, pending[0].reason), ("staged", "youtube_uploaded"))
                staged = self.paths.recordings / pending[0].staged_path
                self.assertEqual(staged.read_bytes(), ARTIFICIAL_MP4)
                self.assertIn(self.marker.event_id, {event.event_id for event in DuelTimelineRepository.from_runtime_paths(self.paths).list("rec")})
                self.assertFalse(window.close())
                self.assertTrue(window.close_after_background)
                self.assertTrue(window.isVisible())
                self.assertFalse(window.centralWidget().isEnabled())
                self.assertFalse(self.service._closed)
                self.assertIn("動画削除の後処理が完了", window.widgets["settings_youtube_status"].text())
                ticks_before = len(heartbeat)
                self.pump(app, window, lambda: len(heartbeat) >= ticks_before + 3)
                self.assertTrue(window.youtube_upload_running)
                self.assertFalse(window.background_tasks[0][1].done())
                self.assertTrue(staged.is_file())
                self.release.set()
                self.pump(app, window, lambda: not window.isVisible())
                self.assertTrue(self.service._closed)
                self.assertFalse(window.youtube_upload_running)
                self.assertFalse(self.source.exists())
                self.assertFalse(staged.exists())
                self.assertEqual(list((self.paths.recordings / STAGING_DIRECTORY).glob("*.pending")), [])
                self.assertEqual(self.lifecycle.pending_intents(), ())
                self.assertEqual(self.lifecycle.pending_cleanup_requests(), ())
                state = self.lifecycle.get_recording_video_state("rec")
                self.assertEqual((state.state, state.reason), ("deleted", "youtube_uploaded"))
                self.assert_saved_upload(identifier=saved.upload_id)
                self.assertEqual(DuelTimelineRepository.from_runtime_paths(self.paths).list("rec"), ())
                self.assertIn(saved.watch_url, window.widgets["settings_youtube_status"].text())
            finally:
                self.release.set()
                timer.stop()

        with patch.object(RecordingDeletionService, "__init__", initialize):
            self.run_gui(scenario)

    @unittest.skipUnless(HAS_QT, "PySide6 is optional")
    def test_gui_protected_full_history_delete_cancel_keeps_db_file_and_card_facts(self):
        from PySide6.QtWidgets import QMessageBox

        self.lifecycle.set_protection("rec", True)

        def scenario(window, app):
            before_rows = self.database_rows()
            before_file = self.source.read_bytes()
            before_state = self.lifecycle.get_recording_video_state("rec")
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as question, patch.object(
                QMessageBox, "warning", return_value=QMessageBox.StandardButton.Cancel,
            ) as warning, patch.object(self.service, "delete_history", wraps=self.service.delete_history) as delete:
                self.assertTrue(window.history_menu_actions["history_delete"].isEnabled())
                window.history_menu_actions["history_delete"].trigger()
                app.processEvents()
                question.assert_called_once()
                self.assertEqual(question.call_args.args[1], "戦績・録画履歴ごと削除")
                warning.assert_called_once()
                self.assertEqual(warning.call_args.args[1], "削除対象外の動画です")
                self.assertEqual(warning.call_args.args[-1], QMessageBox.StandardButton.Cancel)
                delete.assert_not_called()
            self.assertEqual(self.database_rows(), before_rows)
            self.assertEqual(self.source.read_bytes(), before_file)
            self.assertEqual(self.lifecycle.get_recording_video_state("rec"), before_state)
            self.assertEqual(DuelCardRepository.from_runtime_paths(self.paths).get_review(self.duel_id), self.card_review)
            self.assertTrue(self.lifecycle.get_protection("rec").protected)
            self.assertEqual(window.background_tasks, [])

        self.run_gui(scenario)


if __name__ == "__main__":
    unittest.main()
