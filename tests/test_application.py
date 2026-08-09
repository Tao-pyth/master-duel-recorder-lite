import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from master_duel_recorder_lite.application import (
    ApplicationOperationError,
    RecorderApplicationService,
)
from master_duel_recorder_lite.capture_targets import CaptureMode, CaptureTarget
from master_duel_recorder_lite.config import AppConfig
from master_duel_recorder_lite.ffmpeg import FfmpegVersion
from master_duel_recorder_lite.ffmpeg_setup import FfmpegInstallResult
from master_duel_recorder_lite.preflight import CheckStatus, PreflightCheck, PreflightReport
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.visual_detection import DetectionCandidate
from master_duel_recorder_lite.visual_worker import VisualDetectionStatus


class RecorderApplicationServiceTest(unittest.TestCase):
    def test_select_capture_target_persists_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            target = CaptureTarget(
                CaptureMode.WINDOW,
                "window:42",
                "ウィンドウ: Master Duel",
                window_handle=42,
                window_title="Master Duel",
            )

            saved = service.select_capture_target(target)
            loaded = service.load_config().config

        self.assertEqual(saved.capture_mode, "window")
        self.assertEqual(saved.capture_target_id, "window:42")
        self.assertEqual(loaded, saved)

    def test_unavailable_target_is_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            target = CaptureTarget(
                CaptureMode.MASTER_DUEL,
                "master_duel",
                "Master Duelウィンドウ",
                available=False,
            )

            with self.assertRaises(ApplicationOperationError):
                service.select_capture_target(target)

        self.assertFalse((service.paths.config / "app.toml").exists())

    def test_manual_recording_starts_and_stops_through_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            output = root / "data" / "recordings" / "recording.mkv"
            now = datetime.now(timezone.utc)
            result = RecordingResult(
                RecordingState.COMPLETED,
                output,
                0,
                now,
                now,
                100,
                None,
                (),
            )
            released: list[bool] = []
            session = SimpleNamespace(
                state=RecordingState.RECORDING,
                started_at=now,
                result=None,
            )
            prepared = SimpleNamespace(
                target=SimpleNamespace(recording_id="recording-id", path=output),
                session=session,
                start=lambda **_kwargs: RecordingState.RECORDING,
                poll=lambda: session.state,
                stop=lambda: result,
                release=lambda: released.append(True),
                visual_detection_status=VisualDetectionStatus(
                    "disabled", "disabled", 0, 0, 0
                ),
            )
            report = PreflightReport((PreflightCheck("all", "環境", CheckStatus.OK, "利用可能"),))
            service = RecorderApplicationService(user_data_dir=root)
            target = CaptureTarget(CaptureMode.DESKTOP, "desktop", "デスクトップ全体")
            with (
                patch("master_duel_recorder_lite.application.run_preflight", return_value=report),
                patch("master_duel_recorder_lite.application.prepare_recording", return_value=prepared),
            ):
                started = service.start_recording(target)
                stopped = service.stop_recording()

        self.assertTrue(started.active)
        self.assertEqual(started.recording_id, "recording-id")
        self.assertFalse(stopped.active)
        self.assertIs(stopped.state, RecordingState.COMPLETED)
        self.assertEqual(released, [True])

    def test_recording_browsing_delegates_to_shared_service(self) -> None:
        reference = object()
        browser = SimpleNamespace(
            resolve=lambda recording_id: ("resolve", recording_id),
            play=lambda recording_id: ("play", recording_id, reference),
            reveal=lambda recording_id: ("reveal", recording_id),
        )
        service = RecorderApplicationService(recording_browser=browser)  # type: ignore[arg-type]

        self.assertEqual(service.resolve_recording("id"), ("resolve", "id"))
        self.assertEqual(service.play_recording("id"), ("play", "id", reference))
        self.assertEqual(service.reveal_recording("id"), ("reveal", "id"))

    def test_ffmpeg_installation_path_is_saved_to_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            destination = root / "tools" / "ffmpeg"
            executable = destination / "bin" / "ffmpeg.exe"
            ffprobe = destination / "bin" / "ffprobe.exe"
            result = FfmpegInstallResult(
                destination,
                executable,
                ffprobe,
                FfmpegVersion("8.1.2", (8, 1, 2), 60),
                "a" * 64,
            )
            installer = SimpleNamespace(install=lambda _destination, progress=None: result)
            service = RecorderApplicationService(
                user_data_dir=root / "user_data",
                ffmpeg_installer=installer,  # type: ignore[arg-type]
            )

            installed = service.install_ffmpeg(destination)
            configured = service.load_config().config.ffmpeg_path

        self.assertEqual(installed, result)
        self.assertEqual(configured, str(executable))

    def test_automatic_start_candidate_is_saved_at_recording_origin(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        candidate = DetectionCandidate(
            "duel_start",
            2500,
            0.9,
            "3フレーム合意",
            "detector",
            "1",
        )
        repository = SimpleNamespace(add=lambda *args, **kwargs: (args, kwargs))

        with patch(
            "master_duel_recorder_lite.application.DuelTimelineRepository.from_runtime_paths",
            return_value=repository,
        ) as factory:
            with patch.object(repository, "add", wraps=repository.add) as add:
                service._save_automatic_start_candidate("recording", candidate, None)

        factory.assert_called_once_with(service.paths)
        add.assert_called_once()
        _, keyword = add.call_args
        self.assertEqual(keyword["elapsed_ms"], 0)
        self.assertEqual(keyword["event_type"], "duel_start")
        self.assertEqual(keyword["status"], "candidate")
        self.assertEqual(keyword["confidence"], 0.9)

    def test_watch_reports_error_when_visual_detection_is_disabled(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        config = AppConfig(visual_detection_enabled=False)
        events = []

        with patch.object(
            service,
            "load_config",
            return_value=SimpleNamespace(config=config, config_loaded=True),
        ):
            service._watch_loop(events.append)

        self.assertEqual(events[0].kind, "error")
        self.assertIn("画面イベント判定", events[0].message)
        self.assertEqual(events[-1].state, "stopped")

    def test_watch_reports_failed_preflight_details(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        report = PreflightReport(
            (
                PreflightCheck("ffmpeg", "FFmpeg", CheckStatus.OK, "9.0.0"),
                PreflightCheck(
                    "capabilities",
                    "録画能力",
                    CheckStatus.ERROR,
                    "FFmpeg能力検査が終了コード3221225794で失敗しました",
                ),
                PreflightCheck(
                    "inputs",
                    "録画入力",
                    CheckStatus.ERROR,
                    "FFmpeg能力を確認できないため入力列挙を中止しました",
                ),
            )
        )
        events = []

        with (
            patch.object(
                service,
                "load_config",
                return_value=SimpleNamespace(config=AppConfig(), config_loaded=True),
            ),
            patch("master_duel_recorder_lite.application.run_preflight", return_value=report),
        ):
            service._watch_loop(events.append)

        self.assertEqual(events[0].kind, "error")
        self.assertIn("録画能力:", events[0].message)
        self.assertIn("終了コード3221225794", events[0].message)
        self.assertIn("録画入力:", events[0].message)

    def test_watch_stop_during_start_observation_does_not_begin_recording(self) -> None:
        service = RecorderApplicationService(user_data_dir=Path("user_data"))
        config = AppConfig()
        report = PreflightReport((PreflightCheck("all", "環境", CheckStatus.OK, "利用可能"),))
        process = Mock()
        controller = SimpleNamespace(current=None, process=process)

        def observe_and_stop():
            service._watch_stop.set()
            return SimpleNamespace()

        start_monitor = SimpleNamespace(
            observe=observe_and_stop,
            status=VisualDetectionStatus("waiting", "waiting", 0, 0, 0),
            start_candidate=None,
        )
        with (
            patch.object(
                service,
                "load_config",
                return_value=SimpleNamespace(config=config, config_loaded=True),
            ),
            patch("master_duel_recorder_lite.application.run_preflight", return_value=report),
            patch(
                "master_duel_recorder_lite.application.discover_ffmpeg",
                return_value=SimpleNamespace(found=True, executable=Path("ffmpeg.exe").resolve()),
            ),
            patch("master_duel_recorder_lite.application.GameWindowMonitor"),
            patch("master_duel_recorder_lite.application.MasterDuelWindowDetector"),
            patch("master_duel_recorder_lite.application.FfmpegWindowFrameCapture"),
            patch(
                "master_duel_recorder_lite.application.MasterDuelStartMonitor",
                return_value=start_monitor,
            ),
            patch(
                "master_duel_recorder_lite.application.AutoRecordingController",
                return_value=controller,
            ),
        ):
            service._watch_stop.clear()
            service._watch_loop(None)

        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
