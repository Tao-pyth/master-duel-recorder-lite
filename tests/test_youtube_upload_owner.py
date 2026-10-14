from __future__ import annotations

from contextlib import closing
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch

from master_duel_recorder_lite.data_management import ManagedDataError, ManagedDataService
from master_duel_recorder_lite.data_protection import DataProtectionError, DataProtectionService
from master_duel_recorder_lite.post_upload_cleanup import UploadCleanupOutcome
from master_duel_recorder_lite.recording_lock import RecordingBusyError, RecordingLock
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.video_management import (
    VideoManagementError, assert_video_management_allowed, video_management_gate,
)
from master_duel_recorder_lite.youtube_client import FakeYouTubeClient, YouTubeUploadFailureKind
from master_duel_recorder_lite.youtube_service import YouTubeServiceError, YouTubeUploadService
from master_duel_recorder_lite.youtube_uploads import YouTubeUploadState
import tests.test_youtube_service as youtube_fixture


class YouTubeUploadOwnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = youtube_fixture.YouTubeUploadServiceTest("test_upload_reuses_completed_prepare_and_saves_watch_url")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.fixture._completed_prepare()
        self.paths = self.fixture.paths
        self.owner_path = self.paths.data / "youtube-upload.lock"
        self.root_lock = self.paths.data / "video-operations.lock"

    def service(self, **kwargs) -> YouTubeUploadService:
        values = dict(paths=self.paths, upload_repository=self.fixture.uploads, queue=self.fixture.queue,
                      credential_store=self.fixture.credentials, youtube_client=FakeYouTubeClient())
        values.update(kwargs)
        return YouTubeUploadService(**values)

    def assert_owner_busy(self) -> None:
        with self.assertRaises(RecordingBusyError):
            with RecordingLock.acquire(self.owner_path, recording_id="owner-probe"):
                self.fail("owner must remain held")

    def assert_owner_free(self) -> None:
        with RecordingLock.acquire(self.owner_path, recording_id="after-upload"):
            pass

    def test_constructor_and_idle_management_check_do_not_create_or_replay_upload_owner(self) -> None:
        client = FakeYouTubeClient()
        self.service(youtube_client=client)
        self.assertFalse(self.owner_path.exists())
        with video_management_gate(self.paths):
            pass
        self.assertFalse(self.owner_path.exists())
        self.assertEqual(self.fixture.uploads.list(), ())
        self.assertEqual(client.uploaded, [])

    def test_owner_is_acquired_before_create_and_remains_through_cleanup_with_root_lock_free(self) -> None:
        original_create = self.fixture.uploads.create

        def create(**values):
            self.assert_owner_busy()
            return original_create(**values)

        def cleanup(upload, _prepared, _result):
            self.assertEqual(self.fixture.uploads.get(upload.upload_id).state, YouTubeUploadState.COMPLETED)
            self.assert_owner_busy()
            # 後処理は同じ動画排他を使えるので、投稿ownerだけを保持します。
            with RecordingLock.acquire(self.root_lock, recording_id="cleanup-video-operation"):
                with RecordingLock.acquire(self.paths.data / "recording.lock", recording_id="cleanup-video-operation"):
                    with self.assertRaisesRegex(VideoManagementError, "YouTube"):
                        assert_video_management_allowed(self.paths)
            return UploadCleanupOutcome("skipped", "fixture", "後処理終端")

        with patch.object(self.fixture.uploads, "create", side_effect=create):
            outcome = self.service(post_upload_cleanup=cleanup).upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))
        self.assertEqual(outcome.cleanup.status, "skipped")
        self.assertEqual(outcome.upload.watch_url, "https://youtu.be/fake-video-id")
        self.assert_owner_free()

    def test_waiting_query_and_private_processing_routes_use_the_same_owner(self) -> None:
        waiting = self.fixture.uploads.create(recording_id="rec-1", metadata=UploadMetadata("waiting"))
        original_list = self.fixture.uploads.list

        def list_uploads(**values):
            self.assert_owner_busy()
            return original_list(**values)

        def cleanup(_upload, _prepared, _result):
            self.assert_owner_busy()
            return UploadCleanupOutcome("disabled", "disabled", "終了")

        service = self.service(post_upload_cleanup=cleanup)
        with patch.object(self.fixture.uploads, "list", side_effect=list_uploads):
            outcome = service.run_waiting()[0]
        self.assertEqual(outcome.upload.upload_id, waiting.upload_id)
        self.assert_owner_free()
        another = self.fixture.uploads.create(recording_id="rec-1", metadata=UploadMetadata("private route"), force_new=True)
        outcome = service._process_upload(another, max_attempts=1)
        self.assertEqual(outcome.upload.state, YouTubeUploadState.COMPLETED)
        self.assert_owner_free()

    def test_same_instance_same_thread_can_reenter_but_another_instance_is_busy_before_rewrite(self) -> None:
        first = self.service()
        competitor = self.service()
        with first.upload_owner(), first.upload_owner():
            self.assert_owner_busy()
            with self.assertRaises(YouTubeServiceError):
                competitor.upload_recording(recording_id="rec-1", metadata=UploadMetadata("competitor"))
            self.assertEqual(self.fixture.uploads.list(), ())
            self.assertEqual(competitor.youtube_client.uploaded, [])
            outcome = first.upload_recording(recording_id="rec-1", metadata=UploadMetadata("nested owner"))
            self.assertEqual(outcome.upload.state, YouTubeUploadState.COMPLETED)
            self.assert_owner_busy()
        self.assert_owner_free()

    def test_management_lock_rejects_upload_before_create_and_upload_owner_release_allows_management(self) -> None:
        client = FakeYouTubeClient()
        service = self.service(youtube_client=client)
        with video_management_gate(self.paths):
            with self.assertRaises(YouTubeServiceError):
                service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("busy"))
            self.assertEqual(self.fixture.uploads.list(), ())
            self.assertEqual(client.uploaded, [])
        service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("after management"))
        with video_management_gate(self.paths):
            pass

    def test_retry_failure_and_cleanup_exception_all_release_owner_without_losing_success_url(self) -> None:
        client = FakeYouTubeClient(failures=(YouTubeUploadFailureKind.RETRIABLE,))

        def sleep(_delay):
            self.assert_owner_busy()
            with RecordingLock.acquire(self.root_lock, recording_id="during-retry"):
                pass

        service = self.service(youtube_client=client, sleep=sleep,
                               post_upload_cleanup=Mock(side_effect=RuntimeError("terminal cleanup failure")))
        completed = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("retry"))
        self.assertEqual(completed.upload.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(completed.cleanup.status, "failed")
        self.assertEqual(self.fixture.uploads.get(completed.upload.upload_id).watch_url, "https://youtu.be/fake-video-id")
        self.assert_owner_free()
        failed = self.service(youtube_client=FakeYouTubeClient(failures=(YouTubeUploadFailureKind.PERMANENT,))).upload_recording(
            recording_id="rec-1", metadata=UploadMetadata("failed"), force_new_upload=True,
        )
        self.assertEqual(failed.upload.state, YouTubeUploadState.FAILED)
        self.assertEqual(self.fixture.uploads.get(completed.upload.upload_id).watch_url, "https://youtu.be/fake-video-id")
        self.assert_owner_free()

    def test_owner_is_released_when_credential_read_raises_before_client_send(self) -> None:
        client = FakeYouTubeClient()
        service = self.service(youtube_client=client)
        with patch.object(self.fixture.credentials, "read", side_effect=RuntimeError("credential read failed")):
            with self.assertRaisesRegex(RuntimeError, "credential read failed"):
                service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("failure"))
        self.assertEqual(client.uploaded, [])
        self.assert_owner_free()

    def test_missing_original_with_valid_prepared_mp4_still_posts_and_saves_url(self) -> None:
        self.fixture.source.unlink()
        outcome = self.service().upload_recording(recording_id="rec-1", metadata=UploadMetadata("prepared only"))
        self.assertEqual(outcome.upload.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(outcome.upload.watch_url, "https://youtu.be/fake-video-id")
        self.assert_owner_free()

    def test_same_instance_other_thread_is_busy_during_completed_cleanup_and_unblocks_after_terminal(self) -> None:
        entered = threading.Event()
        released = threading.Event()
        outcomes = []
        errors = []

        def cleanup(_upload, _prepared, _result):
            entered.set()
            if not released.wait(5):
                raise RuntimeError("fixture cleanup did not receive release")
            return UploadCleanupOutcome("skipped", "fixture", "終了")

        service = self.service(post_upload_cleanup=cleanup)

        def upload():
            try:
                outcomes.append(service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("thread")))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=upload)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertTrue(worker.is_alive())
            before = self.fixture.uploads.list()
            self.assertEqual(before[0].state, YouTubeUploadState.COMPLETED)
            with self.assertRaises(YouTubeServiceError):
                service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("other thread"), force_new_upload=True)
            self.assertEqual(self.fixture.uploads.list(), before)
            with self.assertRaisesRegex(VideoManagementError, "YouTube"):
                with video_management_gate(self.paths):
                    self.fail("COMPLETED後も後処理中は管理不可")
        finally:
            released.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(outcomes[0].upload.state, YouTubeUploadState.COMPLETED)
        with video_management_gate(self.paths):
            pass

    def test_another_process_completed_cleanup_blocks_json_reset_restore_and_state_only_relink_gate(self) -> None:
        managed = ManagedDataService.from_runtime_paths(self.paths)
        exported = self.paths.exports / "before-upload.json"
        managed.export_to(exported)
        protection = DataProtectionService(self.paths)
        backup = protection.create_backup("owner-fixture", protected=True)
        code = """
import sys
from pathlib import Path
from master_duel_recorder_lite.runtime_paths import default_runtime_paths
from master_duel_recorder_lite.post_upload_cleanup import UploadCleanupOutcome
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_queue import UploadQueueStore
from master_duel_recorder_lite.youtube_client import FakeYouTubeClient
from master_duel_recorder_lite.youtube_oauth import MemoryCredentialStore, YouTubeCredentials
from master_duel_recorder_lite.youtube_service import YouTubeUploadService
from master_duel_recorder_lite.youtube_uploads import YouTubeUploadRepository
paths = default_runtime_paths(user_data_dir=Path(sys.argv[1]))
store = MemoryCredentialStore()
store.write(YouTubeCredentials('fixture-client','fixture-secret','fixture-refresh'))
def cleanup(upload, prepared, result):
    print('COMPLETED:' + upload.upload_id, flush=True)
    sys.stdin.readline()
    return UploadCleanupOutcome('skipped','fixture','terminal')
service = YouTubeUploadService(paths=paths, upload_repository=YouTubeUploadRepository.from_runtime_paths(paths),
                             queue=UploadQueueStore(paths), credential_store=store,
                             youtube_client=FakeYouTubeClient(), post_upload_cleanup=cleanup)
service.upload_recording(recording_id='rec-1', metadata=UploadMetadata('process fixture'))
print('TERMINAL', flush=True)
"""
        environment = dict(os.environ)
        environment["MDRL_USER_DATA_DIR"] = str(self.paths.root)
        process = subprocess.Popen([sys.executable, "-B", "-W", "error::ResourceWarning", "-c", code, str(self.paths.root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=environment)

        def finish_process():
            if process.poll() is None:
                try:
                    process.communicate("release\n", timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

        self.addCleanup(finish_process)
        output = queue.Queue()
        reader = threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            line = output.get(timeout=5)
        except queue.Empty:
            self.fail("fixture subprocessが投稿完了境界へ到達しません")
        reader.join(1)
        self.assertTrue(line.startswith("COMPLETED:"), line)
        self.assertIsNone(process.poll())
        upload_id = line.partition(":")[2].strip()
        saved = self.fixture.uploads.get(upload_id)
        self.assertEqual(saved.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(saved.watch_url, "https://youtu.be/fake-video-id")
        before = self.fixture.uploads.list()
        for action in (lambda: managed.import_from(exported), lambda: managed.reset("history")):
            with self.subTest(action=action), self.assertRaisesRegex(ManagedDataError, "YouTube"):
                action()
            self.assertEqual(self.fixture.uploads.list(), before)
        with self.assertRaisesRegex(DataProtectionError, "YouTube"):
            protection.restore(backup.path)
        with RecordingLock.acquire(self.root_lock, recording_id="relink-fixture"):
            with RecordingLock.acquire(self.paths.data / "recording.lock", recording_id="relink-fixture"):
                with self.assertRaisesRegex(VideoManagementError, "YouTube"):
                    assert_video_management_allowed(self.paths)
        with self.assertRaises(YouTubeServiceError):
            self.service().upload_recording(recording_id="rec-1", metadata=UploadMetadata("another process"), force_new_upload=True)
        self.assertEqual(self.fixture.uploads.list(), before)
        out, err = process.communicate("release\n", timeout=5)
        self.assertEqual(process.returncode, 0, err)
        self.assertIn("TERMINAL", out)
        self.assertEqual(self.fixture.uploads.get(upload_id), saved)
        self.assertTrue(self.fixture.source.exists())
        with video_management_gate(self.paths):
            pass
        with closing(sqlite3.connect(self.fixture.uploads.database_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
