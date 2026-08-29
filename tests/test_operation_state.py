import unittest

from master_duel_recorder_lite.operation_state import (
    OperationAction,
    OperationState,
    OperationStateMachine,
)


class OperationStateMachineTest(unittest.TestCase):
    def test_manual_recording_lifecycle_and_permissions(self) -> None:
        machine = OperationStateMachine()
        self.assertTrue(machine.snapshot.allows(OperationAction.START_MANUAL))
        machine.transition(OperationState.MANUAL_STARTING, "開始中")
        self.assertFalse(machine.snapshot.allowed_actions)
        machine.transition(OperationState.MANUAL_RECORDING, "録画中")
        self.assertTrue(machine.snapshot.allows(OperationAction.STOP_RECORDING))
        self.assertFalse(machine.snapshot.allows(OperationAction.WRITE_DUEL))
        machine.transition(OperationState.STOPPING, "停止中")
        machine.transition(OperationState.IDLE, "待機中")

    def test_watch_candidate_confirm_return_lifecycle(self) -> None:
        machine = OperationStateMachine()
        machine.transition(OperationState.WATCH_STARTING, "開始中")
        machine.transition(OperationState.WATCH_WAITING, "対戦待機中")
        machine.transition(OperationState.CANDIDATE_RECORDING, "候補録画中")
        machine.transition(OperationState.AUTOMATIC_RECORDING, "録画中")
        machine.transition(OperationState.WATCH_WAITING, "次の対戦待機中")
        self.assertTrue(machine.snapshot.allows(OperationAction.STOP_WATCH))

    def test_watch_starting_can_be_stopped(self) -> None:
        machine = OperationStateMachine()
        machine.transition(OperationState.WATCH_STARTING, "開始中")

        self.assertTrue(machine.snapshot.allows(OperationAction.STOP_WATCH))
        machine.transition(OperationState.STOPPING, "停止中")
        machine.transition(OperationState.IDLE, "待機中")

    def test_invalid_transition_and_action_are_rejected(self) -> None:
        machine = OperationStateMachine()
        with self.assertRaises(RuntimeError):
            machine.transition(OperationState.AUTOMATIC_RECORDING, "録画中")
        machine.transition(OperationState.MANUAL_STARTING, "開始中")
        with self.assertRaises(RuntimeError):
            machine.require(OperationAction.CLOSE)

    def test_failed_state_can_retry_or_return_idle(self) -> None:
        machine = OperationStateMachine()
        machine.transition(OperationState.FAILED, "失敗")
        self.assertTrue(machine.snapshot.allows(OperationAction.START_WATCH))
        machine.transition(OperationState.IDLE, "復旧")


if __name__ == "__main__":
    unittest.main()
