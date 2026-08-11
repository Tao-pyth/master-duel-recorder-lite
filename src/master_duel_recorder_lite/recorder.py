from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Callable

from .capture_targets import CaptureInput, CaptureTargetError, resolve_configured_capture
from .config import AppConfig
from .duel_records import DuelRecordError, DuelRecordRepository, DuelRecordValues
from .duel_timeline import DuelTimelineRepository
from .ffmpeg import discover_ffmpeg
from .frame_capture import FfmpegWindowFrameCapture, FrameCaptureResult, FrameSample
from .game_window import WindowSnapshot
from .recording_command import RecordingCommandError, build_recording_command
from .recording_history import RecordingHistoryError, RecordingHistoryRepository
from .recording_failure import classify_recording_failure
from .recording_lock import RecordingBusyError, RecordingLock
from .recording_paths import RecordingPathError, RecordingTarget, create_recording_target
from .recording_profile import RecordingProfile, RecordingProfileError
from .recording_session import RecordingResult, RecordingSession, RecordingState
from .recording_state_store import RecordingStateStore, RecordingStateStoreError
from .runtime_paths import RuntimePaths
from .visual_detection import (
    DetectionCandidate,
    FrameAnalysis,
    TemporalEventConsensus,
    VisualDetectionPipeline,
)
from .visual_worker import VisualDetectionStatus, VisualDetectionWorker


class RecordingPreparationError(RuntimeError):
    """録画開始に必要な情報を安全に準備できないときのエラーです。"""


class RecordingTrackingError(RuntimeError):
    """録画状態を履歴へ一貫して保存できないときのエラーです。"""


VisualWorkerBuilder = Callable[[datetime], VisualDetectionWorker]
SharedFrameCapture = Callable[[], FrameCaptureResult]
AnalysisCallback = Callable[[FrameAnalysis], None]


@dataclass
class RecordingVisualLifecycle:
    confirmed: bool = False
    play_order: str = "unknown"
    outcome: str = "unknown"
    abort_reason: str | None = None
    result_detected_monotonic: float | None = None
    boundary_detected_monotonic: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def handle(self, candidate: DetectionCandidate) -> None:
        with self._lock:
            if candidate.event_type == "duel_confirmed":
                self.confirmed = True
                if candidate.play_order in {"first", "second"}:
                    self.play_order = candidate.play_order
            elif candidate.event_type == "duel_result":
                if candidate.outcome in {"win", "loss", "draw"}:
                    self.outcome = candidate.outcome
                self.result_detected_monotonic = time.monotonic()
            elif candidate.event_type == "duel_boundary":
                self.boundary_detected_monotonic = time.monotonic()
            elif candidate.event_type == "match_error":
                self.abort_reason = "マッチング失敗またはゲームサーバーエラーを検出しました"
            elif candidate.event_type == "replay_detected":
                self.abort_reason = "リプレイ画面を検出したためライブ対戦として保存しません"

    def snapshot(self) -> tuple[bool, str, str, str | None, float | None]:
        with self._lock:
            return (
                self.confirmed,
                self.play_order,
                self.outcome,
                self.abort_reason,
                self.result_detected_monotonic,
            )

    def boundary_snapshot(self) -> float | None:
        with self._lock:
            return self.boundary_detected_monotonic


