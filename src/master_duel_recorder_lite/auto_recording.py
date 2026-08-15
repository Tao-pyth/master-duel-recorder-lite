from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .detection import (
    DetectionAction,
    DetectionDecision,
    DetectionSignal,
    DuelDetectionStateMachine,
    DuelObservation,
)
from .recorder import PreparedRecording, RecordingPreparationError, RecordingTrackingError
from .recording_session import RecordingResult, RecordingState
from .visual_detection import DetectionCandidate


RecordingFactory = Callable[[DuelObservation], PreparedRecording]


class AutoRecordingEventAction(str, Enum):
    NONE = "none"
    STARTED = "started"
    STOPPED = "stopped"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class AutoRecordingEvent:
    action: AutoRecordingEventAction
    message: str
    observation: DuelObservation | None
    decision: DetectionDecision | None
    recording_id: str | None = None
    result: RecordingResult | None = None


@dataclass(frozen=True)
class AutoRetryPolicy:
    base_delay_seconds: float = 10.0
    maximum_delay_seconds: float = 300.0
    maximum_attempts: int = 3

    def __post_init__(self) -> None:
        if self.base_delay_seconds <= 0 or self.maximum_delay_seconds <= 0:
            raise ValueError("再試行待機時間は0より大きい必要があります")
        if self.base_delay_seconds > self.maximum_delay_seconds:
            raise ValueError("再試行の初期待機時間は最大待機時間以下である必要があります")
        if self.maximum_attempts < 1:
            raise ValueError("maximum_attemptsは1以上である必要があります")


