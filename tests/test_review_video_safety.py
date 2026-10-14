"""一時DB・実Qtプレイヤーで、動画利用排他とマーカーの同一性を確認する。"""
from __future__ import annotations

from contextlib import closing, contextmanager
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.card_analysis_models import CardInputFingerprint, CardObservationValues
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisRepository
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.history_database import connect_history_database
from master_duel_recorder_lite.recording_retention import RecordingDeletionService, fingerprint_file
from master_duel_recorder_lite.review_viewmodel import ReviewMarkerRequest
from master_duel_recorder_lite.video_lifecycle import VideoLifecycleRepository
from test_gui_refresh_and_review_lifecycle import register_video


class ReviewSourceFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.service = RecorderApplicationService(user_data_dir=Path(self.temporary.name) / "runtime")
        self.addCleanup(self.service.close)
        self.recording_id = "review"
        self.source = register_video(self.service, self.recording_id)
        self.lifecycle = VideoLifecycleRepository.from_runtime_paths(self.service.paths)
        self.deletion = RecordingDeletionService(self.service.paths, repository=self.lifecycle)
        self.cards = CardAnalysisRepository.from_runtime_paths(self.service.paths)

    @contextmanager
    def connection(self):
        with closing(connect_history_database(self.lifecycle.database_path)) as connection, connection:
            yield connection

    def marker(self):
        return self.service.add_review_marker(ReviewMarkerRequest(self.recording_id, 900, "メモ: 保存位置"))

    def card_marker(self):
        target = self.service.save_card_target(name="検証カード", purpose="検証")
        stat = self.source.stat()
        fingerprint = CardInputFingerprint(str(self.source), stat.st_size, stat.st_mtime_ns,
                                           hashlib.sha256(self.source.read_bytes()).hexdigest())
        job = self.cards.create_job(self.recording_id, fingerprint=fingerprint, analyzer_version="safety-fixture")
        self.cards.claim_job(job.job_id)
        self.cards.complete_job(job.job_id, (
            CardObservationValues(target.target_id, target.name, "self", "activate", 500,
                                  evidence={"supports_use": True}, source_key="proof-fixture"),
        ), fingerprint=fingerprint)
        return self.cards.list_observations(self.recording_id)[0]

    def make_imported_unverified(self, *, unknown_proof=False):
        # JSON取込後と同じく、持ち込んだ証拠を実行可能な検証済み状態にしない。
        with self.connection() as connection:
            for table in ("duel_event_sources", "card_observation_links", "card_analysis_job_sources"):
                connection.execute(f"UPDATE {table} SET verified=0")
                if unknown_proof:
                    connection.execute(f"UPDATE {table} SET input_fingerprint_json='{{}}'")
            connection.execute("UPDATE recording_video_assets SET state='unverified',sha256='',size_bytes=NULL,mtime_ns=NULL")

    def assert_no_uses(self):
        asset = self.lifecycle.get_current_asset(self.recording_id)
        if asset:
            self.assertEqual(self.lifecycle.list_uses(asset.asset_id), ())


