import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.review_viewmodel import (
    ReviewMarkerRequest,
    ReviewModelError,
)
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.youtube_oauth import MemoryCredentialStore, YouTubeCredentials
from master_duel_recorder_lite.youtube_uploads import (
    YouTubeUploadRepository,
    YouTubeUploadState,
)


class ReviewViewModelTest(unittest.TestCase):
    def test_review_view_model_contains_gui_independent_review_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            recording_id = "review-1"
            output = service.paths.recordings / "2026" / "08" / "22" / "review.mp4"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"video")
            started = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)
            history = RecordingHistoryRepository.from_runtime_paths(service.paths)
            history.register_starting(
                recording_id=recording_id,
                output_path=output,
                container="mp4",
                source="manual",
                created_at=started,
            )
            history.finalize(
                recording_id,
                RecordingResult(
                    RecordingState.COMPLETED,
                    output,
                    0,
                    started,
                    started + timedelta(seconds=60),
                    output.stat().st_size,
                    None,
                    (),
                ),
            )
            service.save_duel_record(
                recording_id,
                DuelRecordValues(
                    status="confirmed",
                    result="win",
                    play_order="first",
                    own_deck="青眼",
                    tags=("大会",),
                ),
                expected_revision=0,
            )
            service.add_timeline_event(
                recording_id,
                elapsed_ms=15000,
                event_type="marker",
                label="初動確認",
            )
            uploads = YouTubeUploadRepository.from_runtime_paths(service.paths)
            upload = uploads.create(
                recording_id=recording_id,
                metadata=UploadMetadata("title"),
            )
            uploads.update(
                upload,
                state=YouTubeUploadState.COMPLETED,
                video_id="video-id",
                watch_url="https://youtu.be/video-id",
            )

            model = service.get_review_view_model(recording_id)
            document = model.to_dict()

        self.assertEqual(document["recording"]["recording_id"], recording_id)
        self.assertEqual(document["video"]["suffix"], ".mp4")
        self.assertTrue(document["video"]["can_play_in_app"])
        self.assertEqual(document["duel"]["own_deck"], "青眼")
        self.assertEqual(document["duel"]["youtube_watch_url"], "https://youtu.be/video-id")
        self.assertEqual(document["timeline"][0]["label"], "初動確認")
        self.assertEqual(document["clip_candidates"][0]["center_seconds"], 15.0)

    def test_review_marker_request_rejects_empty_label(self) -> None:
        with self.assertRaises(ReviewModelError):
            ReviewMarkerRequest("recording", 1000, "")

    def test_review_marker_uses_service_timeline_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            recording_id = "review-2"
            output = service.paths.recordings / "review.mkv"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")
            now = datetime.now(timezone.utc)
            history = RecordingHistoryRepository.from_runtime_paths(service.paths)
            history.register_starting(
                recording_id=recording_id,
                output_path=output,
                container="mkv",
                source="manual",
                created_at=now,
            )
            history.finalize(
                recording_id,
                RecordingResult(
                    RecordingState.COMPLETED,
                    output,
                    0,
                    now,
                    now + timedelta(seconds=10),
                    output.stat().st_size,
                    None,
                    (),
                ),
            )

            event = service.add_review_marker(
                ReviewMarkerRequest(recording_id, 5000, "レビュー確認")
            )

        self.assertEqual(event.event_type, "marker")
        self.assertEqual(event.label, "レビュー確認")
        self.assertEqual(event.source, "manual")

    def test_review_marker_label_can_be_updated_through_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            recording_id = "review-marker-edit"
            output = service.paths.recordings / "review-marker-edit.mkv"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"video")
            now = datetime.now(timezone.utc)
            history = RecordingHistoryRepository.from_runtime_paths(service.paths)
            history.register_starting(
                recording_id=recording_id,
                output_path=output,
                container="mkv",
                source="manual",
                created_at=now,
            )
            history.finalize(
                recording_id,
                RecordingResult(
                    RecordingState.COMPLETED,
                    output,
                    0,
                    now,
                    now + timedelta(seconds=10),
                    output.stat().st_size,
                    None,
                    (),
                ),
            )
            event = service.add_review_marker(
                ReviewMarkerRequest(recording_id, 5000, "修正前")
            )

            updated = service.update_review_marker_label(event.event_id, "修正後")
            model = service.get_review_view_model(recording_id)

        self.assertEqual(updated.label, "修正後")
        self.assertEqual(model.timeline[0].label, "修正後")

    def test_review_view_model_does_not_persist_oauth_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryCredentialStore()
            secret = "refresh-token-secret"
            store.write(YouTubeCredentials("client-id", "client-secret", secret))
            service = RecorderApplicationService(
                user_data_dir=Path(tmp_dir) / "user_data",
                youtube_credential_store=store,
            )
            recording_id = "review-secret"
            output = service.paths.recordings / "review-secret.mp4"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"video")
            now = datetime.now(timezone.utc)
            history = RecordingHistoryRepository.from_runtime_paths(service.paths)
            history.register_starting(
                recording_id=recording_id,
                output_path=output,
                container="mp4",
                source="manual",
                created_at=now,
            )
            history.finalize(
                recording_id,
                RecordingResult(
                    RecordingState.COMPLETED,
                    output,
                    0,
                    now,
                    now + timedelta(seconds=5),
                    output.stat().st_size,
                    None,
                    (),
                ),
            )

            document = service.get_review_view_model(recording_id).to_dict()
            persisted_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in service.paths.root.rglob("*")
                if path.is_file()
            )

        self.assertNotIn(secret, str(document))
        self.assertNotIn(secret, persisted_text)


if __name__ == "__main__":
    unittest.main()
