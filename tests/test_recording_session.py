import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.recording_session import (
    RecordingSession,
    RecordingState,
    RecordingStateError,
)


class FakeStdin(io.StringIO):
    def __init__(self, on_stop: object | None = None) -> None:
        super().__init__()
        self.on_stop = on_stop

    def flush(self) -> None:
        if self.getvalue().endswith("q\n") and callable(self.on_stop):
            self.on_stop()


class FakeProcess:
    def __init__(
        self,
        *,
        output_path: Path | None = None,
        returncode: int = 0,
        immediate_returncode: int | None = None,
        timeout_once: bool = False,
        stderr: str = "",
        poll_error: OSError | None = None,
        wait_error: OSError | None = None,
    ) -> None:
        self.output_path = output_path
        self.returncode = returncode
        self.immediate_returncode = immediate_returncode
        self.timeout_once = timeout_once
        self.killed = False
        self.poll_error = poll_error
        self.wait_error = wait_error
        self.stdin = FakeStdin(self._write_output)
        self.stderr = io.StringIO(stderr)

    def _write_output(self) -> None:
        if self.output_path is not None:
            self.output_path.write_bytes(b"fake-recording")

    def poll(self) -> int | None:
        if self.poll_error is not None:
            raise self.poll_error
        return self.immediate_returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.wait_error is not None:
            raise self.wait_error
        if self.timeout_once and not self.killed:
            self.timeout_once = False
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class FakeAuxiliaryAudio:
    def __init__(self, *, start_error: Exception | None = None, warning: str | None = None) -> None:
        self.start_error = start_error
        self.warning = warning
        self.diagnostics = ("event=ready",)
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True
        if self.start_error is not None:
            raise self.start_error

    def poll(self) -> str | None:
        return self.warning

    def stop(self) -> None:
        self.stopped = True