@dataclass
class PreparedRecording:
    target: RecordingTarget
    executable: Path
    profile: RecordingProfile
    command: tuple[str, ...]
    session: RecordingSession
    lock: RecordingLock
    history: RecordingHistoryRepository
    state_store: RecordingStateStore
    visual_worker_builder: VisualWorkerBuilder | None = None
    visual_lifecycle: RecordingVisualLifecycle = field(default_factory=RecordingVisualLifecycle)
    _history_started: bool = field(default=False, init=False)
    _history_finalized: bool = field(default=False, init=False)
    _source: str | None = field(default=None, init=False)
    _visual_worker: VisualDetectionWorker | None = field(default=None, init=False)
    _visual_failure: str | None = field(default=None, init=False)

    def start(self, *, source: str, detection_reason: str | None = None) -> RecordingState:
        self._source = source
        try:
            add_diagnostic = getattr(self.session, "add_diagnostic", None)
            if detection_reason and callable(add_diagnostic):
                add_diagnostic(f"監視開始情報: {detection_reason}")
            self.history.register_starting(
                recording_id=self.target.recording_id,
                output_path=self.target.path,
                container=self.profile.recording_format,
                source=source,
                detection_reason=detection_reason,
                audio_input=self.profile.audio_input or None,
            )
            self._history_started = True
            self._save_state("starting")
            state = self.session.start()
            if state in {RecordingState.COMPLETED, RecordingState.FAILED}:
                self._finalize_history()
                return state
            assert self.session.started_at is not None
            self.history.mark_recording(
                self.target.recording_id,
                started_at=self.session.started_at,
            )
            self._save_state("recording")
            self._start_visual_detection()
            return state
        except (RecordingHistoryError, RecordingStateStoreError) as exc:
            if self.session.state in {
                RecordingState.STARTING,
                RecordingState.RECORDING,
                RecordingState.STOPPING,
            }:
                result = self.session.stop()
                if self._history_started:
                    try:
                        self.history.finalize(self.target.recording_id, result)
                        self._history_finalized = True
                    except RecordingHistoryError:
                        pass
            elif self._history_started and self.session.result is None:
                output = self.target.path
                classification = classify_recording_failure(
                    error=str(exc),
                    returncode=None,
                    output_exists=output.is_file(),
                    output_size=output.stat().st_size if output.is_file() else 0,
                )
                try:
                    self.history.mark_interrupted(
                        self.target.recording_id,
                        classification=classification,
                        ended_at=datetime.now(timezone.utc),
                        size_bytes=output.stat().st_size if output.is_file() else 0,
                    )
                    self._history_finalized = True
                except RecordingHistoryError:
                    pass
            raise RecordingTrackingError(f"録画履歴を開始状態へ更新できません: {exc}") from exc

    def poll(self) -> RecordingState:
        state = self.session.poll()
        if state in {RecordingState.COMPLETED, RecordingState.FAILED}:
            self._stop_visual_detection()
            self._finalize_history()
        return state

    def stop(self, *, timeout_seconds: float = 10.0) -> RecordingResult:
        self._request_visual_detection_stop()
        try:
            result = self.session.stop(timeout_seconds=timeout_seconds)
        finally:
            self._stop_visual_detection()
        self._finalize_history()
        return result

    def _finalize_history(self) -> None:
        if self._history_finalized:
            return
        if not self._history_started or self.session.result is None:
            raise RecordingTrackingError("録画結果に対応する開始履歴がありません")
        try:
            self.history.finalize(self.target.recording_id, self.session.result)
            if self.session.result.succeeded:
                records = DuelRecordRepository(self.history.database_path)
                _, play_order, outcome, _, _ = self.visual_lifecycle.snapshot()
                if play_order != "unknown" or outcome != "unknown":
                    records.save(
                        self.target.recording_id,
                        DuelRecordValues(result=outcome, play_order=play_order),
                        expected_revision=0,
                        source="detected",
                    )
                else:
                    records.create_draft(self.target.recording_id, source="system")
            self._save_state(self.session.result.state.value)
        except (DuelRecordError, RecordingHistoryError, RecordingStateStoreError) as exc:
            raise RecordingTrackingError(f"録画履歴を最終状態へ更新できません: {exc}") from exc
        self._history_finalized = True

    def _save_state(self, state: str) -> None:
        if self._source is None:
            raise RecordingStateStoreError("録画の起点が設定されていません")
        self.state_store.save(
            recording_id=self.target.recording_id,
            state=state,
            source=self._source,
            output_path=self.target.path,
            started_at=self.session.started_at,
        )

    def release(self) -> None:
        self._stop_visual_detection()
        self.lock.release()

    @property
    def visual_detection_status(self) -> VisualDetectionStatus:
        if self._visual_worker is not None:
            return self._visual_worker.status
        if self._visual_failure is not None:
            return VisualDetectionStatus("failed", self._visual_failure, 0, 0, 0)
        return VisualDetectionStatus("disabled", "この録画では自動判定を使用しません", 0, 0, 0)

    @property
    def duel_confirmed(self) -> bool:
        return self.visual_lifecycle.snapshot()[0]

    @property
    def visual_abort_reason(self) -> str | None:
        return self.visual_lifecycle.snapshot()[3]

    @property
    def result_detected_monotonic(self) -> float | None:
        return self.visual_lifecycle.snapshot()[4]

    @property
    def boundary_detected_monotonic(self) -> float | None:
        return self.visual_lifecycle.boundary_snapshot()

    def _start_visual_detection(self) -> None:
        if self.visual_worker_builder is None or self.session.started_at is None:
            return
        try:
            self._visual_worker = self.visual_worker_builder(self.session.started_at)
            self._visual_worker.start()
        except Exception as exc:
            self._visual_worker = None
            self._visual_failure = f"自動判定を開始できません: {exc}"
            self.session.add_diagnostic(self._visual_failure)

    def _stop_visual_detection(self) -> None:
        worker = self._visual_worker
        if worker is None:
            return
        try:
            worker.stop()
        except Exception as exc:
            self._visual_failure = f"自動判定を停止できません: {exc}"
            self.session.add_diagnostic(self._visual_failure)

    def _request_visual_detection_stop(self) -> None:
        worker = self._visual_worker
        if worker is not None and worker.active:
            worker.request_stop()


