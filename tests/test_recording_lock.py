import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.recording_lock import RecordingBusyError, RecordingLock


class RecordingLockTest(unittest.TestCase):
    def test_second_lock_is_rejected_until_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "recording.lock"
            first = RecordingLock.acquire(path, recording_id="first")
            try:
                with self.assertRaises(RecordingBusyError):
                    RecordingLock.acquire(path, recording_id="second")
            finally:
                first.release()

            second = RecordingLock.acquire(path, recording_id="second")
            second.release()

        self.assertTrue(first.released)
        self.assertTrue(second.released)

    def test_metadata_remains_without_deleting_lock_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "recording.lock"
            lock = RecordingLock.acquire(path, recording_id="recording-id")
            lock.release()

            metadata = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(metadata["recording_id"], "recording-id")
        self.assertIsInstance(metadata["pid"], int)

    def test_release_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock = RecordingLock.acquire(Path(tmp_dir) / "recording.lock", recording_id="id")
            lock.release()
            lock.release()

        self.assertTrue(lock.released)

    def test_metadata_write_failure_releases_operating_system_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "recording.lock"
            with patch(
                "master_duel_recorder_lite.recording_lock.os.fsync",
                side_effect=OSError("fsync failed"),
            ):
                with self.assertRaises(OSError):
                    RecordingLock.acquire(path, recording_id="failed")

            recovered = RecordingLock.acquire(path, recording_id="recovered")
            recovered.release()

        self.assertTrue(recovered.released)


if __name__ == "__main__":
    unittest.main()
