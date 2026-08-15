from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.audio_loopback import (
    MINIMUM_PROCESS_LOOPBACK_BUILD,
    ProcessLoopbackController,
    new_audio_pipe_name,
    process_loopback_capability,
)


class InspectableStringIO(io.StringIO):
    def close(self) -> None:
        pass


class FakeHelperProcess:
    def __init__(self) -> None:
        self.stdin = InspectableStringIO()
        self.stderr = io.StringIO("event=ready\nevent=capturing\n")
        self.returncode: int | None = None
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class ProcessLoopbackTest(unittest.TestCase):
    def test_capability_requires_supported_windows_and_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            helper = Path(tmp_dir) / "mdrl-audio-loopback.exe"
            helper.write_bytes(b"helper")
            with patch("master_duel_recorder_lite.audio_loopback.platform.system", return_value="Windows"):
                supported = process_loopback_capability(
                    helper_path=helper,
                    windows_build=MINIMUM_PROCESS_LOOPBACK_BUILD,
                )
                old_windows = process_loopback_capability(
                    helper_path=helper,
                    windows_build=MINIMUM_PROCESS_LOOPBACK_BUILD - 1,
                )

        self.assertTrue(supported.supported)
        self.assertFalse(old_windows.supported)

    def test_pipe_name_is_unique_and_contains_no_user_text(self) -> None:
        first = new_audio_pipe_name("recording 日本語 !")
        second = new_audio_pipe_name("recording 日本語 !")

        self.assertTrue(first.startswith(r"\\.\pipe\mdrl-audio-recording-"))
        self.assertNotEqual(first, second)
        self.assertNotIn("日本語", first)

    def test_controller_starts_hidden_helper_and_stops_normally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            helper = Path(tmp_dir) / "mdrl-audio-loopback.exe"
            helper.write_bytes(b"helper")
            process = FakeHelperProcess()
            with (
                patch(
                    "master_duel_recorder_lite.audio_loopback.subprocess.Popen",
                    return_value=process,
                ) as popen,
                patch(
                    "master_duel_recorder_lite.audio_loopback.subprocess_creation_flags",
                    return_value=0x08000000,
                ),
            ):
                controller = ProcessLoopbackController(
                    helper_path=helper,
                    process_id=123,
                    pipe_name=r"\\.\pipe\mdrl-audio-test",
                )
                controller.start()
                controller.start()
                controller.stop()

        self.assertIn("event=ready", controller.diagnostics)
        self.assertIn("q\n", process.stdin.getvalue())
        self.assertEqual(popen.call_args.kwargs["creationflags"], 0x08000000)
        self.assertEqual(popen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
