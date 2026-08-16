import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.data_location import (
    DataLocationError,
    load_runtime_root_pointer,
    relocate_runtime_data,
)
from master_duel_recorder_lite.history_database import HISTORY_DATABASE_NAME, initialize_history_database
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


class DataLocationTest(unittest.TestCase):
    def test_runtime_data_is_copied_and_pointer_is_switched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            paths = default_runtime_paths(user_data_dir=base / "source")
            ensure_runtime_dirs(paths)
            initialize_history_database(paths.db / HISTORY_DATABASE_NAME)
            recording = paths.recordings / "keep.mkv"
            recording.write_bytes(b"video")
            target = base / "selected" / "mdrl-data"
            pointer = base / "runtime-root.json"
            with patch(
                "master_duel_recorder_lite.data_location.runtime_root_pointer_path",
                return_value=pointer,
            ):
                result = relocate_runtime_data(paths, target)
                selected = load_runtime_root_pointer()
            self.assertEqual(result.destination, target.resolve())
            self.assertEqual((target / "data" / "recordings" / "keep.mkv").read_bytes(), b"video")
            self.assertTrue(recording.is_file())
            self.assertEqual(selected, target.resolve())

    def test_parent_or_child_destination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "source")
            ensure_runtime_dirs(paths)
            with self.assertRaisesRegex(DataLocationError, "親子関係"):
                relocate_runtime_data(paths, paths.root / "moved")


if __name__ == "__main__":
    unittest.main()
