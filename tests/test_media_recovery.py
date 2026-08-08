from datetime import datetime, timezone
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.ffmpeg import CommandResult
from master_duel_recorder_lite.media_recovery import InspectionStatus, MediaRecoveryService
from master_duel_recorder_lite.recording_failure import classify_recording_failure
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository


BASE_TIME = datetime(2026, 8, 8, tzinfo=timezone.utc)
PROBE_JSON = json.dumps(
    {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}], "format": {"duration": "5.0"}}
)


class MediaRecoveryServiceTest(unittest.TestCase):
    def make_failed(self, root: Path) -> tuple[RecordingHistoryRepository, Path]:
        recordings = root / "recordings"
        recordings.mkdir()
        original = recordings / "partial.mkv"
        original.write_bytes(b"original")
        repository = RecordingHistoryRepository(
            database_path=root / "db" / "history.sqlite3",
            recordings_root=recordings,
        )
        repository.register_starting(
            recording_id="failed",
            output_path=original,
            container="mkv",
            source="manual",
            created_at=BASE_TIME,
        )
        repository.mark_interrupted(
            "failed",
            classification=classify_recording_failure(
                error="interrupted",
                returncode=None,
                output_exists=True,
                output_size=8,
                interrupted=True,
            ),
            ended_at=BASE_TIME,
            size_bytes=8,
        )
        return repository, original

    def test_inspect_reads_media_without_modifying_original(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository, original = self.make_failed(Path(tmp_dir))
            before = original.read_bytes()
            service = MediaRecoveryService(
                repository=repository,
                ffmpeg_executable=Path(tmp_dir) / "ffmpeg.exe",
                ffprobe_executable=Path(tmp_dir) / "ffprobe.exe",
                runner=lambda _command, _timeout: CommandResult(0, PROBE_JSON, ""),
            )

            inspection = service.inspect("failed")
            preserved = original.read_bytes()

        self.assertIs(inspection.status, InspectionStatus.VALID)
        self.assertEqual(inspection.duration_seconds, 5.0)
        self.assertEqual(before, preserved)

    def test_repair_creates_valid_separate_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository, original = self.make_failed(Path(tmp_dir))
            before = original.read_bytes()

            def runner(command: tuple[str, ...], _timeout: float) -> CommandResult:
                if "-show_entries" in command:
                    return CommandResult(0, PROBE_JSON, "")
                Path(command[-1]).write_bytes(b"recovered")
                return CommandResult(0, "", "")

            service = MediaRecoveryService(
                repository=repository,
                ffmpeg_executable=Path(tmp_dir) / "ffmpeg.exe",
                ffprobe_executable=Path(tmp_dir) / "ffprobe.exe",
                runner=runner,
            )

            result = service.repair("failed")
            entry = repository.get("failed")
            artifacts = repository.recovery_artifacts("failed")
            consistency = repository.check_consistency()
            preserved = original.read_bytes()

        self.assertTrue(result.succeeded)
        self.assertNotEqual(result.output_path, original)
        self.assertEqual(before, preserved)
        assert entry is not None
        self.assertEqual(entry.recovery_state, "repaired")
        self.assertEqual(artifacts[0].status, "valid")
        self.assertEqual(consistency, ())

    def test_failed_repair_tracks_partial_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository, original = self.make_failed(Path(tmp_dir))

            def runner(command: tuple[str, ...], _timeout: float) -> CommandResult:
                Path(command[-1]).write_bytes(b"partial repair")
                return CommandResult(1, "", "repair failed")

            service = MediaRecoveryService(
                repository=repository,
                ffmpeg_executable=Path(tmp_dir) / "ffmpeg.exe",
                runner=runner,
            )

            result = service.repair("failed")
            artifacts = repository.recovery_artifacts("failed")

        self.assertFalse(result.succeeded)
        self.assertEqual(artifacts[0].kind, "partial")
        self.assertEqual(artifacts[0].status, "failed")

    def test_timeout_is_retryable_and_dry_run_executes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository, _original = self.make_failed(Path(tmp_dir))
            calls = 0

            def timeout_runner(command: tuple[str, ...], timeout: float) -> CommandResult:
                nonlocal calls
                calls += 1
                raise subprocess.TimeoutExpired(command, timeout)

            service = MediaRecoveryService(
                repository=repository,
                ffmpeg_executable=Path(tmp_dir) / "ffmpeg.exe",
                runner=timeout_runner,
            )
            inspection = service.inspect("failed")
            dry_run = service.repair("failed", dry_run=True)

        self.assertIs(inspection.status, InspectionStatus.RETRYABLE)
        self.assertTrue(dry_run.dry_run)
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