class AutoRecordingController:
    def __init__(
        self,
        *,
        state_machine: DuelDetectionStateMachine,
        recording_factory: RecordingFactory,
        retry_policy: AutoRetryPolicy | None = None,
    ) -> None:
        self.state_machine = state_machine
        self.recording_factory = recording_factory
        self.retry_policy = retry_policy or AutoRetryPolicy()
        self.current: PreparedRecording | None = None
        self.consecutive_failures = 0
        self.retry_blocked = False
        self._blocked_reported = False

    @property
    def recording_active(self) -> bool:
        return self.current is not None and self.current.session.state is RecordingState.RECORDING

    def process(self, observation: DuelObservation) -> AutoRecordingEvent:
        if self.retry_blocked:
            if observation.signal is DetectionSignal.ABSENT:
                self.retry_blocked = False
                self.consecutive_failures = 0
                self._blocked_reported = False
                self.state_machine.mark_manual_stopped(observation.observed_at)
            else:
                if self._blocked_reported:
                    return AutoRecordingEvent(
                        AutoRecordingEventAction.NONE,
                        "FFmpegの連続失敗により自動開始を停止中です",
                        observation,
                        None,
                    )
                self._blocked_reported = True
                return AutoRecordingEvent(
                    AutoRecordingEventAction.SKIPPED,
                    "FFmpegの連続失敗により自動開始を停止しています。ゲーム画面の終了後に再開します",
                    observation,
                    None,
                )
        terminal_event = self._collect_terminal_session(observation)
        if terminal_event is not None:
            return terminal_event

        decision = self.state_machine.evaluate(observation)
        if decision.action is DetectionAction.START:
            return self._start(observation, decision, source="automatic")
        if decision.action is DetectionAction.STOP:
            return self._stop(observation, decision)
        return AutoRecordingEvent(AutoRecordingEventAction.NONE, decision.reason, observation, decision)

    def manual_start(self, observed_at: datetime | None = None) -> AutoRecordingEvent:
        observation = _manual_observation("手動開始", observed_at)
        self.state_machine.mark_manual_started()
        decision = DetectionDecision(DetectionAction.START, observation.reason, 0, 0)
        return self._start(observation, decision, source="manual")

    def manual_stop(self, observed_at: datetime | None = None) -> AutoRecordingEvent:
        observation = _manual_observation("手動停止", observed_at)
        self.state_machine.mark_manual_stopped(observation.observed_at)
        decision = DetectionDecision(DetectionAction.STOP, observation.reason, 0, 0)
        return self._stop(observation, decision)

    def start_from_boundary(
        self,
        observation: DuelObservation,
        candidate: DetectionCandidate,
    ) -> AutoRecordingEvent:
        """前録画で検出した次対戦境界を、次録画の開始根拠として引き継ぎます。"""
        if self.current is not None:
            return AutoRecordingEvent(
                AutoRecordingEventAction.SKIPPED,
                "前の録画が停止していないため次対戦へ引き継げません",
                observation,
                None,
                recording_id=self.current.target.recording_id,
            )
        self.state_machine.mark_manual_started()
        decision = DetectionDecision(
            DetectionAction.START,
            f"次対戦境界から録画を引き継ぎました: {candidate.reason}",
            1,
            1,
        )
        return self._start(observation, decision, source="automatic")

    def set_automatic_start(self, enabled: bool) -> None:
        self.state_machine.set_automatic_start(enabled)

    def set_automatic_stop(self, enabled: bool) -> None:
        self.state_machine.set_automatic_stop(enabled)

    def _start(
        self,
        observation: DuelObservation,
        decision: DetectionDecision,
        *,
        source: str,
    ) -> AutoRecordingEvent:
        if self.current is not None:
            return AutoRecordingEvent(
                AutoRecordingEventAction.SKIPPED,
                "録画セッションが既に存在するため開始しません",
                observation,
                decision,
                recording_id=self.current.target.recording_id,
            )
        try:
            prepared = self.recording_factory(observation)
        except RecordingPreparationError as exc:
            retry = self._record_automatic_failure(observation, source)
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                f"自動録画を準備できません: {exc}{retry}",
                observation,
                decision,
            )

        try:
            state = prepared.start(source=source, detection_reason=decision.reason)
        except RecordingTrackingError as exc:
            prepared.release()
            retry = self._record_automatic_failure(observation, source)
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                f"{exc}{retry}",
                observation,
                decision,
                recording_id=prepared.target.recording_id,
            )
        if state is RecordingState.FAILED:
            result = prepared.session.result
            prepared.release()
            retry = self._record_automatic_failure(observation, source)
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                f"{result.error if result and result.error else '自動録画を開始できません'}{retry}",
                observation,
                decision,
                recording_id=prepared.target.recording_id,
                result=result,
            )

        self.current = prepared
        return AutoRecordingEvent(
            AutoRecordingEventAction.STARTED,
            decision.reason,
            observation,
            decision,
            recording_id=prepared.target.recording_id,
        )

    def _stop(
        self,
        observation: DuelObservation,
        decision: DetectionDecision,
    ) -> AutoRecordingEvent:
        if self.current is None:
            return AutoRecordingEvent(
                AutoRecordingEventAction.SKIPPED,
                "実行中の録画がないため停止しません",
                observation,
                decision,
            )

        prepared = self.current
        try:
            result = prepared.stop()
        except RecordingTrackingError as exc:
            prepared.release()
            self.current = None
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                str(exc),
                observation,
                decision,
                recording_id=prepared.target.recording_id,
                result=prepared.session.result,
            )
        prepared.release()
        self.current = None
        if not result.succeeded:
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                result.error or "録画停止に失敗しました",
                observation,
                decision,
                recording_id=prepared.target.recording_id,
                result=result,
            )
        self.consecutive_failures = 0
        return AutoRecordingEvent(
            AutoRecordingEventAction.STOPPED,
            decision.reason,
            observation,
            decision,
            recording_id=prepared.target.recording_id,
            result=result,
        )

    def _collect_terminal_session(self, observation: DuelObservation) -> AutoRecordingEvent | None:
        if self.current is None:
            return None
        try:
            state = self.current.poll()
        except RecordingTrackingError as exc:
            prepared = self.current
            prepared.release()
            self.current = None
            retry = self._record_automatic_failure(observation, "automatic")
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                f"{exc}{retry}",
                observation,
                None,
                recording_id=prepared.target.recording_id,
                result=prepared.session.result,
            )
        if state not in {RecordingState.COMPLETED, RecordingState.FAILED}:
            return None

        prepared = self.current
        result = prepared.session.result
        prepared.release()
        self.current = None
        self.state_machine.mark_manual_stopped(observation.observed_at)
        if result is not None and result.succeeded:
            self.consecutive_failures = 0
            return AutoRecordingEvent(
                AutoRecordingEventAction.STOPPED,
                "FFmpegが録画を終了しました",
                observation,
                None,
                recording_id=prepared.target.recording_id,
                result=result,
            )
        retry = self._record_automatic_failure(observation, "automatic")
        return AutoRecordingEvent(
            AutoRecordingEventAction.ERROR,
            f"{result.error if result and result.error else 'FFmpegが予期せず終了しました'}{retry}",
            observation,
            None,
            recording_id=prepared.target.recording_id,
            result=result,
        )

    def _record_automatic_failure(self, observation: DuelObservation, source: str) -> str:
        if source != "automatic":
            self.state_machine.mark_manual_stopped(observation.observed_at)
            return ""
        self.consecutive_failures += 1
        delay = min(
            self.retry_policy.maximum_delay_seconds,
            self.retry_policy.base_delay_seconds * (2 ** (self.consecutive_failures - 1)),
        )
        self.state_machine.mark_failed(observation.observed_at, retry_delay_seconds=delay)
        if self.consecutive_failures >= self.retry_policy.maximum_attempts:
            self.retry_blocked = True
            self._blocked_reported = False
            return "。連続失敗の上限に達したため、ゲーム画面が終了するまで自動開始を停止します"
        return f"。{delay:g}秒後に再試行できます ({self.consecutive_failures}/{self.retry_policy.maximum_attempts})"


def _manual_observation(reason: str, observed_at: datetime | None) -> DuelObservation:
    timestamp = observed_at or datetime.now(timezone.utc)
    return DuelObservation(
        signal=DetectionSignal.PRESENT if reason == "手動開始" else DetectionSignal.ABSENT,
        confidence=1.0,
        reason=reason,
        observed_at=timestamp,
    )
