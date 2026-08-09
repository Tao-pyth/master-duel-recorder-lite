import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.config import AppConfig
from master_duel_recorder_lite.capture_targets import CaptureInput
from master_duel_recorder_lite.duel_records import DuelRecordRepository
from master_duel_recorder_lite.duel_timeline import DuelTimelineRepository
from master_duel_recorder_lite.ffmpeg import FfmpegDiscoveryResult, FfmpegVersion
from master_duel_recorder_lite.frame_capture import FrameCaptureResult, FrameSample
from master_duel_recorder_lite.recorder import (
    RecordingPreparationError,
    RecordingTrackingError,
    prepare_recording,
)
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.recording_state_store import RecordingStateStoreError
from master_duel_recorder_lite.visual_worker import VisualDetectionStatus
from master_duel_recorder_lite.visual_detection import DetectionCandidate


class RecorderPreparationTest(unittest.TestCase):
    def test_prepare_connects_profile_target_command_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            discovery = FfmpegDiscoveryResult(
                executable=executable.resolve(),
                source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58),
                attempts=(),
            )
            with patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=discovery):
                prepared = prepare_recording(paths=paths, config=AppConfig(ffmpeg_path=str(executable), capture_mode="desktop"))
                prepared.release()

        self.assertTrue(prepared.target.path.is_relative_to(paths.recordings.resolve()))
        self.assertEqual(prepared.command[-1], str(prepared.target.path))
        self.assertIs(prepared.session.state, RecordingState.CREATED)
        self.assertTrue(prepared.lock.released)

    def test_missing_ffmpeg_is_preparation_error(self) -> None:
        missing = FfmpegDiscoveryResult(None, None, None, ())
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            with patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=missing):
                with self.assertRaises(RecordingPreparationError):
                    prepare_recording(paths=paths, config=AppConfig())

    def test_second_preparation_is_rejected_by_recording_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            discovery = FfmpegDiscoveryResult(
                executable=executable.resolve(),
                source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58),
                attempts=(),
            )
            with patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=discovery):
                first = prepare_recording(paths=paths, config=AppConfig(ffmpeg_path=str(executable), capture_mode="desktop"))
                try:
                    with self.assertRaises(RecordingPreparationError):
                        prepare_recording(paths=paths, config=AppConfig(ffmpeg_path=str(executable), capture_mode="desktop"))
                finally:
                    first.release()

    def test_lock_metadata_failure_is_preparation_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            discovery = FfmpegDiscoveryResult(
                executable=executable.resolve(),
                source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58),
                attempts=(),
            )
            with (
                patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=discovery),
                patch(
                    "master_duel_recorder_lite.recorder.RecordingLock.acquire",
                    side_effect=OSError("fsync failed"),
                ),
            ):
                with self.assertRaisesRegex(RecordingPreparationError, "録画ロック"):
                    prepare_recording(paths=paths, config=AppConfig(ffmpeg_path=str(executable), capture_mode="desktop"))

    def test_prepared_recording_persists_successful_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            discovery = FfmpegDiscoveryResult(
                executable=executable.resolve(),
                source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58),
                attempts=(),
            )
            with patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=discovery):
                prepared = prepare_recording(paths=paths, config=AppConfig(ffmpeg_path=str(executable), capture_mode="desktop"))
            prepared.session = FakeLifecycleSession(prepared.target.path)  # type: ignore[assignment]
            try:
                state = prepared.start(source="manual", detection_reason="test")
                result = prepared.stop()
                entry = prepared.history.get(prepared.target.recording_id)
                duel_record = DuelRecordRepository(prepared.history.database_path).get(
                    prepared.target.recording_id
                )
                persisted = prepared.state_store.load()
            finally:
                prepared.release()

        self.assertIs(state, RecordingState.RECORDING)
        self.assertTrue(result.succeeded)
        assert entry is not None
        self.assertEqual(entry.state, "completed")
        self.assertEqual(entry.source, "manual")
        self.assertEqual(entry.detection_reason, "test")
        assert duel_record is not None
        self.assertEqual(duel_record.values.status, "draft")
        assert persisted is not None
        self.assertEqual(persisted.value.state, "completed")

    def test_visual_worker_follows_recording_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            discovery = FfmpegDiscoveryResult(
                executable=executable.resolve(),
                source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58),
                attempts=(),
            )
            with patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=discovery):
                prepared = prepare_recording(
                    paths=paths,
                    config=AppConfig(ffmpeg_path=str(executable), capture_mode="master_duel"),
                    capture_input=CaptureInput(
                        "gdigrab",
                        "title=Master Duel",
                        window_handle=123,
                        window_title="Master Duel",
                    ),
                )
            prepared.session = FakeLifecycleSession(prepared.target.path)  # type: ignore[assignment]
            worker = FakeVisualWorker()
            prepared.visual_worker_builder = lambda _started_at: worker  # type: ignore[assignment]
            try:
                state = prepared.start(source="manual")
                result = prepared.stop()
            finally:
                prepared.release()

        self.assertIs(state, RecordingState.RECORDING)
        self.assertTrue(result.succeeded)
        self.assertEqual((worker.start_count, worker.stop_count), (1, 1))
        self.assertEqual(worker.request_stop_count, 1)
        self.assertFalse(worker.active)

    def test_visual_worker_start_failure_does_not_fail_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            discovery = FfmpegDiscoveryResult(
                executable=executable.resolve(), source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58), attempts=(),
            )
            with patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=discovery):
                prepared = prepare_recording(
                    paths=paths,
                    config=AppConfig(ffmpeg_path=str(executable), capture_mode="desktop"),
                )
            prepared.session = FakeLifecycleSession(prepared.target.path)  # type: ignore[assignment]
            prepared.visual_worker_builder = lambda _started_at: (_ for _ in ()).throw(
                RuntimeError("visual unavailable")
            )
            try:
                state = prepared.start(source="manual")
                result = prepared.stop()
            finally:
                prepared.release()

        self.assertIs(state, RecordingState.RECORDING)
        self.assertTrue(result.succeeded)
        self.assertEqual(prepared.visual_detection_status.state, "failed")
        self.assertIn("visual unavailable", prepared.visual_detection_status.message)

    def test_master_duel_visual_candidate_is_saved_without_auto_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            discovery = FfmpegDiscoveryResult(
                executable=executable.resolve(), source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58), attempts=(),
            )
            with patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=discovery):
                prepared = prepare_recording(
                    paths=paths,
                    config=AppConfig(ffmpeg_path=str(executable), capture_mode="master_duel"),
                    capture_input=CaptureInput(
                        "gdigrab",
                        "title=Master Duel",
                        window_handle=123,
                        window_title="Master Duel",
                    ),
                )
            prepared.session = FakeLifecycleSession(prepared.target.path)  # type: ignore[assignment]
            captured_at = datetime(2026, 8, 8, tzinfo=timezone.utc) + timedelta(seconds=1)
            frame_result = FrameCaptureResult(
                FrameSample(
                    captured_at, 123, "Master Duel", 160, 90, "bmp", b"synthetic"
                ),
                None,
            )
            detected = DetectionCandidate(
                "duel_start", 1000, 0.9, "synthetic match", "test", "1"
            )
            try:
                with (
                    patch(
                        "master_duel_recorder_lite.recorder.FfmpegWindowFrameCapture.capture",
                        return_value=frame_result,
                    ),
                    patch(
                        "master_duel_recorder_lite.recorder.VisualDetectionPipeline.analyze",
                        return_value=(detected,),
                    ),
                ):
                    prepared.start(source="manual")
                    time.sleep(0.15)
                    result = prepared.stop()
                events = DuelTimelineRepository(prepared.history.database_path).list(
                    prepared.target.recording_id
                )
            finally:
                prepared.release()

        self.assertTrue(result.succeeded)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].status, "candidate")
        self.assertEqual(events[0].source, "detected")
        self.assertEqual(events[0].label, "synthetic match")

    def test_prepared_recording_persists_start_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            discovery = FfmpegDiscoveryResult(
                executable=executable.resolve(),
                source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58),
                attempts=(),
            )
            with patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=discovery):
                prepared = prepare_recording(paths=paths, config=AppConfig(ffmpeg_path=str(executable), capture_mode="desktop"))
            prepared.session = FakeLifecycleSession(  # type: ignore[assignment]
                prepared.target.path,
                fail_start=True,
            )
            try:
                state = prepared.start(source="automatic", detection_reason="visible")
                entry = prepared.history.get(prepared.target.recording_id)
                persisted = prepared.state_store.load()
            finally:
                prepared.release()

        self.assertIs(state, RecordingState.FAILED)
        assert entry is not None
        self.assertEqual(entry.state, "failed")
        self.assertIn("injected", entry.error or "")
        assert persisted is not None
        self.assertEqual(persisted.value.state, "failed")

    def test_state_storage_capacity_failure_prevents_ffmpeg_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            executable = root / "ffmpeg.exe"
            executable.touch()
            paths = default_runtime_paths(user_data_dir=root / "user_data")
            ensure_runtime_dirs(paths)
            discovery = FfmpegDiscoveryResult(
                executable=executable.resolve(),
                source="config",
                version=FfmpegVersion("6.1.1", (6, 1, 1), 58),
                attempts=(),
            )
            with patch("master_duel_recorder_lite.recorder.discover_ffmpeg", return_value=discovery):
                prepared = prepare_recording(paths=paths, config=AppConfig(ffmpeg_path=str(executable), capture_mode="desktop"))
            prepared.session = FakeLifecycleSession(prepared.target.path)  # type: ignore[assignment]
            try:
                with patch.object(
                    prepared.state_store,
                    "save",
                    side_effect=RecordingStateStoreError("No space left on device"),
                ):
                    with self.assertRaises(RecordingTrackingError):
                        prepared.start(source="manual")
                entry = prepared.history.get(prepared.target.recording_id)
            finally:
                prepared.release()

        self.assertIs(prepared.session.state, RecordingState.CREATED)
        assert entry is not None
        self.assertEqual(entry.state, "failed")
        self.assertEqual(entry.failure_code, "storage_full")


