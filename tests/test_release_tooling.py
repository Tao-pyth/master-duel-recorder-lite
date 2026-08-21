import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_windows_exe import (
    EXECUTABLE_NAME,
    GUI_EXECUTABLE_NAME,
    UPDATER_EXECUTABLE_NAME,
    APP_ICON,
    build_command,
    PROJECT_ROOT,
    read_project_version,
    resolve_youtube_oauth_client_asset,
    windows_version_resource,
    windows_version_tuple,
)
from scripts.verify_release_tag import (
    read_package_version,
    verify_project_version,
    verify_release_tag,
)
from scripts.verify_release_assets import (
    ReleaseAssetVerificationError,
    verify_release_assets,
)


class ReleaseToolingTest(unittest.TestCase):
    def test_windows_version_resource_uses_project_version(self) -> None:
        version = read_project_version()
        resource = windows_version_resource(version)
        major, minor, fix = (int(item) for item in version.split("."))

        self.assertEqual(windows_version_tuple(version), (major, minor, fix, 0))
        self.assertIn(f"filevers=({major}, {minor}, {fix}, 0)", resource)
        self.assertIn(f"ProductVersion', '{version}'", resource)
        self.assertIn(EXECUTABLE_NAME, resource)

    def test_build_command_is_onefile_console_without_upx(self) -> None:
        root = Path("project").resolve()
        command = build_command(root, root / "build" / "version.txt")

        self.assertIn("--onefile", command)
        self.assertIn("--console", command)
        self.assertIn("--noupx", command)
        self.assertIn(str(root / "src"), command)
        self.assertNotIn("--windowed", command)
        self.assertEqual(command[command.index("--icon") + 1], str(root / APP_ICON))

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
        self.assertEqual(command[command.index("--icon") + 1], str(root / APP_ICON))

    def test_build_command_bundles_native_helper_and_license_notice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            helper = root / "native" / "audio_loopback" / "bin" / "mdrl-audio-loopback.exe"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(b"helper")
            notice = root / "THIRD_PARTY_NOTICES.md"
            notice.write_text("notice", encoding="utf-8")
            command = build_command(root, root / "build" / "version.txt")

        helper_option = command[command.index("--add-binary") + 1]
        notice_option = command[command.index("--add-data") + 1]
        self.assertEqual(helper_option, f"{helper};native")
        self.assertEqual(notice_option, f"{notice};.")

    def test_build_command_can_bundle_youtube_oauth_client_asset(self) -> None:
        root = Path("project").resolve()
        asset = root / "build" / "youtube-oauth-client.json"

        command = build_command(
            root,
            root / "build" / "version.txt",
            youtube_oauth_client_asset=asset,
        )

        self.assertIn("--add-data", command)
        self.assertIn(f"{asset};assets", command)

    def test_gui_build_command_can_bundle_updater_executable(self) -> None:
        root = Path("project").resolve()
        updater = root / "dist" / UPDATER_EXECUTABLE_NAME

        command = build_command(
            root,
            root / "build" / "gui-version.txt",
            executable_name=GUI_EXECUTABLE_NAME,
            entrypoint="mdrl_gui_entry.py",
            windowed=True,
            extra_binaries=((updater, "."),),
        )

        self.assertIn("--add-binary", command)
        self.assertIn(f"{updater};.", command)

    def test_updater_build_command_is_console_entrypoint(self) -> None:
        root = Path("project").resolve()
        command = build_command(
            root,
            root / "build" / "updater-version.txt",
            executable_name=UPDATER_EXECUTABLE_NAME,
            entrypoint="mdrl_updater_entry.py",
            windowed=False,
        )

        self.assertIn("--console", command)
        self.assertNotIn("--windowed", command)
        self.assertIn(str(root / "packaging" / "mdrl_updater_entry.py"), command)

    def test_release_oauth_client_asset_can_be_generated_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            build_root = root / "build"
            with patch.dict(
                "os.environ",
                {
                    "MDRL_YOUTUBE_OAUTH_CLIENT_ID": "client-id",
                    "MDRL_YOUTUBE_OAUTH_CLIENT_SECRET": "client-secret",
                },
                clear=True,
            ):
                asset = resolve_youtube_oauth_client_asset(
                    root,
                    build_root,
                    require=True,
                )

            self.assertIsNotNone(asset)
            content = asset.read_text(encoding="utf-8")
            self.assertIn("client-id", content)
            self.assertIn("client-secret", content)

    def test_release_oauth_client_asset_requires_secret_for_release_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            build_root = root / "build"
            with patch.dict(
                "os.environ",
                {"MDRL_YOUTUBE_OAUTH_CLIENT_ID": "client-id"},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "client_secret"):
                    resolve_youtube_oauth_client_asset(
                        root,
                        build_root,
                        require=True,
                    )

    def test_release_oauth_client_asset_rejects_token_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            asset = root / "assets" / "youtube-oauth-client.json"
            asset.parent.mkdir()
            asset.write_text(
                '{"installed":{"client_id":"client","client_secret":"secret","refresh_token":"token"}}',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "token"):
                resolve_youtube_oauth_client_asset(root, root / "build", require=True)

    def test_release_oauth_client_asset_is_required_for_release_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "client_id"):
                    resolve_youtube_oauth_client_asset(
                        root,
                        root / "build",
                        require=True,
                    )

    def test_release_tag_matches_both_version_sources(self) -> None:
        version = verify_project_version()
        self.assertEqual(read_package_version(), version)
        self.assertEqual(verify_release_tag(f"v{version}"), version)

    def test_release_tag_script_supports_direct_execution(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/verify_release_tag.py", f"v{verify_project_version()}"],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), verify_project_version())

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

    def test_release_asset_verification_accepts_matching_checksums(self) -> None:
        release = {
            "assets": [
                {
                    "name": "master-duel-recorder-lite.exe",
                    "digest": "sha256:" + "1" * 64,
                    "browser_download_url": "https://example.invalid/cli.exe",
                },
                {
                    "name": "master-duel-recorder-lite.exe.sha256",
                    "digest": "sha256:" + "2" * 64,
                    "browser_download_url": "https://example.invalid/cli.sha256",
                },
                {
                    "name": "master-duel-recorder-lite-gui.exe",
                    "digest": "sha256:" + "a" * 64,
                    "browser_download_url": "https://example.invalid/gui.exe",
                },
                {
                    "name": "master-duel-recorder-lite-gui.exe.sha256",
                    "digest": "sha256:" + "b" * 64,
                    "browser_download_url": "https://example.invalid/gui.sha256",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe",
                    "digest": "sha256:" + "c" * 64,
                    "browser_download_url": "https://example.invalid/updater.exe",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe.sha256",
                    "digest": "sha256:" + "d" * 64,
                    "browser_download_url": "https://example.invalid/updater.sha256",
                },
            ]
        }

        def read_bytes(url: str, maximum: int) -> bytes:
            self.assertGreaterEqual(maximum, 4096)
            if url.endswith("/v1.4.1"):
                return __import__("json").dumps(release).encode()
            if "cli.sha256" in url:
                return (("1" * 64) + "  master-duel-recorder-lite.exe\n").encode()
            if "gui.sha256" in url:
                return (("a" * 64) + "  master-duel-recorder-lite-gui.exe\n").encode()
            if "updater.sha256" in url:
                return (
                    ("c" * 64) + "  master-duel-recorder-lite-updater.exe\n"
                ).encode()
            raise AssertionError(url)

        with patch("scripts.verify_release_assets._read_bytes", read_bytes):
            self.assertEqual(
                verify_release_assets("v1.4.1"),
                [
                    "master-duel-recorder-lite.exe: " + "1" * 64,
                    "master-duel-recorder-lite-gui.exe: " + "a" * 64,
                    "master-duel-recorder-lite-updater.exe: " + "c" * 64,
                ],
            )

    def test_release_asset_verification_rejects_mismatched_checksum(self) -> None:
        release = {
            "assets": [
                {
                    "name": "master-duel-recorder-lite.exe",
                    "digest": "sha256:" + "1" * 64,
                    "browser_download_url": "https://example.invalid/cli.exe",
                },
                {
                    "name": "master-duel-recorder-lite.exe.sha256",
                    "digest": "sha256:" + "2" * 64,
                    "browser_download_url": "https://example.invalid/cli.sha256",
                },
                {
                    "name": "master-duel-recorder-lite-gui.exe",
                    "digest": "sha256:" + "a" * 64,
                    "browser_download_url": "https://example.invalid/gui.exe",
                },
                {
                    "name": "master-duel-recorder-lite-gui.exe.sha256",
                    "digest": "sha256:" + "b" * 64,
                    "browser_download_url": "https://example.invalid/gui.sha256",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe",
                    "digest": "sha256:" + "c" * 64,
                    "browser_download_url": "https://example.invalid/updater.exe",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe.sha256",
                    "digest": "sha256:" + "d" * 64,
                    "browser_download_url": "https://example.invalid/updater.sha256",
                },
            ]
        }

        def read_bytes(url: str, _maximum: int) -> bytes:
            if url.endswith("/v1.4.1"):
                return __import__("json").dumps(release).encode()
            if "cli.sha256" in url:
                return (("0" * 64) + "  master-duel-recorder-lite.exe\n").encode()
            if "gui.sha256" in url:
                return (("a" * 64) + "  master-duel-recorder-lite-gui.exe\n").encode()
            if "updater.sha256" in url:
                return (
                    ("c" * 64) + "  master-duel-recorder-lite-updater.exe\n"
                ).encode()
            raise AssertionError(url)

        with patch("scripts.verify_release_assets._read_bytes", read_bytes):
            with self.assertRaisesRegex(ReleaseAssetVerificationError, "一致しません"):
                verify_release_assets("v1.4.1")


if __name__ == "__main__":
    unittest.main()
