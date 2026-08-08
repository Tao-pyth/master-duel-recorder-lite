import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from master_duel_recorder_lite.config import AppConfig
from master_duel_recorder_lite.ffmpeg import CommandResult
from master_duel_recorder_lite.preflight import CheckStatus, run_preflight
from master_duel_recorder_lite.runtime_paths import default_runtime_paths


VERSION_OUTPUT = """ffmpeg version 6.1.1-full_build
libavutil      58. 29.100 / 58. 29.100
"""


def capable_runner(command: tuple[str, ...], _timeout: float) -> CommandResult:
    arguments = tuple(command[1:])
    if arguments == ("-version",):
        return CommandResult(0, VERSION_OUTPUT, "")
    if arguments == ("-hide_banner", "-demuxers"):
        return CommandResult(0, " D  gdigrab GDI capture\n D  dshow DirectShow capture\n", "")
    if arguments == ("-hide_banner", "-muxers"):
        return CommandResult(0, " E  matroska Matroska\n E  mp4 MP4\n", "")
    if arguments == ("-hide_banner", "-encoders"):
        return CommandResult(0, " V....D libx264 H.264\n A..... aac AAC\n", "")
    if "-list_devices" in arguments:
        return CommandResult(1, "", '[dshow @ 000001] "マイク" (audio)\n')
    raise AssertionError(f"予期しないコマンドです: {command}")


class PreflightTest(unittest.TestCase):
    def test_all_checks_succeed_with_selected_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            report = run_preflight(
                paths=paths,
                config=AppConfig(ffmpeg_path=str(executable), audio_input="マイク"),
                config_loaded=True,
                runner=capable_runner,
                path_lookup=lambda _command: None,
                environ={},
                platform_name="Windows",
                disk_usage=lambda _path: SimpleNamespace(free=2 * 1024**3),
            )

        self.assertTrue(report.succeeded)
        self.assertEqual(report.exit_code, 0)
        self.assertTrue(all(check.status is CheckStatus.OK for check in report.checks))
        self.assertEqual(
            [check.code for check in report.checks],
            ["config", "ffmpeg", "capabilities", "inputs", "storage", "disk-space"],
        )

    def test_missing_ffmpeg_fails_but_storage_is_still_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            report = run_preflight(
                paths=paths,
                config=AppConfig(ffmpeg_path=str(Path(tmp_dir) / "missing.exe")),
                config_loaded=False,
                runner=capable_runner,
                path_lookup=lambda _command: None,
                environ={},
                platform_name="Windows",
                disk_usage=lambda _path: SimpleNamespace(free=2 * 1024**3),
            )

        statuses = {check.code: check.status for check in report.checks}
        self.assertFalse(report.succeeded)
        self.assertEqual(report.exit_code, 2)
        self.assertIs(statuses["ffmpeg"], CheckStatus.ERROR)
        self.assertIs(statuses["capabilities"], CheckStatus.ERROR)
        self.assertIs(statuses["inputs"], CheckStatus.ERROR)
        self.assertIs(statuses["storage"], CheckStatus.OK)
        self.assertIs(statuses["disk-space"], CheckStatus.OK)

    def test_missing_selected_audio_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            report = run_preflight(
                paths=paths,
                config=AppConfig(ffmpeg_path=str(executable), audio_input="存在しないマイク"),
                config_loaded=True,
                runner=capable_runner,
                platform_name="Windows",
                disk_usage=lambda _path: SimpleNamespace(free=2 * 1024**3),
            )

        input_check = next(check for check in report.checks if check.code == "inputs")
        self.assertIs(input_check.status, CheckStatus.ERROR)
        self.assertIn("存在しないマイク", input_check.message)

    def test_missing_encoder_fails_capability_check(self) -> None:
        def runner_without_encoder(command: tuple[str, ...], timeout: float) -> CommandResult:
            if tuple(command[1:]) == ("-hide_banner", "-encoders"):
                return CommandResult(0, " V....D h264_nvenc H.264\n", "")
            return capable_runner(command, timeout)

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            report = run_preflight(
                paths=paths,
                config=AppConfig(ffmpeg_path=str(executable)),
                config_loaded=True,
                runner=runner_without_encoder,
                platform_name="Windows",
                disk_usage=lambda _path: SimpleNamespace(free=2 * 1024**3),
            )

        capability_check = next(check for check in report.checks if check.code == "capabilities")
        self.assertFalse(report.succeeded)
        self.assertIs(capability_check.status, CheckStatus.ERROR)
        self.assertIn("libx264", capability_check.message)

    def test_missing_aac_fails_when_audio_is_selected(self) -> None:
        def runner_without_aac(command: tuple[str, ...], timeout: float) -> CommandResult:
            if tuple(command[1:]) == ("-hide_banner", "-encoders"):
                return CommandResult(0, " V....D libx264 H.264\n", "")
            return capable_runner(command, timeout)

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            report = run_preflight(
                paths=paths,
                config=AppConfig(ffmpeg_path=str(executable), audio_input="マイク"),
                config_loaded=True,
                runner=runner_without_aac,
                platform_name="Windows",
                disk_usage=lambda _path: SimpleNamespace(free=2 * 1024**3),
            )

        capability_check = next(check for check in report.checks if check.code == "capabilities")
        self.assertIs(capability_check.status, CheckStatus.ERROR)
        self.assertIn("aac", capability_check.message)

    def test_disabled_audio_is_warning_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            report = run_preflight(
                paths=paths,
                config=AppConfig(ffmpeg_path=str(executable)),
                config_loaded=True,
                runner=capable_runner,
                platform_name="Windows",
                disk_usage=lambda _path: SimpleNamespace(free=2 * 1024**3),
            )

        input_check = next(check for check in report.checks if check.code == "inputs")
        self.assertTrue(report.succeeded)
        self.assertIs(input_check.status, CheckStatus.WARNING)

    def test_low_disk_space_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            report = run_preflight(
                paths=paths,
                config=AppConfig(ffmpeg_path=str(executable)),
                config_loaded=True,
                runner=capable_runner,
                platform_name="Windows",
                disk_usage=lambda _path: SimpleNamespace(free=512 * 1024**2),
            )

        disk_check = next(check for check in report.checks if check.code == "disk-space")
        self.assertFalse(report.succeeded)
        self.assertIs(disk_check.status, CheckStatus.ERROR)

    def test_missing_storage_fails_when_auto_create_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            report = run_preflight(
                paths=paths,
                config=AppConfig(ffmpeg_path=str(executable), auto_create_user_data=False),
                config_loaded=True,
                runner=capable_runner,
                platform_name="Windows",
            )

        storage_check = next(check for check in report.checks if check.code == "storage")
        disk_check = next(check for check in report.checks if check.code == "disk-space")
        self.assertFalse(report.succeeded)
        self.assertIs(storage_check.status, CheckStatus.ERROR)
        self.assertIs(disk_check.status, CheckStatus.ERROR)


if __name__ == "__main__":
    unittest.main()
