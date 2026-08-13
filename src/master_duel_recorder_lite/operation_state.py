from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading


class OperationState(str, Enum):
    IDLE = "idle"
    MANUAL_STARTING = "manual_starting"
    MANUAL_RECORDING = "manual_recording"
    WATCH_STARTING = "watch_starting"
    WATCH_WAITING = "watch_waiting"
    CANDIDATE_RECORDING = "candidate_recording"
    AUTOMATIC_RECORDING = "automatic_recording"
    STOPPING = "stopping"
    FAILED = "failed"
    CLOSING = "closing"


class OperationAction(str, Enum):
    START_MANUAL = "start_manual"
    STOP_RECORDING = "stop_recording"
    START_WATCH = "start_watch"
    STOP_WATCH = "stop_watch"
    WRITE_DUEL = "write_duel"
    MANAGE_DATA = "manage_data"
    CLOSE = "close"


@dataclass(frozen=True)
class OperationSnapshot:
    state: OperationState
    message: str
    allowed_actions: frozenset[OperationAction]

    def allows(self, action: OperationAction) -> bool:
        return action in self.allowed_actions


_ALLOWED = {
    OperationState.IDLE: frozenset(
        {
            OperationAction.START_MANUAL,
            OperationAction.START_WATCH,
            OperationAction.WRITE_DUEL,
            OperationAction.MANAGE_DATA,
            OperationAction.CLOSE,
        }
    ),
    OperationState.MANUAL_STARTING: frozenset(),
    OperationState.MANUAL_RECORDING: frozenset(
        {OperationAction.STOP_RECORDING, OperationAction.CLOSE}
    ),
    OperationState.WATCH_STARTING: frozenset(),
    OperationState.WATCH_WAITING: frozenset(
        {OperationAction.STOP_WATCH, OperationAction.CLOSE}
    ),
    OperationState.CANDIDATE_RECORDING: frozenset(
        {OperationAction.STOP_WATCH, OperationAction.CLOSE}
    ),
    OperationState.AUTOMATIC_RECORDING: frozenset(
        {OperationAction.STOP_WATCH, OperationAction.CLOSE}
    ),
    OperationState.STOPPING: frozenset(),
    OperationState.FAILED: frozenset(
        {
            OperationAction.START_MANUAL,
            OperationAction.START_WATCH,
            OperationAction.WRITE_DUEL,
            OperationAction.MANAGE_DATA,
            OperationAction.CLOSE,
        }
    ),
    OperationState.CLOSING: frozenset(),
}


_TRANSITIONS = {
    OperationState.IDLE: {
        OperationState.MANUAL_STARTING,
        OperationState.WATCH_STARTING,
        OperationState.CLOSING,
        OperationState.FAILED,
    },
    OperationState.MANUAL_STARTING: {
        OperationState.MANUAL_RECORDING,
        OperationState.IDLE,
        OperationState.FAILED,
    },
    OperationState.MANUAL_RECORDING: {
        OperationState.STOPPING,
        OperationState.CLOSING,
        OperationState.FAILED,
    },
    OperationState.WATCH_STARTING: {
        OperationState.WATCH_WAITING,
        OperationState.IDLE,
        OperationState.FAILED,
    },
    OperationState.WATCH_WAITING: {
        OperationState.CANDIDATE_RECORDING,
        OperationState.STOPPING,
        OperationState.CLOSING,
        OperationState.FAILED,
    },
    OperationState.CANDIDATE_RECORDING: {
        OperationState.AUTOMATIC_RECORDING,
        OperationState.WATCH_WAITING,
        OperationState.STOPPING,
        OperationState.CLOSING,
        OperationState.FAILED,
    },
    OperationState.AUTOMATIC_RECORDING: {
        OperationState.WATCH_WAITING,
        OperationState.STOPPING,
        OperationState.CLOSING,
        OperationState.FAILED,
    },
    OperationState.STOPPING: {
        OperationState.IDLE,
        OperationState.WATCH_WAITING,
        OperationState.FAILED,
    },
    OperationState.FAILED: {
        OperationState.IDLE,
        OperationState.MANUAL_STARTING,
        OperationState.WATCH_STARTING,
        OperationState.CLOSING,
    },
    OperationState.CLOSING: {OperationState.IDLE, OperationState.FAILED},
}


class OperationStateMachine:
    def __init__(self) -> None:
        self._state = OperationState.IDLE
        self._message = "待機中"
        self._lock = threading.RLock()

    @property
    def snapshot(self) -> OperationSnapshot:
        with self._lock:
            return OperationSnapshot(
                self._state, self._message, _ALLOWED[self._state]
            )

    def require(self, action: OperationAction) -> None:
        snapshot = self.snapshot
        if not snapshot.allows(action):
            raise RuntimeError(
                f"現在の状態では操作できません: {snapshot.state.value} / {action.value}"
            )

    def transition(
        self, state: OperationState, message: str
    ) -> OperationSnapshot:
        normalized = message.strip()
        if not normalized:
            raise ValueError("状態メッセージは空にできません")
        with self._lock:
            if state is self._state:
                self._message = normalized
                return self.snapshot
            if state not in _TRANSITIONS[self._state]:
                raise RuntimeError(
                    f"不正な状態遷移です: {self._state.value} -> {state.value}"
                )
            self._state = state
            self._message = normalized
            return self.snapshot