class RecordingSessionTest(unittest.TestCase):
    def test_auxiliary_audio_starts_before_ffmpeg_and_stops_with_recording(self) -> None:
        events: list[str] = []
        auxiliary = FakeAuxiliaryAudio()
        original_start = auxiliary.start

        def start_audio() -> None:
            events.append("audio")
            original_start()

        auxiliary.start = start_audio  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(output_path=output)

            def process_factory(*_args: object, **_kwargs: object) -> FakeProcess:
                events.append("ffmpeg")
                return process

            session = RecordingSession(
                command=("ffmpeg", "with-audio"),
                output_path=output,
                process_factory=process_factory,
                startup_grace_seconds=0,
                auxiliary_audio=auxiliary,
            )
            session.start()
            session.stop()

        self.assertEqual(events, ["audio", "ffmpeg"])
        self.assertTrue(auxiliary.stopped)
        self.assertIn("音声: event=ready", session.diagnostics)

    def test_audio_start_failure_uses_video_only_fallback(self) -> None:
        commands: list[tuple[str, ...]] = []
        auxiliary = FakeAuxiliaryAudio(start_error=RuntimeError("activation failed"))
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"

            def process_factory(command: tuple[str, ...], **_kwargs: object) -> FakeProcess:
                commands.append(tuple(command))
                return FakeProcess(output_path=output)

            session = RecordingSession(
                command=("ffmpeg", "with-audio"),
                fallback_command=("ffmpeg", "video-only"),
                output_path=output,
                process_factory=process_factory,
                startup_grace_seconds=0,
                auxiliary_audio=auxiliary,
            )
            self.assertIs(session.start(), RecordingState.RECORDING)
            result = session.stop()

        self.assertTrue(result.succeeded)
        self.assertEqual(commands, [("ffmpeg", "video-only")])
        self.assertIn("映像のみ録画", session.audio_warning or "")

    def test_audio_runtime_failure_is_warning_and_video_continues(self) -> None:
        auxiliary = FakeAuxiliaryAudio(warning="音声が停止しました。映像録画は継続します")
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(output_path=output)
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
                auxiliary_audio=auxiliary,
            )
            session.start()
            self.assertIs(session.poll(), RecordingState.RECORDING)
            result = session.stop()

        self.assertTrue(result.succeeded)
        self.assertEqual(session.audio_warning, auxiliary.warning)
    def test_start_uses_hidden_windows_process_settings(self) -> None:
        captured_kwargs: dict[str, object] = {}

        def process_factory(*_args: object, **kwargs: object) -> FakeProcess:
            captured_kwargs.update(kwargs)
            return FakeProcess()

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch(
                "master_duel_recorder_lite.recording_session.configure_windows_process_errors"
            ) as configure_errors,
            patch(
                "master_duel_recorder_lite.recording_session.subprocess_creation_flags",
                return_value=0x08000000,
            ),
        ):
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=Path(tmp_dir) / "recording.mkv",
                process_factory=process_factory,
                startup_grace_seconds=0,
            )

            self.assertIs(session.start(), RecordingState.RECORDING)

        configure_errors.assert_called_once_with()
        self.assertEqual(captured_kwargs["creationflags"], 0x08000000)
        self.assertIs(captured_kwargs["stdin"], subprocess.PIPE)
        self.assertIs(captured_kwargs["stderr"], subprocess.PIPE)

    def test_start_stop_and_idempotent_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(output_path=output)
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
            )

            self.assertIs(session.start(), RecordingState.RECORDING)
            with self.assertRaises(RecordingStateError):
                session.start()
            first_result = session.stop()
            second_result = session.stop()

        self.assertTrue(first_result.succeeded)
        self.assertIs(first_result, second_result)
        self.assertEqual(first_result.size_bytes, len(b"fake-recording"))

    def test_process_start_failure_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"

            def fail_to_start(*_args: object, **_kwargs: object) -> FakeProcess:
                raise OSError("実行ファイルがありません")

            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=fail_to_start,
                startup_grace_seconds=0,
            )
            state = session.start()

        self.assertIs(state, RecordingState.FAILED)
        assert session.result is not None
        self.assertIn("開始できません", session.result.error or "")
        self.assertIsNone(session.result.returncode)

    def test_nonzero_early_exit_captures_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(immediate_returncode=1, returncode=1, stderr="入力を開けません\n")
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
            )
            state = session.start()

        self.assertIs(state, RecordingState.FAILED)
        assert session.result is not None
        self.assertEqual(session.result.returncode, 1)
        self.assertIn("入力を開けません", session.result.error or "")

    def test_windows_unsigned_exit_code_is_shown_in_three_forms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(immediate_returncode=4294967291, returncode=4294967291)
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
            )
            session.start()

        assert session.result is not None
        self.assertIn("4294967291 / -5 (0xFFFFFFFB)", session.result.error or "")

    def test_stalled_output_is_stopped_and_diagnosed(self) -> None:
        monotonic = [0.0]
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(returncode=0)
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
                output_stall_timeout_seconds=5,
                monotonic_clock=lambda: monotonic[0],
            )
            self.assertIs(session.start(), RecordingState.RECORDING)
            monotonic[0] = 5.0
            state = session.poll()

        self.assertIs(state, RecordingState.FAILED)
        self.assertTrue(process.killed)
        assert session.result is not None
        self.assertIn("出力が停止", session.result.error or "")
        self.assertIn("5秒間増加", session.result.diagnostics[-1])

    def test_diagnostics_keep_only_latest_bounded_lines(self) -> None:
        diagnostics = "".join(f"line-{index}\n" for index in range(150))
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(immediate_returncode=1, returncode=1, stderr=diagnostics)
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
                diagnostic_line_limit=100,
            )
            session.start()

        self.assertEqual(len(session.diagnostics), 100)
        self.assertEqual(session.diagnostics[0], "line-50")
        self.assertEqual(session.diagnostics[-1], "line-149")

    def test_stop_timeout_kills_process_and_stays_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(output_path=output, returncode=-9, timeout_once=True)
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
            )
            session.start()
            result = session.stop(timeout_seconds=0.01)

        self.assertTrue(process.killed)
        self.assertIs(result.state, RecordingState.FAILED)
        self.assertIn("強制終了", result.error or "")

    def test_poll_os_error_becomes_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(poll_error=OSError("invalid handle"))
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
            )

            state = session.start()

        self.assertIs(state, RecordingState.FAILED)
        assert session.result is not None
        self.assertIn("invalid handle", session.result.error or "")

    def test_wait_os_error_becomes_failed_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(wait_error=OSError("wait failed"))
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
            )
            session.start()

            result = session.stop()

        self.assertIs(result.state, RecordingState.FAILED)
        self.assertIn("wait failed", result.error or "")

    def test_zero_byte_output_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            process = FakeProcess(output_path=None, returncode=0)
            session = RecordingSession(
                command=("ffmpeg",),
                output_path=output,
                process_factory=lambda *_args, **_kwargs: process,
                startup_grace_seconds=0,
            )
            session.start()
            result = session.stop()

        self.assertIs(result.state, RecordingState.FAILED)
        self.assertIn("空", result.error or "")

    def test_real_subprocess_fake_ffmpeg_integration(self) -> None:
        fake_program = (
            "import pathlib, sys\n"
            "output = pathlib.Path(sys.argv[1])\n"
            "for line in sys.stdin:\n"
            "    if line.strip() == 'q':\n"
            "        output.write_bytes(b'fake-video-container')\n"
            "        raise SystemExit(0)\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "recording.mkv"
            session = RecordingSession(
                command=(sys.executable, "-u", "-c", fake_program, str(output)),
                output_path=output,
                startup_grace_seconds=0.05,
            )
            self.assertIs(session.start(), RecordingState.RECORDING)
            result = session.stop(timeout_seconds=2.0)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.size_bytes, len(b"fake-video-container"))


if __name__ == "__main__":
    unittest.main()
