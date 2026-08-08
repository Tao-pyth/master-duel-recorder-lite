from datetime import datetime, timezone
import json
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.ffmpeg import CommandResult
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import RuntimePaths, default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_export import UploadExporter
from master_duel_recorder_lite.upload_manifest import UploadManifestWriter
from master_duel_recorder_lite.upload_media import UploadMediaValidator
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_preparation import UploadPreparationService
from master_duel_recorder_lite.upload_queue import UploadQueueState, UploadQueueStore


VALID_MKV = json.dumps(
    {
        "streams": [{"codec_type": "video"}],
        "format": {"format_name": "matroska,webm", "duration": "5.0"},
    }
)
VALID_MP4 = VALID_MKV.replace("matroska,webm", "mov,mp4")


class UploadPreparationServiceTest(unittest.TestCase):
    def make_service(
        self, root: Path
    ) -> tuple[RuntimePaths, RecordingHistoryRepository, UploadQueueStore, UploadPreparationService]:
        paths = default_runtime_paths(user_data_dir=root / "user_data")
        ensure_runtime_dirs(paths)
        repository = RecordingHistoryRepository.from_runtime_paths(paths)
        queue = UploadQueueStore(paths)

        def probe_runner(command: tuple[str, ...], _timeout: float) -> CommandResult:
            path = Path(command[-1])
            if "invalid" in path.name:
                return CommandResult(1, "", "corrupt")
            return CommandResult(0, VALID_MP4 if path.suffix == ".mp4" else VALID_MKV, "")

        validator = UploadMediaValidator(
            ffprobe_executable=root / "ffprobe.exe",
            runner=probe_runner,
        )

        def export_runner(command: tuple[str, ...], _timeout: float) -> CommandResult:
            Path(command[-1]).write_bytes(b"exported")
            source = Path(command[command.index("-i") + 1])
            if "exportfail" in source.name:
                return CommandResult(1, "", "injected export failure")
            return CommandResult(0, "", "")

        exporter = UploadExporter(
            paths=paths,
            ffmpeg_executable=root / "ffmpeg.exe",
            validator=validator,
            runner=export_runner,
        )
        service = UploadPreparationService(
            paths=paths,
            repository=repository,
            queue=queue,
            exporter=exporter,
            manifest_writer=UploadManifestWriter(paths),
        )
        return paths, repository, queue, service

    def add_completed(
        self,
        paths: RuntimePaths,
        repository: RecordingHistoryRepository,
        recording_id: str,
        filename: str,
    ) -> None:
        source = paths.recordings / filename
        source.write_bytes(b"source")
        now = datetime.now(timezone.utc)
        repository.register_starting(
            recording_id=recording_id,
            output_path=source,
            container="mkv",
            source="manual",
            created_at=now,
        )
        repository.mark_recording(recording_id, started_at=now)
        repository.finalize(
            recording_id,
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

    def test_full_offline_flow_creates_private_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths, repository, queue, service = self.make_service(Path(tmp_dir))
            self.add_completed(paths, repository, "recording", "valid.mkv")
            item = service.enqueue(
                recording_id="recording",
                metadata=UploadMetadata("title"),
            )

            result = service.process(item.queue_id)[0]
            stored = queue.get(item.queue_id)
            assert result.manifest_path is not None
            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertTrue(result.succeeded)
        assert stored is not None
        self.assertIs(stored.state, UploadQueueState.COMPLETED)
        self.assertEqual(manifest["metadata"]["privacy"], "private")

    def test_one_invalid_item_does_not_prevent_other_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths, repository, queue, service = self.make_service(Path(tmp_dir))
            self.add_completed(paths, repository, "invalid", "invalid.mkv")
            self.add_completed(paths, repository, "valid", "valid.mkv")
            invalid = service.enqueue(recording_id="invalid", metadata=UploadMetadata("bad"))
            valid = service.enqueue(recording_id="valid", metadata=UploadMetadata("good"))

            results = service.process()
            invalid_item = queue.get(invalid.queue_id)
            valid_item = queue.get(valid.queue_id)

        self.assertEqual(len(results), 2)
        assert invalid_item is not None and valid_item is not None
        self.assertIs(invalid_item.state, UploadQueueState.FAILED)
        self.assertIs(valid_item.state, UploadQueueState.COMPLETED)

    def test_cancelled_item_is_not_processed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths, repository, _queue, service = self.make_service(Path(tmp_dir))
            self.add_completed(paths, repository, "recording", "valid.mkv")
            item = service.enqueue(recording_id="recording", metadata=UploadMetadata("title"))

            cancelled = service.cancel(item.queue_id)
            results = service.process()

        self.assertIs(cancelled.state, UploadQueueState.CANCELLED)
        self.assertEqual(results, ())

    def test_failed_export_tracks_partial_path_in_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths, repository, queue, service = self.make_service(Path(tmp_dir))
            self.add_completed(paths, repository, "exportfail", "exportfail.mkv")
            item = service.enqueue(
                recording_id="exportfail",
                metadata=UploadMetadata("title"),
            )

            result = service.process(item.queue_id)[0]
            stored = queue.get(item.queue_id)
            assert stored is not None and stored.export_path is not None
            partial_exists = (paths.root / stored.export_path).is_file()

        self.assertIs(result.state, UploadQueueState.FAILED)
        self.assertTrue(partial_exists)

    def test_process_reports_item_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths, repository, _queue, service = self.make_service(Path(tmp_dir))
            self.add_completed(paths, repository, "progress", "valid.mkv")
            item = service.enqueue(
                recording_id="progress",
                metadata=UploadMetadata("progress"),
            )
            reported: list[str] = []

            results = service.process(progress=lambda current: reported.append(current.queue_id))

        self.assertEqual(reported, [item.queue_id])
        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
