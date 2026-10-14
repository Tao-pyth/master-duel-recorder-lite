from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.recording_lock import RecordingBusyError, RecordingLock
from master_duel_recorder_lite.runtime_paths import default_runtime_paths
from master_duel_recorder_lite.upload_queue import UploadQueueState
from master_duel_recorder_lite.video_management import (
    VideoManagementError, assert_video_management_allowed, video_management_gate,
)


class VideoManagementGateTest(unittest.TestCase):
    def setUp(self):
        import test_post_upload_cleanup as fixtures

        self.fixture = fixtures.PostUploadCleanupTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.handler = self.fixture.real_handler()
        self.paths = self.fixture.paths
        self.repository = self.handler.lifecycle
        self.asset = self.repository.ensure_current_asset("rec")

    def test_whole_mutation_holds_both_shared_locks(self):
        with video_management_gate(self.paths):
            for name in ("video-operations.lock", "recording.lock"):
                with self.subTest(name=name), self.assertRaises(RecordingBusyError):
                    RecordingLock.acquire(self.paths.data / name, recording_id="competitor")
        with RecordingLock.acquire(self.paths.data / "recording.lock", recording_id="after"):
            pass

    def test_shared_deletion_service_outer_lock_can_use_the_state_only_helper(self):
        with self.handler.deletion.operation_lock():
            assert_video_management_allowed(self.paths)

    def test_body_failure_is_not_mislabeled_as_recording_busy(self):
        with self.assertRaisesRegex(PermissionError, "actual write failure"):
            with video_management_gate(self.paths):
                raise PermissionError("actual write failure")

    def test_pending_journal_is_rejected_without_changing_source_or_current_db(self):
        import hashlib
        stat = self.fixture.source.stat()
        self.repository.set_verified_asset(
            self.asset.asset_id, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns,
            sha256=hashlib.sha256(self.fixture.source.read_bytes()).hexdigest(),
        )
        intent = self.repository.create_intent(
            recording_id="rec", asset_id=self.asset.asset_id, generation=1, reason="manual",
            original_path="original.mkv", staged_path=".mdrl-video-staging/" + "a" * 32 + ".pending",
            size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns,
            sha256=hashlib.sha256(self.fixture.source.read_bytes()).hexdigest(),
            backup_path=str(self.paths.data / "fixture-backup"),
        )
        for state in ("intent", "staged", "unlinked"):
            with self.subTest(state=state):
                if state != "intent":
                    self.repository.update_intent(intent.intent_id, state)
                before = self.repository.database_path.read_bytes()
                with self.assertRaisesRegex(VideoManagementError, "通常起動"):
                    with video_management_gate(self.paths):
                        self.fail("mutation must be blocked")
                self.assertEqual(self.repository.database_path.read_bytes(), before)
                self.assertTrue(self.fixture.source.exists())

    def test_alive_or_unknown_lease_is_retained_and_blocks_management(self):
        for process_id in (os.getpid(), 0):
            with self.subTest(process_id=process_id):
                use = self.repository.register_use(asset_id=self.asset.asset_id, owner_id="fixture", purpose="review", process_id=process_id)
                with self.assertRaisesRegex(VideoManagementError, "利用"):
                    with video_management_gate(self.paths):
                        self.fail("mutation must be blocked")
                self.assertEqual(len(self.repository.list_uses(self.asset.asset_id)), 1)
                self.repository.release_use(use.use_id)

    def test_definitely_dead_lease_can_be_reaped_under_shared_lock(self):
        self.repository.register_use(asset_id=self.asset.asset_id, owner_id="old", purpose="review", process_id=1234567890)
        with patch("master_duel_recorder_lite.video_management._process_alive", return_value=False):
            with video_management_gate(self.paths):
                self.assertEqual(self.repository.list_uses(self.asset.asset_id), ())

    def test_old_noncurrent_generation_lease_also_blocks_mutation(self):
        with closing(sqlite3.connect(self.repository.database_path)) as connection, connection:
            connection.execute(
                "INSERT INTO recording_video_assets (asset_id,recording_id,generation,is_current,path,time_basis,state,created_at,updated_at) "
                "VALUES ('old-asset','rec',2,0,'old.mkv','recording_start','unverified','2026-01-01','2026-01-01')"
            )
            connection.execute("INSERT INTO video_asset_uses VALUES ('old-use','old-asset','old-reader','review',?,'2026-01-01')", (os.getpid(),))
        with self.assertRaisesRegex(VideoManagementError, "利用"):
            with video_management_gate(self.paths):
                self.fail("mutation must be blocked")

    def test_youtube_active_and_preparation_queue_processing_block_management(self):
        for state in ("preparing", "uploading"):
            with self.subTest(state=state):
                with closing(sqlite3.connect(self.repository.database_path)) as connection, connection:
                    connection.execute("UPDATE youtube_uploads SET state=?", (state,))
                with self.assertRaisesRegex(VideoManagementError, "YouTube"):
                    with video_management_gate(self.paths):
                        self.fail("mutation must be blocked")
        with closing(sqlite3.connect(self.repository.database_path)) as connection, connection:
            connection.execute("UPDATE youtube_uploads SET state='completed'")
        queued = self.fixture.queue.enqueue(recording_id="another", metadata=self.fixture.metadata)
        self.fixture.queue.transition(queued.queue_id, UploadQueueState.PROCESSING)
        with self.assertRaisesRegex(VideoManagementError, "準備"):
            with video_management_gate(self.paths):
                self.fail("mutation must be blocked")

    def test_old_schema_tables_absent_is_legal_and_running_analysis_is_still_guarded(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = default_runtime_paths(user_data_dir=Path(temporary))
            database = Path(temporary) / "legacy.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE schema_version(version INTEGER)")
                connection.execute("INSERT INTO schema_version VALUES(17)")
            with video_management_gate(paths, database_path=database):
                pass
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE card_analysis_jobs(state TEXT)")
                connection.execute("INSERT INTO card_analysis_jobs VALUES('running')")
            with self.assertRaisesRegex(VideoManagementError, "カード解析"):
                with video_management_gate(paths, database_path=database):
                    self.fail("mutation must be blocked")


if __name__ == "__main__":
    unittest.main()
