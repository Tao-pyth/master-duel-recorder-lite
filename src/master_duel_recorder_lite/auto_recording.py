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


RecordingFactory = Callable[[], PreparedRecording]


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


class AutoRecordingController:
    def __init__(
        self,
        *,
        state_machine: DuelDetectionStateMachine,
        recording_factory: RecordingFactory,
    ) -> None:
        self.state_machine = state_machine
        self.recording_factory = recording_factory
        self.current: PreparedRecording | None = None

    @property
    def recording_active(self) -> bool:
        return self.current is not None and self.current.session.state is RecordingState.RECORDING

    def process(self, observation: DuelObservation) -> AutoRecordingEvent:
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
            prepared = self.recording_factory()
        except RecordingPreparationError as exc:
            self.state_machine.mark_manual_stopped(observation.observed_at)
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                f"自動録画を準備できません: {exc}",
                observation,
                decision,
            )

        try:
            state = prepared.start(source=source, detection_reason=decision.reason)
        except RecordingTrackingError as exc:
            prepared.release()
            self.state_machine.mark_manual_stopped(observation.observed_at)
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                str(exc),
                observation,
                decision,
                recording_id=prepared.target.recording_id,
            )
        if state is RecordingState.FAILED:
            result = prepared.session.result
            prepared.release()
            self.state_machine.mark_manual_stopped(observation.observed_at)
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                result.error if result and result.error else "自動録画を開始できません",
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
            self.state_machine.mark_manual_stopped(observation.observed_at)
            return AutoRecordingEvent(
                AutoRecordingEventAction.ERROR,
                str(exc),
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
            return AutoRecordingEvent(
                AutoRecordingEventAction.STOPPED,
                "FFmpegが録画を終了しました",
                observation,
                None,
                recording_id=prepared.target.recording_id,
                result=result,
            )
        return AutoRecordingEvent(
            AutoRecordingEventAction.ERROR,
            result.error if result and result.error else "FFmpegが予期せず終了しました",
            observation,
            None,
            recording_id=prepared.target.recording_id,
            result=result,
        )


def _manual_observation(reason: str, observed_at: datetime | None) -> DuelObservation:
    timestamp = observed_at or datetime.now(timezone.utc)
    return DuelObservation(
        signal=DetectionSignal.PRESENT if reason == "手動開始" else DetectionSignal.ABSENT,
        confidence=1.0,
        reason=reason,
        observed_at=timestamp,
    )
