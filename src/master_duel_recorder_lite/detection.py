from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


class DetectionSignal(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


class DetectionAction(str, Enum):
    NONE = "none"
    START = "start"
    STOP = "stop"


@dataclass(frozen=True)
class DuelObservation:
    signal: DetectionSignal
    confidence: float
    reason: str
    observed_at: datetime
    capture_window_handle: int | None = None
    capture_process_id: int | None = None
    capture_window_title: str | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence は0.0から1.0である必要があります")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at にはタイムゾーンが必要です")
        if not self.reason.strip():
            raise ValueError("reason は空にできません")
        if self.capture_window_handle is not None and self.capture_window_handle <= 0:
            raise ValueError("capture_window_handleは正数である必要があります")
        if self.capture_process_id is not None and self.capture_process_id <= 0:
            raise ValueError("capture_process_idは正数である必要があります")
        if self.capture_window_title is not None and not self.capture_window_title.strip():
            raise ValueError("capture_window_titleは空にできません")

    @property
    def capture_target_key(self) -> tuple[int, int] | None:
        if self.capture_process_id is None or self.capture_window_handle is None:
            return None
        return (self.capture_process_id, self.capture_window_handle)


@dataclass(frozen=True)
class DetectionPolicy:
    start_confirmations: int = 3
    stop_confirmations: int = 5
    minimum_confidence: float = 0.5
    cooldown_seconds: float = 10.0
    automatic_start: bool = True
    automatic_stop: bool = True

    def __post_init__(self) -> None:
        if not 1 <= self.start_confirmations <= 60:
            raise ValueError("start_confirmations は1から60である必要があります")
        if not 1 <= self.stop_confirmations <= 60:
            raise ValueError("stop_confirmations は1から60である必要があります")
        if not 0.0 <= self.minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence は0.0から1.0である必要があります")
        if not 0.0 <= self.cooldown_seconds <= 300.0:
            raise ValueError("cooldown_seconds は0から300である必要があります")


@dataclass(frozen=True)
class DetectionDecision:
    action: DetectionAction
    reason: str
    start_count: int
    stop_count: int


class DuelDetectionStateMachine:
    def __init__(self, policy: DetectionPolicy) -> None:
        self.policy = policy
        self.recording_active = False
        self.automatic_start = policy.automatic_start
        self.automatic_stop = policy.automatic_stop
        self.start_count = 0
        self.stop_count = 0
        self.cooldown_until: datetime | None = None
        self.candidate_target: tuple[int, int] | None = None
        self.recording_target: tuple[int, int] | None = None

    def evaluate(self, observation: DuelObservation) -> DetectionDecision:
        signal = observation.signal
        if observation.confidence < self.policy.minimum_confidence:
            signal = DetectionSignal.UNKNOWN

        if self.recording_active:
            return self._evaluate_while_recording(signal, observation)
        return self._evaluate_while_idle(signal, observation)

    def mark_manual_started(self) -> None:
        self.recording_active = True
        self.start_count = 0
        self.stop_count = 0
        self.candidate_target = None
        self.recording_target = None

    def mark_manual_stopped(self, observed_at: datetime | None = None) -> None:
        stopped_at = observed_at or datetime.now(timezone.utc)
        if stopped_at.tzinfo is None:
            raise ValueError("observed_at にはタイムゾーンが必要です")
        self.recording_active = False
        self.start_count = 0
        self.stop_count = 0
        self.cooldown_until = stopped_at + timedelta(seconds=self.policy.cooldown_seconds)
        self.candidate_target = None
        self.recording_target = None

    def mark_failed(self, observed_at: datetime, *, retry_delay_seconds: float) -> None:
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_secondsは0以上である必要があります")
        self.mark_manual_stopped(observed_at)
        self.cooldown_until = observed_at + timedelta(seconds=retry_delay_seconds)

    def set_automatic_start(self, enabled: bool) -> None:
        self.automatic_start = enabled
        if not enabled:
            self.start_count = 0

    def set_automatic_stop(self, enabled: bool) -> None:
        self.automatic_stop = enabled
        if not enabled:
            self.stop_count = 0

    def _evaluate_while_idle(
        self,
        signal: DetectionSignal,
        observation: DuelObservation,
    ) -> DetectionDecision:
        self.stop_count = 0
        if not self.automatic_start or signal is not DetectionSignal.PRESENT:
            self.start_count = 0
            self.candidate_target = None
            return self._none(observation.reason)
        if self.cooldown_until is not None and observation.observed_at < self.cooldown_until:
            self.start_count = 0
            return self._none("停止後のクールダウン中です")

        target = observation.capture_target_key
        if self.candidate_target != target:
            self.candidate_target = target
            self.start_count = 0

        self.start_count += 1
        if self.start_count < self.policy.start_confirmations:
            return self._none(
                f"開始候補を確認中です ({self.start_count}/{self.policy.start_confirmations})"
            )

        self.recording_active = True
        self.recording_target = target
        self.candidate_target = None
        self.start_count = 0
        return DetectionDecision(DetectionAction.START, observation.reason, 0, 0)

    def _evaluate_while_recording(
        self,
        signal: DetectionSignal,
        observation: DuelObservation,
    ) -> DetectionDecision:
        self.start_count = 0
        if (
            signal is DetectionSignal.PRESENT
            and self.recording_target is not None
            and observation.capture_target_key != self.recording_target
        ):
            signal = DetectionSignal.ABSENT
        if not self.automatic_stop or signal is not DetectionSignal.ABSENT:
            self.stop_count = 0
            return self._none(observation.reason)

        self.stop_count += 1
        if self.stop_count < self.policy.stop_confirmations:
            return self._none(
                f"終了候補を確認中です ({self.stop_count}/{self.policy.stop_confirmations})"
            )

        self.recording_active = False
        self.stop_count = 0
        self.cooldown_until = observation.observed_at + timedelta(seconds=self.policy.cooldown_seconds)
        self.recording_target = None
        return DetectionDecision(DetectionAction.STOP, observation.reason, 0, 0)

    def _none(self, reason: str) -> DetectionDecision:
        return DetectionDecision(DetectionAction.NONE, reason, self.start_count, self.stop_count)
