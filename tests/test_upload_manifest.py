from datetime import datetime, timezone
import json
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_export import UploadExportResult, UploadExportStatus
from master_duel_recorder_lite.upload_manifest import (
    UploadManifestError,
    UploadManifestWriter,
    validate_upload_manifest,
)
from master_duel_recorder_lite.upload_media import MediaValidationStatus, UploadMediaValidation
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_queue import UploadQueueStore


class UploadManifestTest(unittest.TestCase):
    def test_manifest_uses_relative_hashed_files_and_allowed_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            source = paths.recordings / "source.mkv"
            export = paths.exports / "recording" / "queue.mp4"
            source.write_bytes(b"source")
            export.parent.mkdir(parents=True)
            export.write_bytes(b"export")
            now = datetime.now(timezone.utc)
            repository = RecordingHistoryRepository.from_runtime_paths(paths)
            repository.register_starting(
                recording_id="recording",
                output_path=source,
                container="mkv",
                source="manual",
                created_at=now,
            )
            repository.mark_recording("recording", started_at=now)
            repository.finalize(
                "recording",
                RecordingResult(
                    RecordingState.COMPLETED,
                    source,
                    0,
                    now,
                    now,
                    source.stat().st_size,
                    None,
                    (),
                ),
            )
            history = repository.get("recording")
            assert history is not None
            item = UploadQueueStore(paths).enqueue(
                recording_id="recording",
                metadata=UploadMetadata("title"),
            )
            source_validation = UploadMediaValidation(
                MediaValidationStatus.VALID,
                source,
                "matroska,webm",
                5.0,
                ("video", "audio"),
                (),
                (),
            )
            export_validation = UploadMediaValidation(
                MediaValidationStatus.VALID,
                export,
                "mov,mp4",
                5.0,
                ("video", "audio"),
                (),
                (),
            )
            result = UploadExportResult(
                UploadExportStatus.COMPLETED,
                source,
                export,
                None,
                source_validation,
                export_validation,
                "ok",
                "ok",
            )

            manifest_path = UploadManifestWriter(paths).write(
                item=item,
                history=history,
                export=result,
            )
            document = json.loads(manifest_path.read_text(encoding="utf-8"))

        validate_upload_manifest(document)
        self.assertFalse(Path(document["source"]["path"]).is_absolute())
        self.assertFalse(Path(document["export"]["path"]).is_absolute())
        serialized = json.dumps(document)
        for forbidden in ("access_token", "client_secret", "api_key", "oauth"):
            self.assertNotIn(forbidden, serialized)

    def test_unknown_manifest_field_is_rejected(self) -> None:
        with self.assertRaises(UploadManifestError):
            validate_upload_manifest({"schema_version": 1, "access_token": "secret"})


if __name__ == "__main__":
    unittest.main()
