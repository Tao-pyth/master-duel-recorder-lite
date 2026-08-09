import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.config import AppConfig, LoadedAppConfig
from master_duel_recorder_lite.preflight import CheckStatus, PreflightCheck, PreflightReport
from master_duel_recorder_lite.game_window import GameWindowObservation, GameWindowStatus
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.visual_worker import VisualDetectionStatus
from master_duel_recorder_lite.recording_browsing import (
    RecordingBrowseError,
    RecordingBrowseFailure,
)
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


class FakeRecordingSession:
    def __init__(self, result: RecordingResult) -> None:
        self.state = RecordingState.CREATED
        self.result: RecordingResult | None = None
        self._stop_result = result
        self.stop_count = 0

    def start(self) -> RecordingState:
        self.state = RecordingState.RECORDING
        return self.state

    def poll(self) -> RecordingState:
        return self.state

    def stop(self) -> RecordingResult:
        self.stop_count += 1
        self.state = self._stop_result.state
        self.result = self._stop_result
        return self._stop_result


class CliTest(unittest.TestCase):
    def test_doctor_returns_two_when_preflight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "user_data" / "config" / "app.toml"
            loaded = LoadedAppConfig(AppConfig(), config_path, False)
            report = PreflightReport(
                (PreflightCheck("ffmpeg", "FFmpeg", CheckStatus.ERROR, "見つかりません"),)
            )
            output = io.StringIO()
            with (
                patch("master_duel_recorder_lite.__main__.load_app_config", return_value=loaded),
                patch("master_duel_recorder_lite.__main__.run_preflight", return_value=report),
                redirect_stdout(output),
            ):
                exit_code = main(["--user-data-dir", str(Path(tmp_dir) / "user_data"), "doctor"])

        self.assertEqual(exit_code, 2)
        self.assertIn("[ERROR] FFmpeg", output.getvalue())
        self.assertIn("解決が必要", output.getvalue())

    def test_doctor_returns_zero_with_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "user_data" / "config" / "app.toml"
            loaded = LoadedAppConfig(AppConfig(), config_path, False)
            report = PreflightReport(
                (PreflightCheck("inputs", "録画入力", CheckStatus.WARNING, "音声入力は無効です"),)
            )
            output = io.StringIO()
            with (
                patch("master_duel_recorder_lite.__main__.load_app_config", return_value=loaded),
                patch("master_duel_recorder_lite.__main__.run_preflight", return_value=report),
                redirect_stdout(output),
            ):
                exit_code = main(["--user-data-dir", str(Path(tmp_dir) / "user_data"), "doctor"])

        self.assertEqual(exit_code, 0)
        self.assertIn("[WARN] 録画入力", output.getvalue())
        self.assertIn("利用できます", output.getvalue())

    def test_record_duration_stops_and_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_path = root / "recording.mkv"
            now = datetime.now(timezone.utc)
            result = RecordingResult(
                state=RecordingState.COMPLETED,
                output_path=output_path,
                returncode=0,
                started_at=now,
                ended_at=now,
                size_bytes=1234,
                error=None,
                diagnostics=(),
            )
            session = FakeRecordingSession(result)
            prepared = SimpleNamespace(
                target=SimpleNamespace(recording_id="recording-id", path=output_path),
                session=session,
                start=lambda **_kwargs: session.start(),
                poll=session.poll,
                stop=session.stop,
                release=lambda: None,
                visual_detection_status=VisualDetectionStatus(
                    "disabled", "disabled", 0, 0, 0
                ),
            )
            loaded = LoadedAppConfig(AppConfig(), root / "config" / "app.toml", False)
            report = PreflightReport(
                (PreflightCheck("inputs", "録画入力", CheckStatus.WARNING, "音声入力は無効です"),)
            )
            output = io.StringIO()
            with (
                patch("master_duel_recorder_lite.__main__.load_app_config", return_value=loaded),
                patch("master_duel_recorder_lite.__main__.run_preflight", return_value=report),
                patch(
                    "master_duel_recorder_lite.__main__.prepare_recording",
                    return_value=prepared,
                ) as prepare,
                redirect_stdout(output),
            ):
                exit_code = main(["--user-data-dir", str(root), "record", "--duration", "0.001"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(session.stop_count, 1)
        self.assertIn("録画を保存しました", output.getvalue())
        self.assertFalse(prepare.call_args.kwargs["enable_visual_detection"])

    def test_record_ctrl_c_attempts_normal_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_path = root / "recording.mkv"
            now = datetime.now(timezone.utc)
            result = RecordingResult(
                state=RecordingState.COMPLETED,
                output_path=output_path,
                returncode=0,
                started_at=now,
                ended_at=now,
                size_bytes=1234,
                error=None,
                diagnostics=(),
            )
            session = FakeRecordingSession(result)
            prepared = SimpleNamespace(
                target=SimpleNamespace(recording_id="recording-id", path=output_path),
                session=session,
                start=lambda **_kwargs: session.start(),
                poll=session.poll,
                stop=session.stop,
                release=lambda: None,
                visual_detection_status=VisualDetectionStatus(
                    "disabled", "disabled", 0, 0, 0
                ),
            )
            loaded = LoadedAppConfig(AppConfig(), root / "config" / "app.toml", False)
            report = PreflightReport((PreflightCheck("all", "環境", CheckStatus.OK, "利用可能"),))
            output = io.StringIO()
            with (
                patch("master_duel_recorder_lite.__main__.load_app_config", return_value=loaded),
                patch("master_duel_recorder_lite.__main__.run_preflight", return_value=report),
                patch("master_duel_recorder_lite.__main__.prepare_recording", return_value=prepared),
                patch("master_duel_recorder_lite.__main__._wait_for_recording", side_effect=KeyboardInterrupt),
                redirect_stdout(output),
            ):
                exit_code = main(["--user-data-dir", str(root), "record"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(session.stop_count, 1)
        self.assertIn("停止要求", output.getvalue())

    def test_watch_once_reports_state_without_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            loaded = LoadedAppConfig(AppConfig(), root / "config" / "app.toml", False)
            monitor = SimpleNamespace(
                observe=lambda: GameWindowObservation(
                    GameWindowStatus.NOT_RUNNING,
                    None,
                    None,
                    0,
                    "masterduel.exeは起動していません",
                )
            )
            output = io.StringIO()
            with (
                patch("master_duel_recorder_lite.__main__.load_app_config", return_value=loaded),
                patch("master_duel_recorder_lite.__main__.GameWindowMonitor", return_value=monitor),
                patch("master_duel_recorder_lite.__main__.prepare_recording") as prepare,
                redirect_stdout(output),
            ):
                exit_code = main(["--user-data-dir", str(root), "watch", "--once"])

        self.assertEqual(exit_code, 0)
        self.assertIn("[NOT_RUNNING]", output.getvalue())
        prepare.assert_not_called()

    def test_history_list_and_show_display_persisted_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            ensure_runtime_dirs(paths)
            output_path = paths.recordings / "recording.mkv"
            output_path.write_bytes(b"video")
            repository = RecordingHistoryRepository.from_runtime_paths(paths)
            now = datetime.now(timezone.utc)
            repository.register_starting(
                recording_id="history-id",
                output_path=output_path,
                container="mkv",
                source="manual",
                created_at=now,
            )
            repository.mark_recording("history-id", started_at=now)
            repository.finalize(
                "history-id",
                RecordingResult(
                    state=RecordingState.COMPLETED,
                    output_path=output_path,
                    returncode=0,
                    started_at=now,
                    ended_at=now,
                    size_bytes=5,
                    error=None,
                    diagnostics=("complete",),
                ),
            )
            list_output = io.StringIO()
            show_output = io.StringIO()
            with redirect_stdout(list_output):
                list_code = main(
                    ["--user-data-dir", str(root), "history", "list", "--state", "completed"]
                )
            with redirect_stdout(show_output):
                show_code = main(["--user-data-dir", str(root), "history", "show", "history-id"])

        self.assertEqual(list_code, 0)
        self.assertEqual(show_code, 0)
        self.assertIn("history-id", list_output.getvalue())
        self.assertIn("state: completed", show_output.getvalue())
        self.assertIn("complete", show_output.getvalue())

    def test_history_show_missing_returns_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            error = io.StringIO()
            with redirect_stderr(error):
                exit_code = main(
                    ["--user-data-dir", tmp_dir, "history", "show", "missing"]
                )

        self.assertEqual(exit_code, 4)
        self.assertIn("見つかりません", error.getvalue())

    def test_history_check_reports_without_deleting_untracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            ensure_runtime_dirs(paths)
            untracked = paths.recordings / "untracked.mkv"
            untracked.write_bytes(b"video")
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(["--user-data-dir", str(root), "history", "check"])
            preserved = untracked.exists()

        self.assertEqual(exit_code, 4)
        self.assertIn("UNTRACKED", output.getvalue())
        self.assertTrue(preserved)

    def test_history_play_uses_recording_browser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            browser = SimpleNamespace(
                play=lambda recording_id: SimpleNamespace(
                    recording_id=recording_id,
                    path=Path(tmp_dir) / "video.mkv",
                    warnings=(),
                )
            )
            output = io.StringIO()
            with (
                patch("master_duel_recorder_lite.__main__.RecordingBrowser", return_value=browser),
                redirect_stdout(output),
            ):
                exit_code = main(["--user-data-dir", tmp_dir, "history", "play", "recording"])

        self.assertEqual(exit_code, 0)
        self.assertIn("再生を開始しました", output.getvalue())

    def test_history_reveal_missing_file_returns_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            def missing(_recording_id: str) -> object:
                raise RecordingBrowseError(
                    RecordingBrowseFailure.MISSING,
                    "録画ファイルが見つかりません",
                )

            browser = SimpleNamespace(reveal=missing)
            error = io.StringIO()
            with (
                patch("master_duel_recorder_lite.__main__.RecordingBrowser", return_value=browser),
                redirect_stderr(error),
            ):
                exit_code = main(["--user-data-dir", tmp_dir, "history", "reveal", "recording"])

        self.assertEqual(exit_code, 4)
        self.assertIn("E_HISTORY_OPEN", error.getvalue())


if __name__ == "__main__":
    unittest.main()
