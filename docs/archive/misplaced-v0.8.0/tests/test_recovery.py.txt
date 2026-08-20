from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_lock import RecordingLock
from master_duel_recorder_lite.recording_state_store import RecordingStateStore
from master_duel_recorder_lite.recovery import InterruptedDetectionKind, RecoveryManager
from master_duel_recorder_lite.runtime_paths import RuntimePaths, default_runtime_paths, ensure_runtime_dirs


BASE_TIME = datetime(2026, 8, 8, tzinfo=timezone.utc)


class RecoveryManagerTest(unittest.TestCase):
    def make_active_recording(
        self, root: Path
    ) -> tuple[RuntimePaths, RecordingHistoryRepository, Path]:
        paths = default_runtime_paths(user_data_dir=root / "user_data")
        ensure_runtime_dirs(paths)
        output = paths.recordings / "partial.mkv"
        output.write_bytes(b"partial")
        repository = RecordingHistoryRepository.from_runtime_paths(paths)
        repository.register_starting(
            recording_id="recording",
            output_path=output,
            container="mkv",
            source="manual",
            created_at=BASE_TIME,
        )
        repository.mark_recording("recording", started_at=BASE_TIME)
        return paths, repository, output

    def test_dead_process_and_free_lock_are_marked_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths, repository, output = self.make_active_recording(Path(tmp_dir))
            store = RecordingStateStore(paths)
            store.save(
                recording_id="recording",
                state="recording",
                source="manual",
                output_path=output,
                started_at=BASE_TIME,
                pid=99999,
            )
            manager = RecoveryManager(
                paths=paths,
                repository=repository,
                state_store=store,
                process_checker=lambda _pid: False,
            )

            detections = manager.detect_interrupted()
            entry = repository.get("recording")

        self.assertEqual(detections[0].kind, InterruptedDetectionKind.INTERRUPTED)
        assert entry is not None
        self.assertEqual(entry.state, "failed")
        self.assertEqual(entry.failure_code, "application_interrupted")

    def test_held_recording_lock_prevents_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths, repository, _output = self.make_active_recording(Path(tmp_dir))
            lock = RecordingLock.acquire(paths.data / "recording.lock", recording_id="active")
            try:
                manager = RecoveryManager(
                    paths=paths,
                    repository=repository,
                    process_checker=lambda _pid: False,
                )
                detections = manager.detect_interrupted()
                entry = repository.get("recording")
            finally:
                lock.release()

        self.assertEqual(detections[0].kind, InterruptedDetectionKind.ACTIVE)
        assert entry is not None
        self.assertEqual(entry.state, "recording")

    def test_live_pid_without_lock_is_left_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths, repository, output = self.make_active_recording(Path(tmp_dir))
            store = RecordingStateStore(paths)
            store.save(
                recording_id="recording",
                state="recording",
                source="manual",
                output_path=output,
                started_at=BASE_TIME,
                pid=123,
            )
            manager = RecoveryManager(
                paths=paths,
                repository=repository,
                state_store=store,
                process_checker=lambda _pid: True,
            )

            detections = manager.detect_interrupted()
            entry = repository.get("recording")

        self.assertEqual(detections[0].kind, InterruptedDetectionKind.ACTIVE)
        assert entry is not None
        self.assertEqual(entry.state, "recording")

    def test_completed_history_is_not_considered_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            repository = RecordingHistoryRepository.from_runtime_paths(paths)
            manager = RecoveryManager(paths=paths, repository=repository)

            self.assertEqual(manager.detect_interrupted(), ())


if __name__ == "__main__":
    unittest.main()
