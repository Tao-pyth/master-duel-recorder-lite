import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from master_duel_recorder_lite.recording_paths import RecordingPathError, create_recording_target
from master_duel_recorder_lite.recording_profile import RecordingProfile
from master_duel_recorder_lite.runtime_paths import default_runtime_paths


class RecordingPathTest(unittest.TestCase):
    def test_target_is_unique_and_under_recordings(self) -> None:
        fixed_time = datetime(2026, 8, 8, 12, 34, 56, 123456, tzinfo=timezone.utc)
        identifiers = iter(
            [
                UUID("00000000-0000-0000-0000-000000000001"),
                UUID("00000000-0000-0000-0000-000000000002"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            first = create_recording_target(
                paths,
                RecordingProfile(),
                clock=lambda: fixed_time,
                uuid_factory=lambda: next(identifiers),
            )
            second = create_recording_target(
                paths,
                RecordingProfile(),
                clock=lambda: fixed_time,
                uuid_factory=lambda: next(identifiers),
            )

        self.assertNotEqual(first.recording_id, second.recording_id)
        self.assertNotEqual(first.path, second.path)
        self.assertTrue(first.path.is_relative_to(paths.recordings.resolve()))
        self.assertEqual(first.path.suffix, ".mkv")
        self.assertIn("20260808T123456_123456Z", first.path.name)

    def test_naive_datetime_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            with self.assertRaises(RecordingPathError):
                create_recording_target(
                    paths,
                    RecordingProfile(),
                    clock=lambda: datetime(2026, 8, 8),
                )


if __name__ == "__main__":
    unittest.main()
