import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from master_duel_recorder_lite.application import (
    ApplicationOperationError,
    RecorderApplicationService,
)
from master_duel_recorder_lite.capture_targets import CaptureMode, CaptureTarget
from master_duel_recorder_lite.preflight import CheckStatus, PreflightCheck, PreflightReport
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState


class RecorderApplicationServiceTest(unittest.TestCase):
    def test_select_capture_target_persists_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RecorderApplicationService(user_data_dir=Path(tmp_dir) / "user_data")
            target = CaptureTarget(
                CaptureMode.WINDOW,
                "window:42",
                "ウィンドウ: Master Duel",
                window_handle=42,
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


if __name__ == "__main__":
    unittest.main()
