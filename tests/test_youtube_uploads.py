import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from master_duel_recorder_lite.history_database import CURRENT_SCHEMA_VERSION
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_metadata import UploadMetadata, UploadPrivacy
from master_duel_recorder_lite.youtube_uploads import (
    YouTubeUploadError,
    YouTubeUploadRepository,
    YouTubeUploadState,
)


class YouTubeUploadRepositoryTest(unittest.TestCase):
    def _repository(self) -> tuple[RecordingHistoryRepository, YouTubeUploadRepository]:
        self.tmp = tempfile.TemporaryDirectory()
        paths = default_runtime_paths(user_data_dir=Path(self.tmp.name) / "user_data")
        ensure_runtime_dirs(paths)
        history = RecordingHistoryRepository.from_runtime_paths(paths)
        source = paths.recordings / "2026" / "08" / "21" / "recording.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video")
        history.register_starting(
            recording_id="rec-1",
            output_path=source,
            container="mp4",
            source="manual",
            created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        )
        history.mark_recording(
            "rec-1",
            started_at=datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc),
        )
        from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState

        history.finalize(
            "rec-1",
            RecordingResult(
                state=RecordingState.COMPLETED,
                output_path=source,
                returncode=0,
                started_at=datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc),
                ended_at=datetime(2026, 8, 21, 0, 1, tzinfo=timezone.utc),
                size_bytes=4,
                error=None,
                diagnostics=(),
            ),
        )
        return history, YouTubeUploadRepository.from_runtime_paths(paths)

    def tearDown(self) -> None:
        tmp = getattr(self, "tmp", None)
        if tmp is not None:
            tmp.cleanup()

    def test_schema_contains_youtube_uploads_table(self) -> None:
        self.assertGreaterEqual(CURRENT_SCHEMA_VERSION, 15)
        _history, repository = self._repository()

        upload = repository.create(
            recording_id="rec-1",
            metadata=UploadMetadata("title", privacy=UploadPrivacy.PUBLIC),
        )

        self.assertEqual(upload.state, YouTubeUploadState.WAITING)
        self.assertEqual(upload.metadata.privacy, UploadPrivacy.PUBLIC)

    def test_completed_upload_prevents_duplicate_by_default(self) -> None:
        _history, repository = self._repository()
        upload = repository.create(recording_id="rec-1", metadata=UploadMetadata("title"))
        upload = repository.update(
            upload,
            state=YouTubeUploadState.COMPLETED,
            video_id="abc123",
            watch_url="https://youtu.be/abc123",
        )

        self.assertEqual(repository.completed_for_recording("rec-1"), upload)
        with self.assertRaises(YouTubeUploadError):
            repository.create(recording_id="rec-1", metadata=UploadMetadata("again"))

    def test_upload_state_tracks_attempts_and_error(self) -> None:
        _history, repository = self._repository()
        upload = repository.create(recording_id="rec-1", metadata=UploadMetadata("title"))

        updated = repository.update(
            upload,
            state=YouTubeUploadState.UPLOADING,
            prepare_queue_id="queue-1",
            increment_attempts=True,
        )
        failed = repository.update(
            updated,
            state=YouTubeUploadState.FAILED,
            error="quota exceeded",
        )

        self.assertEqual(failed.prepare_queue_id, "queue-1")
        self.assertEqual(failed.attempts, 1)
        self.assertEqual(failed.error, "quota exceeded")


if __name__ == "__main__":
    unittest.main()
