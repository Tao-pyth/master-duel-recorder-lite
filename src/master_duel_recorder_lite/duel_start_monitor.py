from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from .detection import DetectionSignal, DuelObservation
from .frame_capture import FrameCaptureResult
from .game_window import WindowSnapshot
from .master_duel_detector import MasterDuelWindowDetector
from .visual_detection import (
    DetectionCandidate,
    DuelStartDetector,
    MasterDuelVisualEventDetector,
    TemporalEventConsensus,
    VisualDetectionPipeline,
)
from .visual_worker import VisualDetectionStatus


FrameCapture = Callable[[WindowSnapshot], FrameCaptureResult]
PipelineFactory = Callable[[], VisualDetectionPipeline]


class MasterDuelStartMonitor:
    """同一Master Duelウィンドウの対戦開始を録画前に確認します。"""

    def __init__(
        self,
        window_detector: MasterDuelWindowDetector,
        *,
        capture: FrameCapture,
        minimum_confidence: float = 0.70,
        confirmations: int = 3,
        pipeline_factory: PipelineFactory | None = None,
    ) -> None:
        if not 0.70 <= minimum_confidence <= 1.0:
            raise ValueError("対戦開始判定の信頼度は0.70から1.0である必要があります")
        if not 2 <= confirmations <= 60:
            raise ValueError("対戦開始判定は2から60フレームの合意が必要です")
        self.window_detector = window_detector
        self.capture = capture
        self.minimum_confidence = minimum_confidence
        self.confirmations = confirmations
        self.pipeline_factory = pipeline_factory or self._default_pipeline
        self.processed_frames = 0
        self.dropped_frames = 0
        self._target: tuple[int, int] | None = None
        self._observed_from: datetime | None = None
        self._pipeline = self.pipeline_factory()
        self._candidate: DetectionCandidate | None = None
        self._status = VisualDetectionStatus(
            "waiting",
            "Master Duelの対戦開始を待っています",
            0,
            0,
            0,
        )

    @property
    def start_candidate(self) -> DetectionCandidate | None:
        return self._candidate

    @property
    def status(self) -> VisualDetectionStatus:
        return self._status

    def observe(self) -> DuelObservation:
        observation = self.window_detector.observe()
        target = observation.capture_target_key
        if observation.signal is not DetectionSignal.PRESENT or target is None:
            self.reset(message=observation.reason)
            return observation

        if target != self._target:
            self.reset(message="対象ウィンドウを固定し、対戦開始の確認を始めます")
            self._target = target
            self._observed_from = observation.observed_at

        if self._candidate is not None:
            self._publish("detected", "対戦開始を確認しました", candidates=1)
            return replace(
                observation,
                confidence=self._candidate.confidence,
                reason=self._candidate.reason,
            )

        assert observation.capture_window_handle is not None
        assert observation.capture_process_id is not None
        assert observation.capture_window_title is not None
        window = WindowSnapshot(
            handle=observation.capture_window_handle,
            pid=observation.capture_process_id,
            title=observation.capture_window_title,
            visible=True,
            minimized=False,
            width=0,
            height=0,
        )
        result = self.capture(window)
        if not result.succeeded:
            self.dropped_frames += 1
            self._pipeline = self.pipeline_factory()
            message = result.error or "判定用フレームを取得できません"
            self._publish("degraded", message)
            return replace(
                observation,
                signal=DetectionSignal.UNKNOWN,
                confidence=0.0,
                reason=message,
            )

        assert result.sample is not None
        self.processed_frames += 1
        observed_from = self._observed_from or observation.observed_at
        elapsed_ms = max(
            0,
            round((result.sample.captured_at - observed_from).total_seconds() * 1000),
        )
        candidates = self._pipeline.analyze(result.sample, elapsed_ms)
        self._candidate = next(
            (candidate for candidate in candidates if candidate.event_type == "duel_start"),
            None,
        )
        if self._candidate is None:
            self._publish("waiting", "対戦開始を判定中です")
            return replace(
                observation,
                signal=DetectionSignal.UNKNOWN,
                confidence=0.0,
                reason="Master Duelウィンドウは表示中ですが、対戦開始は未確認です",
            )

        self._publish("detected", "対戦開始を確認しました", candidates=1)
        return replace(
            observation,
            confidence=self._candidate.confidence,
            reason=self._candidate.reason,
        )

    def reset(self, *, message: str = "Master Duelの対戦開始を待っています") -> None:
        self._target = None
        self._observed_from = None
        self._pipeline = self.pipeline_factory()
        self._candidate = None
        self._publish("waiting", message)

    def _default_pipeline(self) -> VisualDetectionPipeline:
        return VisualDetectionPipeline(
            detector=MasterDuelVisualEventDetector(
                detectors=(DuelStartDetector(),),
            ),
            consensus=TemporalEventConsensus(
                minimum_confidence=self.minimum_confidence,
                confirmations=self.confirmations,
            ),
        )

    def _publish(self, state: str, message: str, *, candidates: int = 0) -> None:
        self._status = VisualDetectionStatus(
            state,
            message,
            self.processed_frames,
            self.dropped_frames,
            candidates,
        )
