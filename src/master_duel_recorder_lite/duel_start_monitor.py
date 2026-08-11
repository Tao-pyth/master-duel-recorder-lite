from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

from .detection import DetectionSignal, DuelObservation
from .frame_capture import FrameCaptureResult
from .game_window import WindowSnapshot
from .master_duel_detector import MasterDuelWindowDetector
from .visual_detection import (
    DetectionCandidate,
    DuelStartDetector,
    FrameAnalysis,
    MasterDuelVisualEventDetector,
    TemporalEventConsensus,
    VisualDetectionPipeline,
)
from .visual_worker import VisualDetectionStatus


FrameCapture = Callable[[WindowSnapshot], FrameCaptureResult]
PipelineFactory = Callable[[], VisualDetectionPipeline]
AnalysisCallback = Callable[[FrameAnalysis], None]


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
        on_analysis: AnalysisCallback | None = None,
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
        self.on_analysis = on_analysis
        self.processed_frames = 0
        self.dropped_frames = 0
        self._target: tuple[int, int, int, int, int, int] | None = None
        self._observed_from: datetime | None = None
        self._pipeline = self.pipeline_factory()
        self._candidate: DetectionCandidate | None = None
        self._last_analysis: FrameAnalysis | None = None
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
        target_key = observation.capture_target_key
        if observation.signal is not DetectionSignal.PRESENT or target_key is None:
            self.reset(message=observation.reason)
            return observation

        target = (
            target_key[0],
            target_key[1],
            observation.capture_left or 0,
            observation.capture_top or 0,
            observation.capture_width or 0,
            observation.capture_height or 0,
        )

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
            width=observation.capture_width or 0,
            height=observation.capture_height or 0,
            left=observation.capture_left or 0,
            top=observation.capture_top or 0,
        )
        result = self.capture(window)
        if not result.succeeded:
            self.dropped_frames += 1
            consensus = getattr(self._pipeline, "consensus", None)
            if consensus is not None:
                consensus.process(())
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
        analyze_frame = getattr(self._pipeline, "analyze_frame", None)
        if callable(analyze_frame):
            self._last_analysis = analyze_frame(result.sample, elapsed_ms)
            if self.on_analysis is not None:
                self.on_analysis(self._last_analysis)
            candidates = self._last_analysis.candidates
        else:
            candidates = self._pipeline.analyze(result.sample, elapsed_ms)
        self._candidate = next(
            (candidate for candidate in candidates if candidate.event_type == "duel_start"),
            None,
        )
        if self._candidate is None:
            elapsed_seconds = elapsed_ms // 1000
            self._publish("waiting", f"対戦開始を判定中です ({elapsed_seconds}s)")
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
        self._last_analysis = None
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
        analysis = self._last_analysis
        capture_owner = getattr(self.capture, "__self__", None)
        source = getattr(capture_owner, "source_description", "")
        restart_count = int(getattr(capture_owner, "restart_count", 0))
        elapsed = (
            max(0.001, (datetime.now(timezone.utc) - self._observed_from).total_seconds())
            if self._observed_from is not None
            else 0.0
        )
        self._status = VisualDetectionStatus(
            state,
            message,
            self.processed_frames,
            self.dropped_frames,
            candidates,
            effective_fps=self.processed_frames / elapsed if elapsed else 0.0,
            source=source,
            resolution=(
                f"{analysis.source_width}x{analysis.source_height}" if analysis is not None else ""
            ),
            profile=analysis.profile_name if analysis is not None else "unknown",
            visual_state=analysis.state.value if analysis is not None else "idle",
            coin_score=analysis.coin_score if analysis is not None else 0.0,
            board_score=analysis.board_score if analysis is not None else 0.0,
            turn_score=analysis.turn_score if analysis is not None else 0.0,
            turn_order_score=analysis.turn_order_score if analysis is not None else 0.0,
            result_score=analysis.result_score if analysis is not None else 0.0,
            error_score=analysis.error_score if analysis is not None else 0.0,
            replay_score=analysis.replay_score if analysis is not None else 0.0,
            overlay_score=analysis.overlay_score if analysis is not None else 0.0,
            loading_score=analysis.loading_score if analysis is not None else 0.0,
            agreement=(
                ", ".join(
                    f"{item.event_type}:{item.matched}/{item.required}"
                    for item in analysis.agreements
                )
                if analysis is not None
                else ""
            ),
            restart_count=restart_count,
        )