class FakeLifecycleSession:
    def __init__(self, output_path: Path, *, fail_start: bool = False) -> None:
        self.output_path = output_path
        self.fail_start = fail_start
        self.state = RecordingState.CREATED
        self.started_at: datetime | None = None
        self.result: RecordingResult | None = None
        self.diagnostics: list[str] = []

    def add_diagnostic(self, line: str) -> None:
        self.diagnostics.append(line)

    def start(self) -> RecordingState:
        self.started_at = datetime(2026, 8, 8, tzinfo=timezone.utc)
        if self.fail_start:
            self.state = RecordingState.FAILED
            self.result = RecordingResult(
                state=self.state,
                output_path=self.output_path,
                returncode=None,
                started_at=self.started_at,
                ended_at=self.started_at,
                size_bytes=0,
                error="injected start failure",
                diagnostics=(),
            )
        else:
            self.state = RecordingState.RECORDING
        return self.state

    def poll(self) -> RecordingState:
        return self.state

    def stop(self, *, timeout_seconds: float = 10.0) -> RecordingResult:
        assert self.started_at is not None
        self.output_path.write_bytes(b"video")
        self.state = RecordingState.COMPLETED
        self.result = RecordingResult(
            state=self.state,
            output_path=self.output_path,
            returncode=0,
            started_at=self.started_at,
            ended_at=self.started_at + timedelta(seconds=5),
            size_bytes=5,
            error=None,
            diagnostics=(),
        )
        return self.result


class FakeVisualWorker:
    def __init__(self) -> None:
        self.active = False
        self.start_count = 0
        self.stop_count = 0
        self.request_stop_count = 0

    @property
    def status(self) -> VisualDetectionStatus:
        state = "running" if self.active else "stopped"
        return VisualDetectionStatus(state, state, 1, 0, 0)

    def start(self) -> None:
        self.start_count += 1
        self.active = True

    def stop(self) -> None:
        if not self.active:
            return
        self.stop_count += 1
        self.active = False

    def request_stop(self) -> None:
        self.request_stop_count += 1


if __name__ == "__main__":
    unittest.main()
