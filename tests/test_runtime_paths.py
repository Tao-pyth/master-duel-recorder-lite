import os
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


class RuntimePathsTest(unittest.TestCase):
    def test_default_runtime_paths_are_under_user_data(self) -> None:
        paths = default_runtime_paths(project_root=Path("project").resolve())

        self.assertEqual(paths.root, Path("project").resolve() / "user_data")
        self.assertEqual(paths.config, paths.root / "config")
        self.assertEqual(paths.data, paths.root / "data")
        self.assertEqual(paths.logs, paths.root / "logs")
        self.assertEqual(paths.db, paths.root / "data" / "db")
        self.assertEqual(paths.recordings, paths.root / "data" / "recordings")
        self.assertEqual(paths.screenshots, paths.root / "data" / "screenshots")
        self.assertEqual(paths.exports, paths.root / "data" / "exports")
        self.assertEqual(paths.queue, paths.root / "data" / "queue")

    def test_env_override_changes_runtime_root(self) -> None:
        original = os.environ.get("MDRL_USER_DATA_DIR")
        with tempfile.TemporaryDirectory() as tmp_dir:
            os.environ["MDRL_USER_DATA_DIR"] = tmp_dir
            try:
                paths = default_runtime_paths(project_root=Path("ignored"))
            finally:
                if original is None:
                    os.environ.pop("MDRL_USER_DATA_DIR", None)
                else:
                    os.environ["MDRL_USER_DATA_DIR"] = original

        self.assertEqual(paths.root, Path(tmp_dir).resolve())

    def test_ensure_runtime_dirs_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            ensure_runtime_dirs(paths)

            self.assertTrue(paths.config.is_dir())
            self.assertTrue(paths.db.is_dir())
            self.assertTrue(paths.recordings.is_dir())
            self.assertTrue(paths.queue.is_dir())


if __name__ == "__main__":
    unittest.main()
