from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import threading
import time

from .frame_capture import FrameCaptureResult, FrameSample
from .visual_detection import DetectionCandidate, FrameAnalysis


FrameCapture = Callable[[], FrameCaptureResult]
FrameAnalyzer = Callable[[FrameSample, int], tuple[DetectionCandidate, ...] | FrameAnalysis]
CandidateCallback = Callable[[DetectionCandidate], None]
AnalysisCallback = Callable[[FrameAnalysis], None]
StatusCallback = Callable[["VisualDetectionStatus"], None]
MonotonicClock = Callable[[], float]
RestartCounter = Callable[[], int]


@dataclass(frozen=True)
class VisualDetectionStatus:
    state: str
    message: str
    processed_frames: int
    dropped_frames: int
    candidate_count: int
    effective_fps: float = 0.0
    source: str = ""
    resolution: str = ""
    profile: str = "unknown"
    visual_state: str = "idle"
    coin_score: float = 0.0
    board_score: float = 0.0
    turn_score: float = 0.0
    turn_order_score: float = 0.0
    result_score: float = 0.0
    error_score: float = 0.0
    replay_score: float = 0.0
    overlay_score: float = 0.0
    loading_score: float = 0.0
    agreement: str = ""
    restart_count: int = 0


class VisualDetectionWorker:
    def __init__(
        self,
        *,
        recording_started_at: datetime,
        capture: FrameCapture,
        analyze: FrameAnalyzer,
        on_candidate: CandidateCallback,
        on_status: StatusCallback | None = None,
        on_analysis: AnalysisCallback | None = None,
        maximum_fps: float = 2.0,
        monotonic: MonotonicClock = time.monotonic,
        source: str = "",
        restart_counter: RestartCounter | None = None,
    ) -> None:
        if recording_started_at.tzinfo is None:
            raise ValueError("recording_started_atにはタイムゾーンが必要です")
        if not 0 < maximum_fps <= 2:
            raise ValueError("maximum_fpsは0より大きく2以下である必要があります")
        self.recording_started_at = recording_started_at.astimezone(timezone.utc)
        self.capture = capture
        self.analyze = analyze
        self.on_candidate = on_candidate
        self.on_status = on_status
        self.on_analysis = on_analysis
        self.maximum_fps = maximum_fps
        self.monotonic = monotonic
        self.source = source
        self.restart_counter = restart_counter
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started_monotonic: float | None = None
        self._status = VisualDetectionStatus(
            "idle",
            "自動判定は待機中です",
            0,
            0,
            0,
            source=source,
        )

    @property
    def active(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def status(self) -> VisualDetectionStatus:
        with self._lock:
            return self._status

    def start(self) -> None:
        if self.active:
            raise RuntimeError("自動判定ワーカーは既に実行中です")
        self._stop.clear()
        self._started_monotonic = self.monotonic()
        self._publish("running", "自動判定を開始しました")
        self._thread = threading.Thread(
            target=self._run,
            name="mdrl-visual-detection",
            daemon=False,
        )
        self._thread.start()

    def stop(self, timeout_seconds: float = 10.0) -> None:
        thread = self._thread
        if thread is None:
            return
        self.request_stop()
        thread.join(timeout_seconds)
        if thread.is_alive():
            raise RuntimeError("自動判定ワーカーを停止できません")
        self._thread = None
        if self.status.state != "failed":
            self._publish("stopped", "自動判定を停止しました")

    def request_stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        interval = 1 / self.maximum_fps
        while not self._stop.is_set():
            started = self.monotonic()
            if not self._process_frame():
                return
            elapsed = max(0.0, self.monotonic() - started)
            skipped = max(0, int(elapsed / interval))
            if skipped:
                self._increment(dropped=skipped)
            self._stop.wait(max(0.0, interval - elapsed))

    def _process_frame(self) -> bool:
        try:
            result = self.capture()
            if not result.succeeded:
                self._publish("degraded", result.error or "フレームを取得できません")
                return True
            assert result.sample is not None
            if self._stop.is_set():
                return True
            elapsed_ms = max(
                0,
                round(
                    (
                        result.sample.captured_at.astimezone(timezone.utc)
                        - self.recording_started_at
                    ).total_seconds()
                    * 1000
                ),
            )
            analyzed = self.analyze(result.sample, elapsed_ms)
            analysis = analyzed if isinstance(analyzed, FrameAnalysis) else None
            candidates = analysis.candidates if analysis is not None else analyzed
            if self._stop.is_set():
                return True
            self._increment(processed=1)
            if analysis is not None:
                self._apply_analysis(analysis)
                if self.on_analysis is not None:
                    self.on_analysis(analysis)
            for candidate in candidates:
                self.on_candidate(candidate)
                self._increment(candidates=1)
            self._publish("running", "自動判定を実行中です")
            return True
        except Exception as exc:
            self._publish("failed", f"自動判定を無効化しました: {exc}")
            return False

    def _increment(
        self,
        *,
        processed: int = 0,
        dropped: int = 0,
        candidates: int = 0,
    ) -> None:
        with self._lock:
            current = self._status
            elapsed = max(
                0.001,
                self.monotonic() - (self._started_monotonic or self.monotonic()),
            )
            self._status = replace(
                current,
                processed_frames=current.processed_frames + processed,
                dropped_frames=current.dropped_frames + dropped,
                candidate_count=current.candidate_count + candidates,
                effective_fps=(current.processed_frames + processed) / elapsed,
            )

    def _publish(self, state: str, message: str) -> None:
        with self._lock:
            current = self._status
            self._status = replace(current, state=state, message=message)
            status = self._status
        if self.on_status is not None:
            try:
                self.on_status(status)
            except Exception:
                pass

    def _apply_analysis(self, analysis: FrameAnalysis) -> None:
        agreement = ", ".join(
            f"{item.event_type}:{item.matched}/{item.required}"
            for item in analysis.agreements
        )
        with self._lock:
            self._status = replace(
                self._status,
                resolution=f"{analysis.source_width}x{analysis.source_height}",
                profile=analysis.profile_name,
                visual_state=analysis.state.value,
                coin_score=analysis.coin_score,
                board_score=analysis.board_score,
                turn_score=analysis.turn_score,
                turn_order_score=analysis.turn_order_score,
                result_score=analysis.result_score,
                error_score=analysis.error_score,
                replay_score=analysis.replay_score,
                overlay_score=analysis.overlay_score,
                loading_score=analysis.loading_score,
                agreement=agreement,
                restart_count=(self.restart_counter() if self.restart_counter is not None else 0),
            )
