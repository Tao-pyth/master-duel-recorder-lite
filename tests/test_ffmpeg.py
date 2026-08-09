import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.ffmpeg import (
    CommandResult,
    FfmpegCapabilities,
    FfmpegVersion,
    discover_ffmpeg,
    enumerate_windows_inputs,
    parse_dshow_devices,
    parse_ffmpeg_version,
    probe_ffmpeg_capabilities,
    validate_ffmpeg_capabilities,
)


VERSION_OUTPUT = """ffmpeg version 6.1.1-full_build
libavutil      58. 29.100 / 58. 29.100
"""


class FfmpegDiscoveryTest(unittest.TestCase):
    def test_explicit_config_path_has_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = Path(tmp_dir) / "ffmpeg.exe"
            executable.touch()

            result = discover_ffmpeg(
                str(executable),
                runner=lambda _command, _timeout: CommandResult(0, VERSION_OUTPUT, ""),
                path_lookup=lambda _command: "C:/ignored/ffmpeg.exe",
                environ={},
                platform_name="Windows",
            )

        self.assertTrue(result.found)
        self.assertEqual(result.source, "config")
        self.assertEqual(result.executable, executable.resolve())

    def test_path_is_used_for_default_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = Path(tmp_dir) / "ffmpeg.exe"
            executable.touch()

            result = discover_ffmpeg(
                "ffmpeg",
                runner=lambda _command, _timeout: CommandResult(0, VERSION_OUTPUT, ""),
                path_lookup=lambda _command: str(executable),
                environ={},
                platform_name="Windows",
            )

        self.assertTrue(result.found)
        self.assertEqual(result.source, "PATH")

    def test_missing_ffmpeg_returns_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = discover_ffmpeg(
                "ffmpeg",
                runner=lambda _command, _timeout: self.fail("存在しない候補を実行してはいけません"),
                path_lookup=lambda _command: None,
                environ={"LOCALAPPDATA": tmp_dir},
                platform_name="Windows",
            )

        self.assertFalse(result.found)
        self.assertGreaterEqual(len(result.attempts), 1)
        self.assertTrue(all(attempt.result for attempt in result.attempts))

    def test_managed_local_app_data_install_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            executable = (
                Path(tmp_dir)
                / "MasterDuelRecorderLite"
                / "tools"
                / "ffmpeg"
                / "bin"
                / "ffmpeg.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.touch()
            timeouts: list[float] = []

            def runner(_command: tuple[str, ...], timeout: float) -> CommandResult:
                timeouts.append(timeout)
                return CommandResult(0, VERSION_OUTPUT, "")

            result = discover_ffmpeg(
                "ffmpeg",
                runner=runner,
                path_lookup=lambda _command: None,
                environ={"LOCALAPPDATA": tmp_dir},
                platform_name="Windows",
            )

        self.assertTrue(result.found)
        self.assertEqual(result.executable, executable.resolve())
        self.assertEqual(timeouts, [15.0])


class FfmpegCapabilityTest(unittest.TestCase):
    def test_nightly_version_uses_libavutil_abi(self) -> None:
        version = parse_ffmpeg_version(
            "ffmpeg version N-112394-g37b5f4a1f6-20231009\n"
            "libavutil      58. 27.100 / 58. 27.100\n"
        )

        self.assertIsNotNone(version)
        assert version is not None
        self.assertIsNone(version.semantic)
        self.assertEqual(version.libavutil_major, 58)
        self.assertTrue(version.is_supported)

    def test_probe_parses_required_components(self) -> None:
        outputs = {
            ("-version",): VERSION_OUTPUT,
            ("-hide_banner", "-demuxers"): " D  dshow DirectShow capture\n D  gdigrab GDI capture\n",
            ("-hide_banner", "-muxers"): " E  matroska Matroska\n E  mp4 MP4\n",
            ("-hide_banner", "-encoders"): " V....D libx264 H.264\n A..... aac AAC\n",
        }

        def runner(command: tuple[str, ...], _timeout: float) -> CommandResult:
            return CommandResult(0, outputs[tuple(command[1:])], "")

        capabilities = probe_ffmpeg_capabilities(Path("ffmpeg.exe"), runner=runner)

        self.assertIn("gdigrab", capabilities.demuxers)
        self.assertIn("matroska", capabilities.muxers)
        self.assertIn("libx264", capabilities.encoders)

    def test_validation_reports_each_missing_capability(self) -> None:
        capabilities = FfmpegCapabilities(
            version=FfmpegVersion("5.1", (5, 1, 0), 57),
            demuxers=frozenset(),
            muxers=frozenset(),
            encoders=frozenset(),
        )

        validation = validate_ffmpeg_capabilities(
            capabilities,
            required_demuxers=("gdigrab", "dshow"),
            required_encoder="libx264",
            required_muxer="matroska",
        )

        self.assertFalse(validation.supported)
        self.assertEqual(len(validation.errors), 5)


class FfmpegInputEnumerationTest(unittest.TestCase):
    def test_parse_dshow_devices_preserves_japanese_names(self) -> None:
        output = """[dshow @ 000001] \"マイク (Realtek Audio)\" (audio)
[dshow @ 000001]   Alternative name \"@device_cm_...\"
[dshow @ 000001] \"USB Camera\" (video)
"""

        devices = parse_dshow_devices(output)

        self.assertEqual([device.display_name for device in devices], ["マイク (Realtek Audio)", "USB Camera"])
        self.assertEqual([device.kind for device in devices], ["audio", "video"])

    def test_enumeration_returns_desktop_and_audio(self) -> None:
        output = '[dshow @ 000001] "マイク" (audio)\n'
        result = enumerate_windows_inputs(
            Path("ffmpeg.exe"),
            runner=lambda _command, _timeout: CommandResult(1, "", output),
            platform_name="Windows",
        )

        self.assertTrue(result.succeeded)
        self.assertEqual([device.identifier for device in result.inputs], ["desktop", "マイク"])
        self.assertEqual(result.warnings, ())

    def test_no_audio_is_a_distinct_warning(self) -> None:
        result = enumerate_windows_inputs(
            Path("ffmpeg.exe"),
            runner=lambda _command, _timeout: CommandResult(1, "", "DirectShow audio devices\n"),
            platform_name="Windows",
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.inputs), 1)
        self.assertEqual(result.warnings, ("音声入力候補が見つかりません",))

    def test_missing_dshow_is_an_error(self) -> None:
        result = enumerate_windows_inputs(
            Path("ffmpeg.exe"),
            runner=lambda _command, _timeout: CommandResult(
                1, "", "Unknown input format: 'dshow'\n"
            ),
            platform_name="Windows",
        )

        self.assertFalse(result.succeeded)
        self.assertEqual(result.errors, ("FFmpegがdshow入力に対応していません",))


if __name__ == "__main__":
    unittest.main()
