import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_queue import UploadQueueState, UploadQueueStore
from master_duel_recorder_lite.youtube_client import (
    FakeYouTubeClient,
    YouTubeUploadResult,
    YouTubeUploadFailureKind,
)
from master_duel_recorder_lite.youtube_oauth import MemoryCredentialStore, YouTubeCredentials
from master_duel_recorder_lite.youtube_service import YouTubeUploadService
from master_duel_recorder_lite.post_upload_cleanup import UploadCleanupOutcome
from master_duel_recorder_lite.youtube_uploads import YouTubeUploadError, YouTubeUploadRepository, YouTubeUploadState


class YouTubeUploadServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = default_runtime_paths(user_data_dir=Path(self.tmp.name) / "user_data")
        ensure_runtime_dirs(self.paths)
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.source = self.paths.recordings / "2026" / "08" / "21" / "recording.mp4"
        self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b"video")
        self.history.register_starting(
            recording_id="rec-1",
            output_path=self.source,
            container="mp4",
            source="manual",
            created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )
        self.history.mark_recording(
            "rec-1",
            started_at=datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc),
        )
        self.history.finalize(
            "rec-1",
            RecordingResult(
                state=RecordingState.COMPLETED,
                output_path=self.source,
                returncode=0,
                started_at=datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 8, 21, 0, 1, tzinfo=timezone.utc),
                size_bytes=4,
                error=None,
                diagnostics=(),
            ),
        )
        self.queue = UploadQueueStore(self.paths)
        self.uploads = YouTubeUploadRepository.from_runtime_paths(self.paths)
        self.credentials = MemoryCredentialStore()
        self.credentials.write(YouTubeCredentials("client", "secret", "refresh"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _completed_prepare(self) -> None:
        export = self.paths.exports / "rec-1" / "prepared.mp4"
        export.parent.mkdir(parents=True)
        export.write_bytes(b"video")
        item = self.queue.enqueue(recording_id="rec-1", metadata=UploadMetadata("title"))
        processing = self.queue.transition(
            item.queue_id,
            UploadQueueState.PROCESSING,
            increment_attempts=True,
        )
        self.queue.transition(
            processing.queue_id,
            UploadQueueState.COMPLETED,
            export_path=export.relative_to(self.paths.root),
        )

    def test_upload_reuses_completed_prepare_and_saves_watch_url(self) -> None:
        self._completed_prepare()
        client = FakeYouTubeClient()
        service = YouTubeUploadService(
            paths=self.paths,
            upload_repository=self.uploads,
            queue=self.queue,
            credential_store=self.credentials,
            youtube_client=client,
        )

        outcome = service.upload_recording(
            recording_id="rec-1",
            metadata=UploadMetadata("title"),
        )

        self.assertEqual(outcome.upload.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(outcome.upload.watch_url, "https://youtu.be/fake-video-id")
        self.assertEqual(len(client.uploaded), 1)

    def test_cleanup_runs_after_completed_commit_and_reread_for_single(self) -> None:
        self._completed_prepare()
        calls = []

        def cleanup(upload, prepared, result):
            saved = self.uploads.get(upload.upload_id)
            self.assertEqual(saved.state, YouTubeUploadState.COMPLETED)
            self.assertEqual(saved.watch_url, result.watch_url)
            self.assertEqual(saved.prepare_queue_id, prepared.queue_id)
            calls.append(upload.upload_id)
            return UploadCleanupOutcome("skipped", "protected", "保護中")

        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=self.credentials, youtube_client=FakeYouTubeClient(), post_upload_cleanup=cleanup,
        )
        first = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("first"))
        self.assertEqual(calls, [first.upload.upload_id])
        self.assertEqual(first.cleanup.status, "skipped")

    def test_waiting_upload_uses_the_same_saved_completion_hook(self) -> None:
        self._completed_prepare()
        waiting = self.uploads.create(recording_id="rec-1", metadata=UploadMetadata("waiting"))

        def cleanup(upload, prepared, result):
            saved = self.uploads.get(upload.upload_id)
            self.assertEqual(saved.state, YouTubeUploadState.COMPLETED)
            self.assertEqual(saved.watch_url, result.watch_url)
            self.assertEqual(saved.prepare_queue_id, prepared.queue_id)
            return UploadCleanupOutcome("skipped", "protected", "保護中")

        callback = Mock(side_effect=cleanup)
        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=self.credentials, youtube_client=FakeYouTubeClient(), post_upload_cleanup=callback,
        )
        outcomes = service.run_waiting()
        self.assertEqual(outcomes[0].upload.upload_id, waiting.upload_id)
        self.assertEqual(outcomes[0].upload.state, YouTubeUploadState.COMPLETED)
        callback.assert_called_once()

    def test_cleanup_exception_does_not_overwrite_completed_upload(self) -> None:
        self._completed_prepare()
        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=self.credentials, youtube_client=FakeYouTubeClient(),
            post_upload_cleanup=Mock(side_effect=RuntimeError("cleanup-db-failure")),
        )
        outcome = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))
        saved = self.uploads.get(outcome.upload.upload_id)
        self.assertEqual(saved.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(saved.watch_url, "https://youtu.be/fake-video-id")
        self.assertIsNone(saved.error)
        self.assertEqual(outcome.cleanup.status, "failed")
        self.assertIn("cleanup-db-failure", outcome.message)

    def test_invalid_cleanup_return_still_gives_a_terminal_cleanup_failure(self) -> None:
        self._completed_prepare()
        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=self.credentials, youtube_client=FakeYouTubeClient(),
            post_upload_cleanup=lambda *_arguments: object(),
        )
        outcome = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))
        self.assertEqual(outcome.cleanup.status, "failed")
        self.assertEqual(outcome.upload.state, YouTubeUploadState.COMPLETED)

    def test_privacy_warning_and_cleanup_failure_are_both_retained(self) -> None:
        self._completed_prepare()
        client = Mock()
        client.upload_video.return_value = YouTubeUploadResult(
            "warning-video", "https://youtu.be/warning-video", "private", "公開範囲が変更されました",
        )
        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=self.credentials, youtube_client=client,
            post_upload_cleanup=lambda *_arguments: UploadCleanupOutcome("failed", "unlink_failed", "元動画の削除に失敗"),
        )
        outcome = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))
        self.assertIn("公開範囲が変更", outcome.message)
        self.assertIn("元動画の削除に失敗", outcome.message)
        self.assertEqual(outcome.upload.error, "公開範囲が変更されました")
        self.assertEqual(self.uploads.get(outcome.upload.upload_id).state, YouTubeUploadState.COMPLETED)

    def test_failed_upload_does_not_invoke_cleanup_or_create_retry(self) -> None:
        self._completed_prepare()
        cleanup = Mock()
        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=self.credentials,
            youtube_client=FakeYouTubeClient(failures=(YouTubeUploadFailureKind.PERMANENT,)),
            post_upload_cleanup=cleanup,
        )
        outcome = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))
        self.assertEqual(outcome.upload.state, YouTubeUploadState.FAILED)
        cleanup.assert_not_called()
        self.assertIsNone(outcome.cleanup)

    def test_completed_commit_then_reread_failure_never_rolls_upload_back_to_failed(self) -> None:
        self._completed_prepare()
        cleanup = Mock()
        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=self.credentials, youtube_client=FakeYouTubeClient(), post_upload_cleanup=cleanup,
        )
        original_get = self.uploads.get

        def reread(upload_id):
            saved = original_get(upload_id)
            if saved.state is YouTubeUploadState.COMPLETED:
                raise RuntimeError("completed-reread-failed")
            return saved

        with patch.object(self.uploads, "get", side_effect=reread):
            outcome = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))
        saved = original_get(outcome.upload.upload_id)
        self.assertEqual(saved.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(saved.watch_url, "https://youtu.be/fake-video-id")
        cleanup.assert_not_called()
        self.assertEqual(outcome.cleanup.reason, "completion_not_verified")

    def test_completed_save_failure_never_permits_cleanup(self) -> None:
        self._completed_prepare()
        cleanup = Mock()
        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=self.credentials, youtube_client=FakeYouTubeClient(), post_upload_cleanup=cleanup,
        )
        original_update = self.uploads.update

        def update(upload, **values):
            if values.get("state") is YouTubeUploadState.COMPLETED:
                raise RuntimeError("commit-failed")
            return original_update(upload, **values)

        with patch.object(self.uploads, "update", side_effect=update):
            outcome = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))
        cleanup.assert_not_called()
        self.assertEqual(outcome.cleanup.reason, "completion_not_verified")
        self.assertEqual(self.uploads.get(outcome.upload.upload_id).state, YouTubeUploadState.UPLOADING)

    def test_retriable_failure_increments_attempts_then_completes(self) -> None:
        self._completed_prepare()
        service = YouTubeUploadService(
            paths=self.paths,
            upload_repository=self.uploads,
            queue=self.queue,
            credential_store=self.credentials,
            youtube_client=FakeYouTubeClient(
                failures=(YouTubeUploadFailureKind.RETRIABLE,)
            ),
            sleep=lambda _seconds: None,
        )

        outcome = service.upload_recording(
            recording_id="rec-1",
            metadata=UploadMetadata("title"),
        )

        self.assertEqual(outcome.upload.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(outcome.upload.attempts, 2)

    def test_missing_credentials_marks_upload_failed_without_secret_output(self) -> None:
        self._completed_prepare()
        store = MemoryCredentialStore()
        service = YouTubeUploadService(
            paths=self.paths,
            upload_repository=self.uploads,
            queue=self.queue,
            credential_store=store,
            youtube_client=FakeYouTubeClient(),
        )

        outcome = service.upload_recording(
            recording_id="rec-1",
            metadata=UploadMetadata("title"),
        )

        self.assertEqual(outcome.upload.state, YouTubeUploadState.FAILED)
        self.assertIn("OAuth", outcome.message)

    def test_reauthorization_failure_keeps_media_and_credentials_then_can_recover(self) -> None:
        self._completed_prepare()
        original_credentials = self.credentials.read()
        export = self.paths.exports / "rec-1" / "prepared.mp4"
        original_queue = (self.paths.queue / "upload-preparation.json").read_bytes()
        client = FakeYouTubeClient(failures=(YouTubeUploadFailureKind.REAUTHORIZE,))
        sleep = Mock()
        service = YouTubeUploadService(
            paths=self.paths, upload_repository=self.uploads, queue=self.queue,
            credential_store=self.credentials, youtube_client=client, sleep=sleep,
        )

        failed = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))

        self.assertEqual(failed.upload.state, YouTubeUploadState.FAILED)
        self.assertEqual(failed.upload.attempts, 1)
        self.assertIn("設定 → YouTube", failed.message)
        sleep.assert_not_called()
        self.assertEqual(self.credentials.read(), original_credentials)
        self.assertEqual(self.source.read_bytes(), b"video")
        self.assertEqual(export.read_bytes(), b"video")
        self.assertEqual((self.paths.queue / "upload-preparation.json").read_bytes(), original_queue)

        self.credentials.write(YouTubeCredentials("client", "secret", "new-refresh"))
        recovered = service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))
        self.assertEqual(recovered.upload.state, YouTubeUploadState.COMPLETED)
        self.assertEqual(len(client.uploaded), 1)
        with self.assertRaises(YouTubeUploadError):
            service.upload_recording(recording_id="rec-1", metadata=UploadMetadata("title"))
        self.assertEqual(len(client.uploaded), 1)


if __name__ == "__main__":
    unittest.main()
