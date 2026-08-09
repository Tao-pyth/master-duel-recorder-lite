from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import threading

from .auto_recording import AutoRecordingController, AutoRecordingEvent, AutoRecordingEventAction
from .capture_targets import CaptureTarget, CaptureTargetCatalog, capture_input_for_target
from .config import AppConfig, LoadedAppConfig, load_app_config, save_app_config, validate_app_config
from .config_management import updated_config
from .detection import DetectionPolicy, DuelDetectionStateMachine
from .ffmpeg import discover_ffmpeg
from .game_window import GameWindowMonitor
from .master_duel_detector import MasterDuelWindowDetector
from .media_recovery import MediaInspection, MediaRecoveryService, MediaRepairResult
from .preflight import PreflightReport, run_preflight
from .recorder import PreparedRecording, prepare_recording
from .recording_history import (
    ConsistencyIssue,
    HistoryQuery,
    RecordingHistoryEntry,
    RecordingHistoryRepository,
)
from .recording_browsing import RecordingBrowser, RecordingReference
from .recording_session import RecordingResult, RecordingState
from .recovery import InterruptedDetection, RecoveryManager
from .runtime_paths import default_runtime_paths
from .upload_export import UploadExporter
from .upload_manifest import UploadManifestWriter
from .upload_media import UploadMediaValidator, find_ffprobe
from .upload_metadata import UploadMetadata, UploadPrivacy
from .upload_preparation import UploadPreparationResult, UploadPreparationService
from .upload_queue import UploadQueueItem, UploadQueueStore


class ApplicationOperationError(RuntimeError):
    """GUIとCLIが利用する操作を完了できない場合のエラーです。"""


@dataclass(frozen=True)
class RecordingSnapshot:
    active: bool
    state: RecordingState
    recording_id: str | None
    output_path: Path | None
    started_at: datetime | None
    elapsed_seconds: float
    result: RecordingResult | None = None


@dataclass(frozen=True)
class ApplicationEvent:
    kind: str
    message: str
    recording_id: str | None = None
    state: str = ""


EventCallback = Callable[[ApplicationEvent], None]


