import re
import tomllib
import unittest
from pathlib import Path

from master_duel_recorder_lite import __version__


class VersionTest(unittest.TestCase):
    def test_project_and_package_versions_match(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        with (project_root / "pyproject.toml").open("rb") as pyproject_file:
            project_version = tomllib.load(pyproject_file)["project"]["version"]

        self.assertEqual(project_version, __version__)
        self.assertRegex(project_version, re.compile(r"^\d+\.\d+\.\d+$"))


if __name__ == "__main__":
    unittest.main()