def prepare_recording(
    *,
    paths: RuntimePaths,
    config: AppConfig,
    capture_input: CaptureInput | None = None,
    master_duel_window_handle: int | None = None,
    master_duel_window_title: str | None = None,
    enable_visual_detection: bool = True,
    visual_frame_capture: SharedFrameCapture | None = None,
    visual_analysis_callback: AnalysisCallback | None = None,
    visual_source: str = "",
    visual_restart_counter: Callable[[], int] | None = None,
    visual_frame_generation: Callable[[], int] | None = None,
) -> PreparedRecording:
    discovery = discover_ffmpeg(config.ffmpeg_path)
    if not discovery.found or discovery.executable is None:
        raise RecordingPreparationError("FFmpegを再検出できません。doctorを再実行してください")

    try:
        profile = RecordingProfile.from_config(config)
        selected_input = capture_input or resolve_configured_capture(
            config,
            master_duel_window_handle=master_duel_window_handle,
            master_duel_window_title=master_duel_window_title,
        )
        target = create_recording_target(paths, profile)
        command = build_recording_command(
            executable=discovery.executable,
            profile=profile,
            capture_input=selected_input,
            output_path=target.path,
            recordings_root=paths.recordings,
        )
    except (
        CaptureTargetError,
        RecordingProfileError,
        RecordingPathError,
        RecordingCommandError,
        OSError,
    ) as exc:
        raise RecordingPreparationError(f"録画を準備できません: {exc}") from exc

    try:
        recording_lock = RecordingLock.acquire(paths.data / "recording.lock", recording_id=target.recording_id)
    except (RecordingBusyError, OSError, TypeError, ValueError) as exc:
        raise RecordingPreparationError(f"録画ロックを取得できません: {exc}") from exc

    try:
        history = RecordingHistoryRepository.from_runtime_paths(paths)
        visual_lifecycle = RecordingVisualLifecycle()
        visual_worker_builder = (
            _visual_worker_builder(
                config=config,
                executable=discovery.executable,
                capture_input=selected_input,
                history=history,
                recording_id=target.recording_id,
                lifecycle=visual_lifecycle,
                shared_frame_capture=visual_frame_capture,
                analysis_callback=visual_analysis_callback,
                source=visual_source,
                restart_counter=visual_restart_counter,
                frame_generation=visual_frame_generation,
            )
            if enable_visual_detection
            else None
        )
        return PreparedRecording(
            target=target,
            executable=discovery.executable,
            profile=profile,
            command=command,
            session=RecordingSession(command=command, output_path=target.path),
            lock=recording_lock,
            history=history,
            state_store=RecordingStateStore(paths),
            visual_worker_builder=visual_worker_builder,
            visual_lifecycle=visual_lifecycle,
        )
    except (OSError, RecordingHistoryError) as exc:
        recording_lock.release()
        raise RecordingPreparationError(f"録画履歴を準備できません: {exc}") from exc


def _visual_worker_builder(
    *,
    config: AppConfig,
    executable: Path,
    capture_input: CaptureInput,
    history: RecordingHistoryRepository,
    recording_id: str,
    lifecycle: RecordingVisualLifecycle,
    shared_frame_capture: SharedFrameCapture | None = None,
    analysis_callback: AnalysisCallback | None = None,
    source: str = "",
    restart_counter: Callable[[], int] | None = None,
    frame_generation: Callable[[], int] | None = None,
) -> VisualWorkerBuilder | None:
    if not config.visual_detection_enabled or config.capture_mode != "master_duel":
        return None
    if capture_input.window_handle is None or capture_input.window_title is None:
        return None
    window_handle = capture_input.window_handle
    if window_handle <= 0:
        return None
    window = WindowSnapshot(
        handle=window_handle,
        pid=0,
        title=capture_input.window_title,
        visible=True,
        minimized=False,
        width=0,
        height=0,
    )
    capture = FfmpegWindowFrameCapture(executable) if shared_frame_capture is None else None
    repository = DuelTimelineRepository(history.database_path)

    def build(started_at: datetime) -> VisualDetectionWorker:
        def new_pipeline() -> VisualDetectionPipeline:
            return VisualDetectionPipeline(
                consensus=TemporalEventConsensus(
                    minimum_confidence=config.visual_detection_minimum_confidence,
                    assume_started=True,
                )
            )

        pipeline = new_pipeline()
        generation = frame_generation() if frame_generation is not None else 0

        def analyze(frame: FrameSample, elapsed_ms: int) -> FrameAnalysis:
            nonlocal pipeline, generation
            current_generation = frame_generation() if frame_generation is not None else generation
            if current_generation != generation:
                generation = current_generation
                pipeline = new_pipeline()
            return pipeline.analyze_frame(frame, elapsed_ms)

        def save_candidate(candidate: DetectionCandidate) -> None:
            lifecycle.handle(candidate)
            if candidate.event_type not in {"duel_start", "turn_change", "duel_result"}:
                return
            repository.add(
                recording_id,
                elapsed_ms=candidate.elapsed_ms,
                event_type=candidate.event_type,
                actor=candidate.actor,
                outcome=candidate.outcome,
                label=candidate.reason,
                source="detected",
                confidence=candidate.confidence,
                status="candidate",
                detector_id=candidate.detector_id,
                detector_version=candidate.detector_version,
            )

        return VisualDetectionWorker(
            recording_started_at=started_at,
            capture=(
                shared_frame_capture
                if shared_frame_capture is not None
                else lambda: capture.capture(window)  # type: ignore[union-attr]
            ),
            analyze=analyze,
            on_candidate=save_candidate,
            on_analysis=analysis_callback,
            maximum_fps=config.visual_detection_maximum_fps,
            source=source,
            restart_counter=restart_counter,
        )

    return build
