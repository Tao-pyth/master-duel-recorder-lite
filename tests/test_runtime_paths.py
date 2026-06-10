import unittest
from pathlib import Path

from master_duel_recorder_lite.runtime_paths import default_runtime_paths


class RuntimePathsTest(unittest.TestCase):
    def test_default_runtime_paths_are_under_user_data(self) -> None:
        paths = default_runtime_paths(Path("project"))

        self.assertEqual(paths.root, Path("project") / "user_data")
        self.assertEqual(paths.config, paths.root / "config")
        self.assertEqual(paths.data, paths.root / "data")
        self.assertEqual(paths.logs, paths.root / "logs")
        self.assertEqual(paths.recordings, paths.root / "recordings")
        self.assertEqual(paths.queue, paths.root / "queue")


if __name__ == "__main__":
    unittest.main()
