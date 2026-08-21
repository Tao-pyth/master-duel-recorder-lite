import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_queue import UploadQueueState, UploadQueueStore
from master_duel_recorder_lite.youtube_client import (
    FakeYouTubeClient,
    YouTubeUploadFailureKind,
)
from master_duel_recorder_lite.youtube_oauth import MemoryCredentialStore, YouTubeCredentials
from master_duel_recorder_lite.youtube_service import YouTubeUploadService
from master_duel_recorder_lite.youtube_uploads import YouTubeUploadRepository, YouTubeUploadState


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


if __name__ == "__main__":
    unittest.main()
