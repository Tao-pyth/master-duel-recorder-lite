import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.build_windows_exe import (
    EXECUTABLE_NAME,
    GUI_EXECUTABLE_NAME,
    build_command,
    PROJECT_ROOT,
    read_project_version,
    windows_version_resource,
    windows_version_tuple,
)
from scripts.verify_release_tag import (
    read_package_version,
    verify_project_version,
    verify_release_tag,
)


class ReleaseToolingTest(unittest.TestCase):
    def test_windows_version_resource_uses_project_version(self) -> None:
        version = read_project_version()
        resource = windows_version_resource(version)

        self.assertEqual(version, "0.25.0")
        self.assertEqual(windows_version_tuple(version), (0, 25, 0, 0))
        self.assertIn("filevers=(0, 25, 0, 0)", resource)
        self.assertIn("ProductVersion', '0.25.0'", resource)
        self.assertIn(EXECUTABLE_NAME, resource)

    def test_build_command_is_onefile_console_without_upx(self) -> None:
        root = Path("project").resolve()
        command = build_command(root, root / "build" / "version.txt")

        self.assertIn("--onefile", command)
        self.assertIn("--console", command)
        self.assertIn("--noupx", command)
        self.assertIn(str(root / "src"), command)
        self.assertNotIn("--windowed", command)

    def test_gui_build_command_is_onefile_windowed(self) -> None:
        root = Path("project").resolve()
        command = build_command(
            root,
            root / "build" / "gui-version.txt",
            executable_name=GUI_EXECUTABLE_NAME,
            entrypoint="mdrl_gui_entry.py",
            windowed=True,
        )

        self.assertIn("--onefile", command)
        self.assertIn("--windowed", command)
        self.assertNotIn("--console", command)
        self.assertIn(str(root / "packaging" / "mdrl_gui_entry.py"), command)

    def test_release_tag_matches_both_version_sources(self) -> None:
        self.assertEqual(read_package_version(), "0.25.0")
        self.assertEqual(verify_project_version(), "0.25.0")
        self.assertEqual(verify_release_tag("v0.25.0"), "0.25.0")

    def test_release_tag_script_supports_direct_execution(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/verify_release_tag.py", "v0.25.0"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "0.25.0")

    def test_release_tag_mismatch_fails(self) -> None:
        with self.assertRaises(ValueError):
            verify_release_tag("v0.9.2")

    def test_invalid_project_version_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "pyproject.toml").write_text(
                '[project]\nversion = "0.9"\n',
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                read_project_version(root)


if __name__ == "__main__":
    unittest.main()