class RecorderApplicationService:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        user_data_dir: Path | None = None,
        target_catalog: CaptureTargetCatalog | None = None,
        recording_browser: RecordingBrowser | None = None,
    ) -> None:
        self.project_root = project_root
        self.user_data_dir = user_data_dir
        self.paths = default_runtime_paths(project_root=project_root, user_data_dir=user_data_dir)
        self._target_catalog = target_catalog
        self._recording_browser = recording_browser
        self._lock = threading.RLock()
        self._current: PreparedRecording | None = None
        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()

    def load_config(self) -> LoadedAppConfig:
        return load_app_config(project_root=self.project_root, user_data_dir=self.user_data_dir)

    def save_settings(self, values: Mapping[str, str]) -> AppConfig:
        loaded = self.load_config()
        config = loaded.config
        for key, raw_value in values.items():
            config = updated_config(config, key, raw_value)
        save_app_config(paths=self.paths, config=config)
        return config

    def list_capture_targets(self) -> tuple[CaptureTarget, ...]:
        config = self.load_config().config
        monitor = GameWindowMonitor(
            process_name=config.game_process_name,
            title_contains=config.game_window_title_contains,
        )
        catalog = self._target_catalog or CaptureTargetCatalog()
        return catalog.list_targets(master_duel_monitor=monitor)

    def select_capture_target(self, target: CaptureTarget) -> AppConfig:
        if not target.available:
            raise ApplicationOperationError(f"録画対象を利用できません: {target.label}")
        loaded = self.load_config()
        target_id = "" if target.mode.value in {"desktop", "master_duel"} else target.identifier
        config = replace(
            loaded.config,
            capture_mode=target.mode.value,
            capture_target_id=target_id,
        )
        validate_app_config(config)
        save_app_config(paths=self.paths, config=config)
        return config

    def diagnose(self) -> PreflightReport:
        loaded = self.load_config()
        return run_preflight(paths=self.paths, config=loaded.config, config_loaded=loaded.config_loaded)

    def start_recording(self, target: CaptureTarget | None = None) -> RecordingSnapshot:
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._current is not None:
                raise ApplicationOperationError("録画はすでに実行中です")
            if self.watch_active:
                raise ApplicationOperationError("自動監視中は手動録画を開始できません")
            loaded = self.load_config()
            report = run_preflight(
                paths=self.paths,
                config=loaded.config,
                config_loaded=loaded.config_loaded,
            )
            if not report.succeeded:
                failures = [check.message for check in report.checks if check.status.value == "error"]
                raise ApplicationOperationError(" / ".join(failures) or "録画環境を利用できません")
            capture_input = capture_input_for_target(target) if target is not None else None
            try:
                prepared = prepare_recording(
                    paths=self.paths,
                    config=loaded.config,
                    capture_input=capture_input,
                )
                state = prepared.start(source="gui", detection_reason="GUIによる手動録画")
            except Exception:
                if "prepared" in locals():
                    prepared.release()
                raise
            if state is RecordingState.FAILED:
                result = prepared.session.result
                prepared.release()
                raise ApplicationOperationError(result.error if result and result.error else "録画を開始できません")
            self._current = prepared
            return self._manual_snapshot_locked()

    def recording_snapshot(self) -> RecordingSnapshot:
        with self._lock:
            self._collect_manual_terminal_locked()
            return self._manual_snapshot_locked()

    def stop_recording(self) -> RecordingSnapshot:
        with self._lock:
            if self._current is None:
                raise ApplicationOperationError("実行中の手動録画はありません")
            prepared = self._current
            try:
                result = prepared.stop()
            finally:
                prepared.release()
                self._current = None
            return RecordingSnapshot(
                False,
                result.state,
                prepared.target.recording_id,
                result.output_path,
                result.started_at,
                _elapsed(result.started_at, result.ended_at),
                result,
            )

    @property
    def watch_active(self) -> bool:
        return self._watch_thread is not None and self._watch_thread.is_alive()

    def start_watch(self, callback: EventCallback | None = None) -> None:
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._current is not None:
                raise ApplicationOperationError("手動録画中は自動監視を開始できません")
            if self.watch_active:
                raise ApplicationOperationError("自動監視はすでに実行中です")
            self._watch_stop.clear()
            thread = threading.Thread(
                target=self._watch_loop,
                args=(callback,),
                name="mdrl-watch",
                daemon=False,
            )
            self._watch_thread = thread
            thread.start()

    def stop_watch(self, timeout_seconds: float = 15.0) -> None:
        with self._lock:
            thread = self._watch_thread
            if thread is None:
                return
            self._watch_stop.set()
        thread.join(timeout_seconds)
        if thread.is_alive():
            raise ApplicationOperationError("自動監視を正常停止できません")
        with self._lock:
            self._watch_thread = None

    def list_history(self, *, limit: int = 200) -> tuple[RecordingHistoryEntry, ...]:
        return RecordingHistoryRepository.from_runtime_paths(self.paths).query(HistoryQuery(limit=limit))

    def check_history(self) -> tuple[ConsistencyIssue, ...]:
        return RecordingHistoryRepository.from_runtime_paths(self.paths).check_consistency()

    def resolve_recording(self, recording_id: str) -> RecordingReference:
        return self._browser().resolve(recording_id)

    def play_recording(self, recording_id: str) -> RecordingReference:
        return self._browser().play(recording_id)

    def reveal_recording(self, recording_id: str) -> RecordingReference:
        return self._browser().reveal(recording_id)

    def detect_recovery(self) -> tuple[InterruptedDetection, ...]:
        return RecoveryManager(paths=self.paths).detect_interrupted()

    def list_recovery(self) -> tuple[RecordingHistoryEntry, ...]:
        return RecordingHistoryRepository.from_runtime_paths(self.paths).recovery_entries()

    def inspect_recovery(self, recording_id: str) -> MediaInspection:
        return self._media_recovery_service().inspect(recording_id)

    def repair_recovery(self, recording_id: str, *, dry_run: bool = True) -> MediaRepairResult:
        return self._media_recovery_service().repair(recording_id, dry_run=dry_run)

    def list_preparations(self) -> tuple[UploadQueueItem, ...]:
        return UploadQueueStore(self.paths).list()

    def enqueue_preparation(
        self,
        recording_id: str,
        *,
        title: str,
        description: str = "",
        tags: tuple[str, ...] = (),
        privacy: str | None = None,
    ) -> UploadQueueItem:
        config = self.load_config().config
        metadata = UploadMetadata(
            title=title,
            description=description,
            tags=tags,
            privacy=UploadPrivacy(privacy or config.upload_privacy_status),
        )
        return self._upload_preparation_service().enqueue(
            recording_id=recording_id,
            metadata=metadata,
        )

    def process_preparations(self, queue_id: str | None = None) -> tuple[UploadPreparationResult, ...]:
        return self._upload_preparation_service().process(queue_id)

    def close(self) -> None:
        if self.watch_active:
            self.stop_watch()
        with self._lock:
            if self._current is not None:
                prepared = self._current
                try:
                    prepared.stop()
                finally:
                    prepared.release()
                    self._current = None

    def _watch_loop(self, callback: EventCallback | None) -> None:
        controller: AutoRecordingController | None = None
        try:
            loaded = self.load_config()
            config = loaded.config
            report = run_preflight(paths=self.paths, config=config, config_loaded=loaded.config_loaded)
            if not report.succeeded:
                raise ApplicationOperationError("自動監視の開始前診断に失敗しました")
            monitor = GameWindowMonitor(
                process_name=config.game_process_name,
                title_contains=config.game_window_title_contains,
            )
            detector = MasterDuelWindowDetector(monitor)
            controller = AutoRecordingController(
                state_machine=DuelDetectionStateMachine(
                    DetectionPolicy(
                        start_confirmations=config.start_confirmations,
                        stop_confirmations=config.stop_confirmations,
                        minimum_confidence=config.detection_minimum_confidence,
                        cooldown_seconds=config.detection_cooldown_seconds,
                        automatic_start=config.auto_start_recording,
                        automatic_stop=config.auto_stop_recording,
                    )
                ),
                recording_factory=lambda observation: prepare_recording(
                    paths=self.paths,
                    config=config,
                    master_duel_window_handle=observation.capture_window_handle,
                ),
            )
            self._emit(callback, ApplicationEvent("watch", "自動監視を開始しました", state="watching"))
            while not self._watch_stop.is_set():
                event = controller.process(detector.observe())
                if event.action is not AutoRecordingEventAction.NONE:
                    self._emit(callback, _application_event(event))
                self._watch_stop.wait(config.detection_poll_interval_seconds)
        except Exception as exc:
            self._emit(callback, ApplicationEvent("error", str(exc), state="failed"))
        finally:
            if controller is not None and controller.current is not None:
                event = controller.manual_stop()
                self._emit(callback, _application_event(event))
            self._emit(callback, ApplicationEvent("watch", "自動監視を停止しました", state="stopped"))

    def _collect_manual_terminal_locked(self) -> None:
        if self._current is None:
            return
        state = self._current.poll()
        if state not in {RecordingState.COMPLETED, RecordingState.FAILED}:
            return
        self._current.release()
        self._current = None

    def _manual_snapshot_locked(self) -> RecordingSnapshot:
        if self._current is None:
            return RecordingSnapshot(False, RecordingState.COMPLETED, None, None, None, 0.0)
        session = self._current.session
        return RecordingSnapshot(
            session.state is RecordingState.RECORDING,
            session.state,
            self._current.target.recording_id,
            self._current.target.path,
            session.started_at,
            _elapsed(session.started_at),
            session.result,
        )

    def _media_recovery_service(self) -> MediaRecoveryService:
        discovery = discover_ffmpeg(self.load_config().config.ffmpeg_path)
        if not discovery.found or discovery.executable is None:
            raise ApplicationOperationError("FFmpegが見つかりません")
        return MediaRecoveryService(
            repository=RecordingHistoryRepository.from_runtime_paths(self.paths),
            ffmpeg_executable=discovery.executable,
        )

    def _browser(self) -> RecordingBrowser:
        if self._recording_browser is None:
            self._recording_browser = RecordingBrowser(
                repository=RecordingHistoryRepository.from_runtime_paths(self.paths),
                recordings_root=self.paths.recordings,
            )
        return self._recording_browser

    def _upload_preparation_service(self) -> UploadPreparationService:
        discovery = discover_ffmpeg(self.load_config().config.ffmpeg_path)
        if not discovery.found or discovery.executable is None:
            raise ApplicationOperationError("FFmpegが見つかりません")
        repository = RecordingHistoryRepository.from_runtime_paths(self.paths)
        queue = UploadQueueStore(self.paths)
        validator = UploadMediaValidator(ffprobe_executable=find_ffprobe(discovery.executable))
        return UploadPreparationService(
            paths=self.paths,
            repository=repository,
            queue=queue,
            exporter=UploadExporter(
                paths=self.paths,
                ffmpeg_executable=discovery.executable,
                validator=validator,
            ),
            manifest_writer=UploadManifestWriter(self.paths),
        )

    @staticmethod
    def _emit(callback: EventCallback | None, event: ApplicationEvent) -> None:
        if callback is not None:
            callback(event)


def _application_event(event: AutoRecordingEvent) -> ApplicationEvent:
    return ApplicationEvent(
        event.action.value,
        event.message,
        recording_id=event.recording_id,
        state=event.result.state.value if event.result is not None else event.action.value,
    )


def _elapsed(started_at: datetime | None, ended_at: datetime | None = None) -> float:
    if started_at is None:
        return 0.0
    end = ended_at or datetime.now(timezone.utc)
    return max(0.0, (end - started_at).total_seconds())
