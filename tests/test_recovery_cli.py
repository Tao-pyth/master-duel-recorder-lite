from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.config import AppConfig, LoadedAppConfig
from master_duel_recorder_lite.ffmpeg import FfmpegDiscoveryResult, FfmpegVersion
from master_duel_recorder_lite.media_recovery import InspectionStatus, MediaInspection, MediaRepairResult
from master_duel_recorder_lite.recording_failure import classify_recording_failure
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.runtime_paths import RuntimePaths, default_runtime_paths, ensure_runtime_dirs


BASE_TIME = datetime(2026, 8, 8, tzinfo=timezone.utc)


class RecoveryCliTest(unittest.TestCase):
    def make_active(self, root: Path) -> tuple[RuntimePaths, RecordingHistoryRepository, Path]:
        paths = default_runtime_paths(user_data_dir=root)
        ensure_runtime_dirs(paths)
        output = paths.recordings / "partial.mkv"
        output.write_bytes(b"partial")
        repository = RecordingHistoryRepository.from_runtime_paths(paths)
        repository.register_starting(
            recording_id="recording",
            output_path=output,
            container="mkv",
            source="manual",
            created_at=BASE_TIME,
        )
        repository.mark_recording("recording", started_at=BASE_TIME)
        return paths, repository, output

    def make_failed(self, root: Path) -> tuple[RuntimePaths, RecordingHistoryRepository, Path]:
        paths, repository, output = self.make_active(root)
        repository.mark_interrupted(
            "recording",
            classification=classify_recording_failure(
                error="interrupted",
                returncode=None,
                output_exists=True,
                output_size=7,
                interrupted=True,
            ),
            ended_at=BASE_TIME,
            size_bytes=7,
        )
        return paths, repository, output

    def test_operational_startup_detects_interrupted_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            _paths, repository, _output = self.make_active(root)
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    ["--user-data-dir", str(root), "history", "show", "recording"]
                )
            entry = repository.get("recording")

        self.assertEqual(exit_code, 0)
        self.assertIn("[RECOVERY]", output.getvalue())
        assert entry is not None
        self.assertEqual(entry.failure_code, "application_interrupted")

    def test_recovery_list_and_ignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            _paths, repository, _output = self.make_failed(root)
            list_output = io.StringIO()
            ignore_output = io.StringIO()

            with redirect_stdout(list_output):
                list_code = main(["--user-data-dir", str(root), "recovery", "list"])
            with redirect_stdout(ignore_output):
                ignore_code = main(
                    ["--user-data-dir", str(root), "recovery", "ignore", "recording"]
                )
            entry = repository.get("recording")

        self.assertEqual(list_code, 0)
        self.assertEqual(ignore_code, 0)
        self.assertIn("recording", list_output.getvalue())
        assert entry is not None
        self.assertEqual(entry.recovery_state, "ignored")

    def test_inspect_and_repair_dry_run_use_selected_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths, _repository, original = self.make_failed(root)
            executable = Path(tmp_dir) / "ffmpeg.exe"
            discovery = FfmpegDiscoveryResult(
                executable=executable,
                source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58),
                attempts=(),
            )
            service = SimpleNamespace(
                inspect=lambda recording_id: MediaInspection(
                    recording_id,
                    InspectionStatus.VALID,
                    original,
                    "valid",
                    "ok",
                    5.0,
                    ("video",),
                ),
                repair=lambda recording_id, dry_run: MediaRepairResult(
                    recording_id,
                    False,
                    dry_run,
                    original,
                    original.with_name("planned.mkv"),
                    "planned",
                    "dry-run",
                ),
            )
            loaded = LoadedAppConfig(AppConfig(), paths.config / "app.toml", False)
            with (
                patch("master_duel_recorder_lite.__main__.load_app_config", return_value=loaded),
                patch("master_duel_recorder_lite.__main__.discover_ffmpeg", return_value=discovery),
                patch("master_duel_recorder_lite.__main__.MediaRecoveryService", return_value=service),
                redirect_stdout(io.StringIO()),
            ):
                inspect_code = main(
                    ["--user-data-dir", str(root), "recovery", "inspect", "recording"]
                )
                repair_code = main(
                    [
                        "--user-data-dir",
                        str(root),
                        "recovery",
                        "repair",
                        "recording",
                        "--dry-run",
                    ]
                )

        self.assertEqual(inspect_code, 0)
        self.assertEqual(repair_code, 0)

    def test_repair_ctrl_c_returns_130_and_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths, _repository, original = self.make_failed(root)
            before = original.read_bytes()
            discovery = FfmpegDiscoveryResult(
                executable=Path(tmp_dir) / "ffmpeg.exe",
                source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58),
                attempts=(),
            )

            def interrupt_repair(_recording_id: str, *, dry_run: bool) -> None:
                self.assertFalse(dry_run)
                raise KeyboardInterrupt

            service = SimpleNamespace(repair=interrupt_repair)
            loaded = LoadedAppConfig(AppConfig(), paths.config / "app.toml", False)
            output = io.StringIO()
            with (
                patch("master_duel_recorder_lite.__main__.load_app_config", return_value=loaded),
                patch("master_duel_recorder_lite.__main__.discover_ffmpeg", return_value=discovery),
                patch("master_duel_recorder_lite.__main__.MediaRecoveryService", return_value=service),
                redirect_stdout(output),
            ):
                code = main(
                    ["--user-data-dir", str(root), "recovery", "repair", "recording"]
                )
            preserved = original.read_bytes()

        self.assertEqual(code, 130)
        self.assertEqual(preserved, before)
        self.assertIn("停止要求", output.getvalue())


if __name__ == "__main__":
    unittest.main()
