import hashlib
from io import BytesIO
import json
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.app_update import AppUpdateError, AppUpdateService


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
            ],
        }

        def opener(request, timeout):
            self.assertEqual(timeout, 20)
            self.assertEqual(request.headers["Cache-control"], "no-cache")
            url = request.full_url
            if url.endswith("releases/latest"):
                return _Response(json.dumps(release).encode())
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
            opener=lambda _request, timeout: _Response(json.dumps(release).encode())
        )
        self.assertFalse(service.check("1.0.0").available)

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


if __name__ == "__main__":
    unittest.main()
