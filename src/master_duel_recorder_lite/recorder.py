from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .capture_targets import CaptureInput, CaptureTargetError, resolve_configured_capture
from .config import AppConfig
from .ffmpeg import discover_ffmpeg
from .recording_command import RecordingCommandError, build_recording_command
from .recording_history import RecordingHistoryError, RecordingHistoryRepository
from .recording_failure import classify_recording_failure
from .recording_lock import RecordingBusyError, RecordingLock
from .recording_paths import RecordingPathError, RecordingTarget, create_recording_target
from .recording_profile import RecordingProfile, RecordingProfileError
from .recording_session import RecordingResult, RecordingSession, RecordingState
from .recording_state_store import RecordingStateStore, RecordingStateStoreError
from .runtime_paths import RuntimePaths


class RecordingPreparationError(RuntimeError):
    """録画開始に必要な情報を安全に準備できないときのエラーです。"""


class RecordingTrackingError(RuntimeError):
    """録画状態を履歴へ一貫して保存できないときのエラーです。"""


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
    _history_started: bool = field(default=False, init=False)
    _history_finalized: bool = field(default=False, init=False)
    _source: str | None = field(default=None, init=False)

    def start(self, *, source: str, detection_reason: str | None = None) -> RecordingState:
        self._source = source
        try:
            self.history.register_starting(
                recording_id=self.target.recording_id,
                output_path=self.target.path,
                container=self.profile.recording_format,
                source=source,
                detection_reason=detection_reason,
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
            self._finalize_history()
        return state

    def stop(self, *, timeout_seconds: float = 10.0) -> RecordingResult:
        result = self.session.stop(timeout_seconds=timeout_seconds)
        self._finalize_history()
        return result

    def _finalize_history(self) -> None:
        if self._history_finalized:
            return
        if not self._history_started or self.session.result is None:
            raise RecordingTrackingError("録画結果に対応する開始履歴がありません")
        try:
            self.history.finalize(self.target.recording_id, self.session.result)
            self._save_state(self.session.result.state.value)
        except (RecordingHistoryError, RecordingStateStoreError) as exc:
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
        self.lock.release()


def prepare_recording(
    *,
    paths: RuntimePaths,
    config: AppConfig,
    capture_input: CaptureInput | None = None,
    master_duel_window_handle: int | None = None,
) -> PreparedRecording:
    discovery = discover_ffmpeg(config.ffmpeg_path)
    if not discovery.found or discovery.executable is None:
        raise RecordingPreparationError("FFmpegを再検出できません。doctorを再実行してください")

    try:
        profile = RecordingProfile.from_config(config)
        selected_input = capture_input or resolve_configured_capture(
            config,
            master_duel_window_handle=master_duel_window_handle,
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
        return PreparedRecording(
            target=target,
            executable=discovery.executable,
            profile=profile,
            command=command,
            session=RecordingSession(command=command, output_path=target.path),
            lock=recording_lock,
            history=history,
            state_store=RecordingStateStore(paths),
        )
    except (OSError, RecordingHistoryError) as exc:
        recording_lock.release()
        raise RecordingPreparationError(f"録画履歴を準備できません: {exc}") from exc
