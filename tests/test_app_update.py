import hashlib
from io import BytesIO
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.app_update import (
    AppUpdateError,
    AppUpdateService,
    launch_update_after_exit,
)


class _Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class AppUpdateServiceTest(unittest.TestCase):
    def test_new_stable_release_is_detected_and_verified(self) -> None:
        executable = b"signed-by-published-hash"
        digest = hashlib.sha256(executable).hexdigest()
        release = {
            "tag_name": "v1.0.1",
            "name": "V1.0.1",
            "html_url": "https://example.invalid/release",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "master-duel-recorder-lite-gui.exe",
                    "size": len(executable),
                    "browser_download_url": "https://example.invalid/app.exe",
                },
                {
                    "name": "master-duel-recorder-lite-gui.exe.sha256",
                    "size": 80,
                    "browser_download_url": "https://example.invalid/app.sha256",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe",
                    "size": 8,
                    "browser_download_url": "https://example.invalid/updater.exe",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe.sha256",
                    "size": 80,
                    "browser_download_url": "https://example.invalid/updater.sha256",
                },
            ],
        }

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            self.assertEqual(request.headers["Cache-control"], "no-cache")
            url = request.full_url
            if "releases?per_page=20" in url:
                return _Response(json.dumps([release]).encode())
            if url.endswith("sha256"):
                return _Response(f"{digest}  app.exe\n".encode())
            return _Response(executable)

        service = AppUpdateService(opener=opener)
        result = service.check("1.0.0")
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "update.exe"
            service.download(result.release, target)  # type: ignore[arg-type]
            self.assertEqual(target.read_bytes(), executable)
        self.assertTrue(result.available)
        self.assertEqual(result.release.version, "1.0.1")  # type: ignore[union-attr]

    def test_same_release_is_not_available(self) -> None:
        release = {
            "tag_name": "v1.0.0",
            "draft": False,
            "prerelease": False,
            "assets": [],
        }
        service = AppUpdateService(
            opener=lambda _request, timeout: _Response(json.dumps([release]).encode())
        )
        self.assertFalse(service.check("1.0.0").available)

    def test_latest_release_without_assets_is_skipped_for_distributable_release(self) -> None:
        executable = b"distributed-exe"
        digest = hashlib.sha256(executable).hexdigest()
        source_only_release = {
            "tag_name": "v1.0.2",
            "name": "V1.0.2",
            "html_url": "https://example.invalid/source-only",
            "draft": False,
            "prerelease": False,
            "assets": [],
        }
        distributable_release = {
            "tag_name": "v1.0.1",
            "name": "V1.0.1",
            "html_url": "https://example.invalid/release",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "master-duel-recorder-lite-gui.exe",
                    "size": len(executable),
                    "browser_download_url": "https://example.invalid/app.exe",
                },
                {
                    "name": "master-duel-recorder-lite-gui.exe.sha256",
                    "size": 80,
                    "browser_download_url": "https://example.invalid/app.sha256",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe",
                    "size": 8,
                    "browser_download_url": "https://example.invalid/updater.exe",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe.sha256",
                    "size": 80,
                    "browser_download_url": "https://example.invalid/updater.sha256",
                },
            ],
        }

        def opener(request, timeout):
            if "releases?per_page=20" in request.full_url:
                return _Response(
                    json.dumps([source_only_release, distributable_release]).encode()
                )
            if request.full_url.endswith("sha256"):
                return _Response(f"{digest}  app.exe\n".encode())
            return _Response(executable)

        service = AppUpdateService(opener=opener)
        result = service.check("1.0.0")

        self.assertTrue(result.available)
        self.assertEqual(result.release.version, "1.0.1")  # type: ignore[union-attr]

    def test_only_newer_source_release_is_not_an_update_error(self) -> None:
        source_only_release = {
            "tag_name": "v1.0.1",
            "name": "V1.0.1",
            "html_url": "https://example.invalid/source-only",
            "draft": False,
            "prerelease": False,
            "assets": [],
        }

        service = AppUpdateService(
            opener=lambda _request, timeout: _Response(
                json.dumps([source_only_release]).encode()
            )
        )

        result = service.check("1.0.0")

        self.assertFalse(result.available)

    def test_malformed_release_entries_do_not_block_valid_release(self) -> None:
        executable = b"distributed-exe"
        bad_tag_release = {
            "tag_name": "release-candidate",
            "draft": False,
            "prerelease": False,
            "assets": [],
        }
        bad_url_release = {
            "tag_name": "v1.0.2",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "master-duel-recorder-lite-gui.exe",
                    "size": len(executable),
                    "browser_download_url": "http://example.invalid/app.exe",
                },
                {
                    "name": "master-duel-recorder-lite-gui.exe.sha256",
                    "size": 80,
                    "browser_download_url": "https://example.invalid/app.sha256",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe",
                    "size": 8,
                    "browser_download_url": "https://example.invalid/updater.exe",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe.sha256",
                    "size": 80,
                    "browser_download_url": "https://example.invalid/updater.sha256",
                },
            ],
        }
        valid_release = {
            "tag_name": "v1.0.1",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": "master-duel-recorder-lite-gui.exe",
                    "size": len(executable),
                    "browser_download_url": "https://example.invalid/app.exe",
                },
                {
                    "name": "master-duel-recorder-lite-gui.exe.sha256",
                    "size": 80,
                    "browser_download_url": "https://example.invalid/app.sha256",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe",
                    "size": 8,
                    "browser_download_url": "https://example.invalid/updater.exe",
                },
                {
                    "name": "master-duel-recorder-lite-updater.exe.sha256",
                    "size": 80,
                    "browser_download_url": "https://example.invalid/updater.sha256",
                },
            ],
        }

        service = AppUpdateService(
            opener=lambda _request, timeout: _Response(
                json.dumps([bad_tag_release, bad_url_release, valid_release]).encode()
            )
        )

        result = service.check("1.0.0")

        self.assertTrue(result.available)
        self.assertEqual(result.release.version, "1.0.1")  # type: ignore[union-attr]

    def test_hash_mismatch_is_rejected(self) -> None:
        service = AppUpdateService(
            opener=lambda request, timeout: _Response(
                ("0" * 64).encode() if request.full_url.endswith("sha256") else b"exe"
            )
        )
        release = type(
            "Release",
            (),
            {
                "checksum_url": "https://example.invalid/sha256",
                "executable_url": "https://example.invalid/exe",
                "size_bytes": 3,
            },
        )()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaisesRegex(AppUpdateError, "SHA-256"):
                service.download(release, Path(tmp_dir) / "update.exe")

    def test_download_and_verify_runs_gui_smoke_before_accepting_update(self) -> None:
        executable = b"verified-exe"
        digest = hashlib.sha256(executable).hexdigest()
        release = type(
            "Release",
            (),
            {
                "checksum_url": "https://example.invalid/sha256",
                "executable_url": "https://example.invalid/exe",
                "size_bytes": len(executable),
                "version": "1.4.2",
            },
        )()
        smoke_calls: list[list[str]] = []

        def opener(request, timeout):
            if request.full_url.endswith("sha256"):
                return _Response(f"{digest}  app.exe\n".encode())
            return _Response(executable)

        def runner(args, **_kwargs):
            smoke_calls.append(list(args))
            output = Path(args[args.index("--smoke-output") + 1])
            output.write_text(
                json.dumps(
                    {
                        "version": "1.4.2",
                        "runtime_data": str(
                            output.parent / "local-app-data" / "MasterDuelRecorderLite"
                        ),
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args, 0, "", "")

        service = AppUpdateService(opener=opener, process_runner=runner)
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = service.download_and_verify(release, Path(tmp_dir) / "update.exe")
            self.assertEqual(target.read_bytes(), executable)

        self.assertEqual(len(smoke_calls), 1)

    def test_download_and_verify_rejects_smoke_without_result(self) -> None:
        executable = b"broken-exe"
        digest = hashlib.sha256(executable).hexdigest()
        release = type(
            "Release",
            (),
            {
                "checksum_url": "https://example.invalid/sha256",
                "executable_url": "https://example.invalid/exe",
                "size_bytes": len(executable),
                "version": "1.4.2",
            },
        )()

        def opener(request, timeout):
            if request.full_url.endswith("sha256"):
                return _Response(f"{digest}  app.exe\n".encode())
            return _Response(executable)

        def runner(args, **_kwargs):
            return subprocess.CompletedProcess(args, 0, "", "")

        service = AppUpdateService(opener=opener, process_runner=runner)
        with tempfile.TemporaryDirectory() as tmp_dir:
            with self.assertRaisesRegex(AppUpdateError, "起動検証結果"):
                service.download_and_verify(release, Path(tmp_dir) / "update.exe")

    def test_launch_update_after_exit_uses_bundled_updater_not_powershell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            current = root / "app" / "master-duel-recorder-lite-gui.exe"
            downloaded = root / "data" / "updates" / "mdrl-gui-1.4.3.exe"
            bundled = root / "bundle" / "master-duel-recorder-lite-updater.exe"
            current.parent.mkdir(parents=True)
            downloaded.parent.mkdir(parents=True)
            bundled.parent.mkdir(parents=True)
            current.write_bytes(b"current")
            downloaded.write_bytes(b"candidate")
            bundled.write_bytes(b"updater")
            launches: list[tuple[str, ...]] = []

            class _Process:
                pass

            def popen(args, **_kwargs):
                launches.append(tuple(str(item) for item in args))
                return _Process()

            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(current)),
                patch.object(sys, "_MEIPASS", str(bundled.parent), create=True),
                patch("subprocess.Popen", popen),
            ):
                updater = launch_update_after_exit(
                    downloaded,
                    expected_version="1.4.3",
                )

            self.assertEqual(updater.resolve(), (downloaded.parent / bundled.name).resolve())
            self.assertEqual(updater.read_bytes(), b"updater")
            self.assertEqual(len(launches), 1)
            self.assertEqual(Path(launches[0][0]), updater)
            self.assertNotIn("powershell.exe", launches[0])
            self.assertIn("--candidate", launches[0])
            self.assertIn(str(downloaded.resolve()), launches[0])


if __name__ == "__main__":
    unittest.main()
