import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from master_duel_recorder_lite.auto_recording import AutoRecordingController, AutoRecordingEventAction
from master_duel_recorder_lite.detection import (
    DetectionPolicy,
    DetectionSignal,
    DuelDetectionStateMachine,
    DuelObservation,
)
from master_duel_recorder_lite.recorder import RecordingPreparationError
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState


BASE_TIME = datetime(2026, 8, 8, tzinfo=timezone.utc)


def observation(signal: DetectionSignal, second: int) -> DuelObservation:
    return DuelObservation(signal, 1.0, signal.value, BASE_TIME + timedelta(seconds=second))


def result(state: RecordingState, error: str | None = None) -> RecordingResult:
    return RecordingResult(
        state=state,
        output_path=Path("recording.mkv"),
        returncode=0 if state is RecordingState.COMPLETED else 1,
        started_at=BASE_TIME,
        ended_at=BASE_TIME,
        size_bytes=100 if state is RecordingState.COMPLETED else 0,
        error=error,
        diagnostics=(),
    )


class FakeSession:
    def __init__(self, *, start_state: RecordingState = RecordingState.RECORDING) -> None:
        self.state = RecordingState.CREATED
        self.result: RecordingResult | None = None
        self.start_state = start_state

    def start(self) -> RecordingState:
        self.state = self.start_state
        if self.state is RecordingState.FAILED:
            self.result = result(RecordingState.FAILED, "start failed")
        return self.state

    def poll(self) -> RecordingState:
        return self.state

    def stop(self) -> RecordingResult:
        self.state = RecordingState.COMPLETED
        self.result = result(RecordingState.COMPLETED)
        return self.result


class FakePrepared:
    def __init__(self, recording_id: str = "id", session: FakeSession | None = None) -> None:
        self.target = SimpleNamespace(recording_id=recording_id)
        self.session = session or FakeSession()
        self.release_count = 0

    def release(self) -> None:
        self.release_count += 1

    def start(self, *, source: str, detection_reason: str | None = None) -> RecordingState:
        return self.session.start()

    def poll(self) -> RecordingState:
        return self.session.poll()

    def stop(self) -> RecordingResult:
        return self.session.stop()


class AutoRecordingControllerTest(unittest.TestCase):
    def test_scenario_starts_and_stops_once(self) -> None:
        machine = DuelDetectionStateMachine(
            DetectionPolicy(start_confirmations=2, stop_confirmations=2, cooldown_seconds=10)
        )
        prepared = FakePrepared()
        controller = AutoRecordingController(
            state_machine=machine,
            recording_factory=lambda _observation: prepared,
        )

        first = controller.process(observation(DetectionSignal.PRESENT, 0))
        started = controller.process(observation(DetectionSignal.PRESENT, 1))
        temporary_minimize = controller.process(observation(DetectionSignal.ABSENT, 2))
        restored = controller.process(observation(DetectionSignal.PRESENT, 3))
        ending = controller.process(observation(DetectionSignal.ABSENT, 4))
        stopped = controller.process(observation(DetectionSignal.ABSENT, 5))

        self.assertIs(first.action, AutoRecordingEventAction.NONE)
        self.assertIs(started.action, AutoRecordingEventAction.STARTED)
        self.assertIs(temporary_minimize.action, AutoRecordingEventAction.NONE)
        self.assertIs(restored.action, AutoRecordingEventAction.NONE)
        self.assertIs(ending.action, AutoRecordingEventAction.NONE)
        self.assertIs(stopped.action, AutoRecordingEventAction.STOPPED)
        self.assertEqual(prepared.release_count, 1)
        self.assertIsNone(controller.current)

    def test_preparation_failure_rolls_back_detection_state(self) -> None:
        machine = DuelDetectionStateMachine(DetectionPolicy(start_confirmations=1))

        def fail(_observation: DuelObservation) -> object:
            raise RecordingPreparationError("busy")

        controller = AutoRecordingController(
            state_machine=machine,
            recording_factory=fail,  # type: ignore[arg-type]
        )
        event = controller.process(observation(DetectionSignal.PRESENT, 0))

        self.assertIs(event.action, AutoRecordingEventAction.ERROR)
        self.assertFalse(machine.recording_active)
        self.assertIsNone(controller.current)

    def test_start_failure_releases_prepared_recording(self) -> None:
        machine = DuelDetectionStateMachine(DetectionPolicy(start_confirmations=1))
        prepared = FakePrepared(session=FakeSession(start_state=RecordingState.FAILED))
        controller = AutoRecordingController(
            state_machine=machine,
            recording_factory=lambda _observation: prepared,
        )

        event = controller.process(observation(DetectionSignal.PRESENT, 0))

        self.assertIs(event.action, AutoRecordingEventAction.ERROR)
        self.assertEqual(prepared.release_count, 1)
        self.assertFalse(machine.recording_active)

    def test_unexpected_process_failure_is_collected(self) -> None:
        machine = DuelDetectionStateMachine(DetectionPolicy(start_confirmations=1))
        prepared = FakePrepared()
        controller = AutoRecordingController(
            state_machine=machine,
            recording_factory=lambda _observation: prepared,
        )
        controller.process(observation(DetectionSignal.PRESENT, 0))
        prepared.session.state = RecordingState.FAILED
        prepared.session.result = result(RecordingState.FAILED, "encoder crashed")

        event = controller.process(observation(DetectionSignal.PRESENT, 1))

        self.assertIs(event.action, AutoRecordingEventAction.ERROR)
        self.assertIn("encoder crashed", event.message)
        self.assertEqual(prepared.release_count, 1)
        self.assertFalse(machine.recording_active)

    def test_manual_start_and_stop_take_effect_immediately(self) -> None:
        machine = DuelDetectionStateMachine(DetectionPolicy(start_confirmations=10, stop_confirmations=10))
        prepared = FakePrepared()
        controller = AutoRecordingController(
            state_machine=machine,
            recording_factory=lambda _observation: prepared,
        )

        started = controller.manual_start(BASE_TIME)
        stopped = controller.manual_stop(BASE_TIME + timedelta(seconds=1))

        self.assertIs(started.action, AutoRecordingEventAction.STARTED)
        self.assertIs(stopped.action, AutoRecordingEventAction.STOPPED)
        self.assertEqual(prepared.release_count, 1)


if __name__ == "__main__":
    unittest.main()
