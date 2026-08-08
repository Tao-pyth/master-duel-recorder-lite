import unittest
from datetime import datetime, timedelta, timezone

from master_duel_recorder_lite.detection import (
    DetectionAction,
    DetectionPolicy,
    DetectionSignal,
    DuelDetectionStateMachine,
    DuelObservation,
)


BASE_TIME = datetime(2026, 8, 8, tzinfo=timezone.utc)


def observation(signal: DetectionSignal, second: int, confidence: float = 1.0) -> DuelObservation:
    return DuelObservation(signal, confidence, signal.value, BASE_TIME + timedelta(seconds=second))


class DetectionStateMachineTest(unittest.TestCase):
    def test_consecutive_present_observations_start_once(self) -> None:
        machine = DuelDetectionStateMachine(DetectionPolicy(start_confirmations=3))

        first = machine.evaluate(observation(DetectionSignal.PRESENT, 0))
        second = machine.evaluate(observation(DetectionSignal.PRESENT, 1))
        third = machine.evaluate(observation(DetectionSignal.PRESENT, 2))
        fourth = machine.evaluate(observation(DetectionSignal.PRESENT, 3))

        self.assertIs(first.action, DetectionAction.NONE)
        self.assertIs(second.action, DetectionAction.NONE)
        self.assertIs(third.action, DetectionAction.START)
        self.assertIs(fourth.action, DetectionAction.NONE)

    def test_noise_resets_start_confirmation(self) -> None:
        machine = DuelDetectionStateMachine(DetectionPolicy(start_confirmations=2))

        machine.evaluate(observation(DetectionSignal.PRESENT, 0))
        machine.evaluate(observation(DetectionSignal.UNKNOWN, 1))
        decision = machine.evaluate(observation(DetectionSignal.PRESENT, 2))

        self.assertIs(decision.action, DetectionAction.NONE)
        self.assertEqual(decision.start_count, 1)

    def test_consecutive_absent_observations_stop(self) -> None:
        machine = DuelDetectionStateMachine(
            DetectionPolicy(start_confirmations=1, stop_confirmations=2, cooldown_seconds=10)
        )
        machine.evaluate(observation(DetectionSignal.PRESENT, 0))

        first = machine.evaluate(observation(DetectionSignal.ABSENT, 1))
        second = machine.evaluate(observation(DetectionSignal.ABSENT, 2))

        self.assertIs(first.action, DetectionAction.NONE)
        self.assertIs(second.action, DetectionAction.STOP)
        self.assertFalse(machine.recording_active)

    def test_unknown_does_not_stop_active_recording(self) -> None:
        machine = DuelDetectionStateMachine(DetectionPolicy(start_confirmations=1, stop_confirmations=2))
        machine.evaluate(observation(DetectionSignal.PRESENT, 0))
        machine.evaluate(observation(DetectionSignal.ABSENT, 1))

        decision = machine.evaluate(observation(DetectionSignal.UNKNOWN, 2))

        self.assertIs(decision.action, DetectionAction.NONE)
        self.assertEqual(decision.stop_count, 0)
        self.assertTrue(machine.recording_active)

    def test_cooldown_blocks_immediate_restart(self) -> None:
        machine = DuelDetectionStateMachine(
            DetectionPolicy(start_confirmations=1, stop_confirmations=1, cooldown_seconds=10)
        )
        machine.evaluate(observation(DetectionSignal.PRESENT, 0))
        machine.evaluate(observation(DetectionSignal.ABSENT, 1))

        blocked = machine.evaluate(observation(DetectionSignal.PRESENT, 5))
        allowed = machine.evaluate(observation(DetectionSignal.PRESENT, 11))

        self.assertIs(blocked.action, DetectionAction.NONE)
        self.assertIs(allowed.action, DetectionAction.START)

    def test_manual_override_and_automatic_toggles(self) -> None:
        machine = DuelDetectionStateMachine(DetectionPolicy(start_confirmations=1, stop_confirmations=1))
        machine.set_automatic_start(False)
        self.assertIs(
            machine.evaluate(observation(DetectionSignal.PRESENT, 0)).action,
            DetectionAction.NONE,
        )

        machine.mark_manual_started()
        machine.set_automatic_stop(False)
        self.assertIs(
            machine.evaluate(observation(DetectionSignal.ABSENT, 1)).action,
            DetectionAction.NONE,
        )
        machine.mark_manual_stopped(observation(DetectionSignal.ABSENT, 2).observed_at)
        self.assertFalse(machine.recording_active)


if __name__ == "__main__":
    unittest.main()
