import hashlib
from io import BytesIO
import tempfile
import unittest
from pathlib import Path
import zipfile

from master_duel_recorder_lite.ffmpeg import CommandResult
from master_duel_recorder_lite.ffmpeg_setup import (
    FFMPEG_CHECKSUM_URL,
    FFMPEG_DOWNLOAD_URL,
    FfmpegInstaller,
    FfmpegSetupError,
    default_ffmpeg_install_directory,
)


VERSION_OUTPUT = """ffmpeg version 8.1.2-essentials_build
libavutil      60. 20.100 / 60. 20.100
"""


def archive_bytes(*, malicious_path: str | None = None) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ffmpeg-8.1.2-essentials_build/bin/ffmpeg.exe", b"ffmpeg")
        archive.writestr("ffmpeg-8.1.2-essentials_build/bin/ffprobe.exe", b"ffprobe")
        archive.writestr("ffmpeg-8.1.2-essentials_build/README.txt", b"readme")
        if malicious_path is not None:
            archive.writestr(malicious_path, b"unsafe")
    return buffer.getvalue()


def downloader_for(
    archive: bytes,
    *,
    checksum: str | None = None,
):
    expected = checksum or hashlib.sha256(archive).hexdigest()

    def download(url: str, destination: Path, _limit: int, progress) -> None:
        data = (
            f"{expected}  ffmpeg-release-essentials.zip\n".encode("ascii")
            if url == FFMPEG_CHECKSUM_URL
            else archive
        )
        destination.write_bytes(data)
        if progress is not None:
            from master_duel_recorder_lite.ffmpeg_setup import FfmpegInstallProgress

            progress(FfmpegInstallProgress("test", len(data), len(data)))

    return download


class FfmpegInstallerTest(unittest.TestCase):
    def test_verified_archive_is_installed_transactionally(self) -> None:
        archive = archive_bytes()
        progress = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = Path(tmp_dir) / "tools" / "ffmpeg"
            installer = FfmpegInstaller(
                download=downloader_for(archive),
                runner=lambda _command, timeout: CommandResult(
                    0 if timeout == 15 else 1,
                    VERSION_OUTPUT,
                    "",
                ),
                platform_name="Windows",
            )

            result = installer.install(destination, progress=progress.append)

            self.assertTrue(result.executable.is_file())
            self.assertTrue(result.ffprobe_executable.is_file())
            self.assertEqual(result.version.display, "8.1.2")
            self.assertEqual(result.archive_sha256, hashlib.sha256(archive).hexdigest())
            self.assertIn(FFMPEG_DOWNLOAD_URL, (destination / "INSTALLATION.txt").read_text())
            self.assertFalse((destination / "README.txt").exists())
            self.assertFalse(
                any(path.name.startswith(".mdrl-ffmpeg-install-") for path in destination.parent.iterdir())
            )
            self.assertTrue(progress)

    def test_checksum_mismatch_leaves_destination_untouched(self) -> None:
        archive = archive_bytes()
        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = Path(tmp_dir) / "ffmpeg"
            installer = FfmpegInstaller(
                download=downloader_for(archive, checksum="0" * 64),
                platform_name="Windows",
            )

            with self.assertRaisesRegex(FfmpegSetupError, "SHA-256"):
                installer.install(destination)

            self.assertFalse(destination.exists())

    def test_archive_path_traversal_is_rejected(self) -> None:
        archive = archive_bytes(malicious_path="../outside.exe")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            installer = FfmpegInstaller(
                download=downloader_for(archive),
                platform_name="Windows",
            )

            with self.assertRaisesRegex(FfmpegSetupError, "不正なパス"):
                installer.install(root / "ffmpeg")

            self.assertFalse((root / "outside.exe").exists())
            self.assertFalse((root / "ffmpeg").exists())

    def test_nonempty_destination_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = Path(tmp_dir) / "ffmpeg"
            destination.mkdir()
            existing = destination / "keep.txt"
            existing.write_text("keep", encoding="utf-8")
            installer = FfmpegInstaller(
                download=lambda *_args: self.fail("download must not start"),
                platform_name="Windows",
            )

            with self.assertRaisesRegex(FfmpegSetupError, "空ではありません"):
                installer.install(destination)

            self.assertEqual(existing.read_text(encoding="utf-8"), "keep")

    def test_unsupported_version_is_removed_before_publish(self) -> None:
        archive = archive_bytes()
        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = Path(tmp_dir) / "ffmpeg"
            installer = FfmpegInstaller(
                download=downloader_for(archive),
                runner=lambda _command, _timeout: CommandResult(
                    0,
                    "ffmpeg version 5.1\nlibavutil      57. 10.100 / 57. 10.100\n",
                    "",
                ),
                platform_name="Windows",
            )

            with self.assertRaisesRegex(FfmpegSetupError, "6.0以上"):
                installer.install(destination)

            self.assertFalse(destination.exists())
            self.assertFalse(
                any(path.name.startswith(".mdrl-ffmpeg-install-") for path in Path(tmp_dir).iterdir())
            )

    def test_ffprobe_failure_is_removed_before_publish(self) -> None:
        archive = archive_bytes()

        def runner(command, _timeout):
            if Path(command[0]).name == "ffprobe.exe":
                return CommandResult(1, "", "ffprobe failed")
            return CommandResult(0, VERSION_OUTPUT, "")

        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = Path(tmp_dir) / "ffmpeg"
            installer = FfmpegInstaller(
                download=downloader_for(archive),
                runner=runner,
                platform_name="Windows",
            )

            with self.assertRaisesRegex(FfmpegSetupError, "ffprobe"):
                installer.install(destination)

            self.assertFalse(destination.exists())

    def test_default_destination_is_under_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = default_ffmpeg_install_directory(
                environ={"LOCALAPPDATA": tmp_dir}
            )

        self.assertEqual(
            destination,
            Path(tmp_dir).resolve()
            / "MasterDuelRecorderLite"
            / "tools"
            / "ffmpeg",
        )


if __name__ == "__main__":
    unittest.main()