class ReviewVerificationTest(ReviewSourceFixture):
    def test_background_verification_hashes_once_for_normal_and_card_sources_and_reuses_cache(self):
        normal, card = self.marker(), self.card_marker()
        self.make_imported_unverified()
        original = self.source.stat()
        os.utime(self.source, ns=(original.st_atime_ns, original.st_mtime_ns + 5_000_000_000))
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", wraps=fingerprint_file) as hashed:
            self.assertGreaterEqual(self.service.verify_recording_video_sources(self.recording_id), 3)
            self.assertGreaterEqual(self.service.verify_recording_video_sources(self.recording_id), 1)
            for _ in range(3):
                model = self.service.get_review_view_model(self.recording_id)
                self.assertEqual({event.event_id for event in model.timeline if event.video_verified},
                                 {normal.event_id, f"card:{card.event_id}"})
                self.assertEqual(len(model.visual_timeline), 2)
                self.assertEqual(len(model.clip_candidates), 2)
            self.assertEqual(hashed.call_count, 1)
        self.assert_no_uses()

    def test_old_json_proof_cannot_enable_positions_for_replacement_at_same_path(self):
        normal, card = self.marker(), self.card_marker()
        values = DuelRecordValues(status="confirmed", result="win", notes="動画に依存しない戦績")
        self.service.save_duel_record(self.recording_id, values, expected_revision=0)
        self.make_imported_unverified()
        self.source.write_bytes(b"new video!")
        self.service.verify_recording_video_sources(self.recording_id)
        model = self.service.get_review_view_model(self.recording_id)
        self.assertEqual({event.event_id for event in model.timeline}, {normal.event_id, f"card:{card.event_id}"})
        self.assertTrue(all(not event.video_verified for event in model.timeline))
        self.assertEqual(model.visual_timeline, ())
        self.assertEqual(model.clip_candidates, ())
        self.assertEqual(self.service.get_duel_record(self.recording_id).values, values)
        self.assert_no_uses()

    def test_unknown_legacy_proof_never_hashes_or_automatically_promotes(self):
        self.marker()
        self.card_marker()
        self.make_imported_unverified(unknown_proof=True)
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", side_effect=AssertionError("unknown source must not gain proof")):
            self.assertEqual(self.service.verify_recording_video_sources(self.recording_id), 0)
            model = self.service.get_review_view_model(self.recording_id)
        self.assertEqual(len(model.timeline), 2)
        self.assertTrue(all(not event.video_verified for event in model.timeline))
        self.assertEqual(model.clip_candidates, ())
        self.assertEqual(model.visual_timeline, ())
        self.assert_no_uses()

    def test_changed_actual_source_invalidates_cached_positions_without_hashing_in_getter(self):
        self.marker()
        self.card_marker()
        self.service.verify_recording_video_sources(self.recording_id)
        self.source.write_bytes(b"changed source contents")
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", side_effect=AssertionError("getter must not hash")):
            model = self.service.get_review_view_model(self.recording_id)
        self.assertTrue(all(not event.video_verified for event in model.timeline))
        self.assertEqual(model.clip_candidates, ())
        self.assertEqual(model.visual_timeline, ())

    def test_pending_deletion_refuses_background_verification_before_hash(self):
        self.marker()
        asset = self.lifecycle.get_current_asset(self.recording_id)
        self.lifecycle.create_intent(
            recording_id=self.recording_id, asset_id=asset.asset_id, generation=asset.generation, reason="manual",
            original_path=asset.path, staged_path=".mdrl-video-staging/" + "a" * 32 + ".pending",
            size_bytes=asset.size_bytes, mtime_ns=asset.mtime_ns, sha256=asset.sha256,
            backup_path=str(Path(self.temporary.name) / "fixture-backup"),
        )
        with patch("master_duel_recorder_lite.recording_retention.fingerprint_file", side_effect=AssertionError("pending source must not be opened")):
            with self.assertRaises(RuntimeError):
                self.service.verify_recording_video_sources(self.recording_id)
        self.assert_no_uses()
        self.assertTrue(self.source.exists())


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class ReviewPlayerLeaseTest(ReviewSourceFixture):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        from PySide6.QtWidgets import QMessageBox
        self.warning = self.enterContext(patch.object(QMessageBox, "warning"))
        self.enterContext(patch.object(self.service, "play_recording"))

    def wait_for(self, condition):
        deadline = time.monotonic() + 6
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(condition())

    def close_review(self, window):
        import shiboken6
        from PySide6.QtCore import QCoreApplication, QEvent
        if shiboken6.isValid(window):
            window.close()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def open_review(self):
        from master_duel_recorder_lite.pyside_review import create_review_window
        window = create_review_window(service=self.service, recording_id=self.recording_id)
        self.addCleanup(self.close_review, window)
        window.show()
        self.app.processEvents()
        return window

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed for actual media opening")
    def test_actual_media_lease_blocks_delete_until_stop_and_empty_source(self):
        from PySide6.QtCore import QUrl
        self.recording_id = "real"
        self.source = register_video(self.service, self.recording_id, real_video=True)
        window = self.open_review()
        window.player.play()
        self.wait_for(lambda: window.player.position() > 0)
        result = self.deletion.delete_video(self.recording_id)
        self.assertEqual((result.status, result.reason), ("skipped", "source_in_use"))
        window.player.stop()
        self.assertFalse(window.source_session.released)
        window.player.setSource(QUrl())
        self.wait_for(lambda: window.source_session.released)
        self.assert_no_uses()
        self.close_review(window)
        self.assertEqual(self.deletion.delete_video(self.recording_id).status, "deleted")
        self.assertFalse(self.source.exists())

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed for actual media opening")
    def test_closing_actual_playing_window_releases_file_and_lease(self):
        self.recording_id = "real"
        self.source = register_video(self.service, self.recording_id, real_video=True)
        window = self.open_review()
        window.player.play()
        self.wait_for(lambda: window.player.position() > 0)
        self.assertEqual(self.deletion.delete_video(self.recording_id).reason, "source_in_use")
        self.close_review(window)
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video(self.recording_id).status, "deleted")

    def test_model_initialization_failure_releases_registered_lease(self):
        from master_duel_recorder_lite.pyside_review import create_review_window

        def fail(_recording_id):
            asset = self.lifecycle.get_current_asset(self.recording_id)
            self.assertEqual(len(self.lifecycle.list_uses(asset.asset_id)), 1)
            raise RuntimeError("injected model initialization failure")

        with patch.object(self.service, "get_review_view_model", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "model initialization failure"):
                create_review_window(service=self.service, recording_id=self.recording_id)
        self.assert_no_uses()
        self.assertEqual(self.deletion.delete_video(self.recording_id).status, "deleted")

    def test_initialization_failure_after_actual_set_source_clears_player_and_lease(self):
        from PySide6.QtMultimedia import QMediaPlayer
        from master_duel_recorder_lite.pyside_card_analysis import CardReviewPanel
        from master_duel_recorder_lite.pyside_review import create_review_window
        players = []

        class ObservedPlayer(QMediaPlayer):
            def __init__(self, parent):
                super().__init__(parent)
                players.append(self)

        def fail(_panel, *_args, **_kwargs):
            self.assertFalse(players[0].source().isEmpty())
            asset = self.lifecycle.get_current_asset(self.recording_id)
            self.assertEqual(len(self.lifecycle.list_uses(asset.asset_id)), 1)
            raise RuntimeError("injected panel initialization failure")

        with patch("PySide6.QtMultimedia.QMediaPlayer", ObservedPlayer), patch.object(CardReviewPanel, "__init__", fail):
            with self.assertRaisesRegex(RuntimeError, "panel initialization failure"):
                create_review_window(service=self.service, recording_id=self.recording_id)
        self.assertTrue(players[0].source().isEmpty())
        self.assertIsNone(players[0].videoOutput())
        self.assert_no_uses()
        self.app.processEvents()
        self.assertEqual(self.deletion.delete_video(self.recording_id).status, "deleted")

    def test_unverified_normal_and_card_markers_remain_labels_but_cannot_seek_or_export(self):
        from PySide6.QtWidgets import QWidget
        self.marker()
        self.card_marker()
        self.make_imported_unverified()
        self.source.write_bytes(b"replaced video")
        self.service.verify_recording_video_sources(self.recording_id)
        window = self.open_review()
        table = window.findChild(QWidget, "review_timeline_table")
        clip = window.findChild(QWidget, "review_clip_export")
        for row in range(2):
            table.selectRow(row)
            self.app.processEvents()
            self.assertEqual(table.item(row, 2).text(), "動画未検証")
            self.assertFalse(clip.isEnabled())
            with patch.object(window.player, "setPosition") as seek, patch.object(self.service, "export_review_clip") as export:
                table.cellClicked.emit(row, 0)
                clip.click()
                seek.assert_not_called()
                export.assert_not_called()
        panel = window.card_panel
        panel.table.selectRow(0)
        with patch.object(window.player, "setPosition") as seek:
            panel.seek_current()
            seek.assert_not_called()
        self.assertEqual(self.service.get_review_view_model(self.recording_id).visual_timeline, ())
        self.close_review(window)
        self.assert_no_uses()

    def test_service_without_explicit_runtime_paths_cannot_open_player(self):
        from types import SimpleNamespace
        from master_duel_recorder_lite.pyside_review import PySideReviewError, create_review_window
        with self.assertRaises(PySideReviewError):
            create_review_window(service=SimpleNamespace(), recording_id=self.recording_id)
        self.assert_no_uses()

    def test_source_replacement_after_rendering_refuses_stale_row_and_card_positions(self):
        from PySide6.QtWidgets import QWidget
        self.marker()
        self.card_marker()
        window = self.open_review()
        table = window.findChild(QWidget, "review_timeline_table")
        clip = window.findChild(QWidget, "review_clip_export")
        self.source.write_bytes(b"replaced after rendering")
        for row in range(2):
            table.selectRow(row)
            with patch.object(window.player, "setPosition") as seek, patch.object(self.service, "export_review_clip") as export:
                table.cellClicked.emit(row, 0)
                clip.click()
                seek.assert_not_called()
                export.assert_not_called()
        panel = window.card_panel
        panel.table.selectRow(0)
        with patch.object(window.player, "setPosition") as seek:
            panel.seek_current()
            seek.assert_not_called()
        self.assertTrue(self.warning.called)
        self.close_review(window)
        self.assert_no_uses()


if __name__ == "__main__":
    unittest.main()
