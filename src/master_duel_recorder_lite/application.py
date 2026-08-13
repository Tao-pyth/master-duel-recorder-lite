from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import threading
import time

from .auto_recording import (
    AutoRecordingController,
    AutoRecordingEvent,
    AutoRecordingEventAction,
)
from .capture_targets import (
    CaptureInput,
    CaptureMode,
    CaptureTarget,
    CaptureTargetCatalog,
    capture_input_for_target,
    capture_input_for_window_region,
    resolve_configured_capture,
)
from .config import (
    AppConfig,
    LoadedAppConfig,
    load_app_config,
    save_app_config,
    validate_app_config,
)
from .config_management import updated_config
from .data_management import ManagedDataResult, ManagedDataService
from .detection import DetectionPolicy, DuelDetectionStateMachine, DuelObservation
from .duel_catalog import DuelCatalogEntry, DuelCatalogRepository
from .duel_start_monitor import MasterDuelStartMonitor
from .duel_records import (
    DuelRecord,
    DuelRecordChange,
    DuelRecordRepository,
    DuelRecordValues,
)
from .duel_statistics import (
    DuelStatisticsRepository,
    StatisticsDashboard,
    StatisticsFilter,
)
from .duel_timeline import DuelEvent, DuelTimelineRepository
from .ffmpeg import (
    AudioInputTestResult,
    InputEnumerationResult,
    discover_ffmpeg,
    enumerate_windows_inputs,
    test_windows_audio_input,
)
from .ffmpeg_setup import (
    FfmpegInstallProgress,
    FfmpegInstallResult,
    FfmpegInstaller,
    default_ffmpeg_install_directory,
)
from .frame_capture import FrameCaptureResult, PersistentFfmpegRegionFrameCapture
from .game_window import GameWindowMonitor, GameWindowStatus, WindowSnapshot
from .master_duel_detector import MasterDuelWindowDetector
from .preflight import PreflightReport, run_preflight
from .recorder import PreparedRecording, prepare_recording
from .recording_history import (
    ConsistencyIssue,
    HistoryDeletionResult,
    HistoryQuery,
    RecordingHistoryEntry,
    RecordingHistoryRepository,
)
from .recording_browsing import RecordingBrowser, RecordingReference
from .recording_session import RecordingResult, RecordingState
from .seasons import Season, SeasonRepository
from .runtime_paths import default_runtime_paths
from .upload_export import UploadExporter
from .upload_manifest import UploadManifestWriter
from .upload_media import UploadMediaValidator, find_ffprobe
from .upload_metadata import UploadMetadata, UploadPrivacy
from .upload_preparation import UploadPreparationResult, UploadPreparationService
from .upload_queue import UploadQueueItem, UploadQueueStore
from .visual_detection import DetectionCandidate, FrameAnalysis
from .visual_diagnostics import VisualDiagnosticSession
from .visual_worker import VisualDetectionStatus


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


@dataclass(frozen=True)
class DuelEditorData:
    record: DuelRecord | None
    values: DuelRecordValues
    decks: tuple[DuelCatalogEntry, ...]
    tags: tuple[DuelCatalogEntry, ...]
    seasons: tuple[Season, ...]


@dataclass(frozen=True)
class RecordingHistoryView:
    entry: RecordingHistoryEntry
    duel_record: DuelRecord | None
    own_deck_color: str | None = None

    @property
    def recording_id(self) -> str:
        return self.entry.recording_id

    @property
    def result(self) -> str:
        return (
            self.duel_record.values.result
            if self.duel_record is not None
            else "unknown"
        )

    @property
    def play_order(self) -> str:
        return (
            self.duel_record.values.play_order
            if self.duel_record is not None
            else "unknown"
        )

    @property
    def coin_face(self) -> str:
        return (
            self.duel_record.values.coin_face
            if self.duel_record is not None
            else "unknown"
        )

    @property
    def coin_toss_outcome(self) -> str:
        return (
            self.duel_record.values.coin_toss_outcome
            if self.duel_record is not None
            else "unknown"
        )

    @property
    def duel_type(self) -> str:
        return (
            self.duel_record.values.duel_type
            if self.duel_record is not None
            else "other"
        )

    @property
    def own_deck(self) -> str:
        return self.duel_record.values.own_deck if self.duel_record is not None else ""


@dataclass(frozen=True)
class RecordingHistoryDashboard:
    views: tuple[RecordingHistoryView, ...]
    incomplete_duel_record_count: int


EventCallback = Callable[[ApplicationEvent], None]


class RecorderApplicationService:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        user_data_dir: Path | None = None,
        target_catalog: CaptureTargetCatalog | None = None,
        recording_browser: RecordingBrowser | None = None,
        ffmpeg_installer: FfmpegInstaller | None = None,
    ) -> None:
        self.project_root = project_root
        self.user_data_dir = user_data_dir
        self.paths = default_runtime_paths(
            project_root=project_root, user_data_dir=user_data_dir
        )
        self._target_catalog = target_catalog
        self._recording_browser = recording_browser
        self._ffmpeg_installer = ffmpeg_installer or FfmpegInstaller()
        self._lock = threading.RLock()
        self._current: PreparedRecording | None = None
        self._manual_starting = False
        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()
        self._visual_status = VisualDetectionStatus(
            "disabled", "録画開始後に自動判定状態を表示します", 0, 0, 0
        )

    def load_config(self) -> LoadedAppConfig:
        return load_app_config(
            project_root=self.project_root, user_data_dir=self.user_data_dir
        )

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
        target_id = (
            "" if target.mode.value in {"desktop", "master_duel"} else target.identifier
        )
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
        return run_preflight(
            paths=self.paths, config=loaded.config, config_loaded=loaded.config_loaded
        )

    def runtime_data_directory(self) -> Path:
        return self.paths.root

    def default_ffmpeg_install_directory(self) -> Path:
        return default_ffmpeg_install_directory()

    def install_ffmpeg(
        self,
        destination: Path,
        *,
        progress: Callable[[FfmpegInstallProgress], None] | None = None,
    ) -> FfmpegInstallResult:
        result = self._ffmpeg_installer.install(destination, progress=progress)
        self.save_settings({"recorder.ffmpeg_path": str(result.executable)})
        return result

    def list_audio_inputs(self) -> InputEnumerationResult:
        config = self.load_config().config
        discovery = discover_ffmpeg(config.ffmpeg_path)
        if not discovery.found or discovery.executable is None:
            return InputEnumerationResult(inputs=(), errors=("FFmpegが見つかりません",))
        result = enumerate_windows_inputs(discovery.executable)
        audio_inputs = tuple(item for item in result.inputs if item.kind == "audio")
        return InputEnumerationResult(audio_inputs, result.warnings, result.errors)

    def test_audio_input(self, identifier: str) -> AudioInputTestResult:
        normalized = identifier.strip()
        if not normalized:
            return AudioInputTestResult("disabled", "音声入力は無効です")
        config = self.load_config().config
        discovery = discover_ffmpeg(config.ffmpeg_path)
        if not discovery.found or discovery.executable is None:
            return AudioInputTestResult("unavailable", "FFmpegが見つかりません")
        enumeration = enumerate_windows_inputs(discovery.executable)
        device = next(
            (
                item
                for item in enumeration.inputs
                if item.kind == "audio"
                and normalized in {item.identifier, item.display_name}
            ),
            None,
        )
        if device is None:
            return AudioInputTestResult(
                "unavailable", "選択した音声入力が見つかりません"
            )
        return test_windows_audio_input(discovery.executable, device)

    def start_recording(self, target: CaptureTarget | None = None) -> RecordingSnapshot:
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._manual_starting:
                raise ApplicationOperationError("録画の開始処理はすでに実行中です")
            if self._current is not None:
                raise ApplicationOperationError("録画はすでに実行中です")
            if self.watch_active:
                raise ApplicationOperationError("自動監視中は手動録画を開始できません")
            self._manual_starting = True

        prepared: PreparedRecording | None = None
        try:
            loaded = self.load_config()
            report = run_preflight(
                paths=self.paths,
                config=loaded.config,
                config_loaded=loaded.config_loaded,
            )
            if not report.succeeded:
                failures = [
                    check.message
                    for check in report.checks
                    if check.status.value == "error"
                ]
                raise ApplicationOperationError(
                    " / ".join(failures) or "録画環境を利用できません"
                )
            capture_input = None
            if target is not None:
                capture_input = (
                    resolve_configured_capture(
                        replace(
                            loaded.config,
                            capture_mode=CaptureMode.MASTER_DUEL.value,
                            capture_target_id="",
                        )
                    )
                    if target.mode is CaptureMode.MASTER_DUEL
                    else capture_input_for_target(target)
                )
            prepared = prepare_recording(
                paths=self.paths,
                config=loaded.config,
                capture_input=capture_input,
                enable_visual_detection=False,
            )
            state = prepared.start(source="gui", detection_reason="GUIによる手動録画")
            if state is RecordingState.FAILED:
                result = prepared.session.result
                raise ApplicationOperationError(
                    result.error if result and result.error else "録画を開始できません"
                )
            with self._lock:
                self._current = prepared
                self._visual_status = prepared.visual_detection_status
                self._manual_starting = False
                return self._manual_snapshot_locked()
        except BaseException:
            try:
                if prepared is not None:
                    prepared.release()
            finally:
                with self._lock:
                    self._manual_starting = False
            raise

    def recording_snapshot(self) -> RecordingSnapshot:
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._current is not None:
                self._visual_status = self._current.visual_detection_status
            return self._manual_snapshot_locked()

    def visual_detection_status(self) -> VisualDetectionStatus:
        with self._lock:
            if self._current is not None:
                self._visual_status = self._current.visual_detection_status
            return self._visual_status

    def stop_recording(self) -> RecordingSnapshot:
        with self._lock:
            if self._manual_starting:
                raise ApplicationOperationError("録画の開始処理中は停止できません")
            if self._current is None:
                raise ApplicationOperationError("実行中の手動録画はありません")
            prepared = self._current
            try:
                result = prepared.stop()
            finally:
                self._visual_status = prepared.visual_detection_status
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
            if self._manual_starting:
                raise ApplicationOperationError(
                    "手動録画の開始処理中は自動監視を開始できません"
                )
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

    def list_history(
        self, *, limit: int = 200, query: HistoryQuery | None = None
    ) -> tuple[RecordingHistoryEntry, ...]:
        selected = query or HistoryQuery(limit=limit)
        return RecordingHistoryRepository.from_runtime_paths(self.paths).query(selected)

    def list_history_views(
        self, *, limit: int = 200, query: HistoryQuery | None = None
    ) -> tuple[RecordingHistoryView, ...]:
        entries = self.list_history(limit=limit, query=query)
        records = {
            item.recording_id: item
            for item in DuelRecordRepository.from_runtime_paths(self.paths).list(
                limit=1000
            )
        }
        deck_colors = {
            item.name.casefold(): item.color
            for item in DuelCatalogRepository.from_runtime_paths(self.paths).list_decks(
                include_archived=True
            )
        }
        return tuple(
            RecordingHistoryView(
                entry,
                records.get(entry.recording_id),
                deck_colors.get(records[entry.recording_id].values.own_deck.casefold())
                if entry.recording_id in records
                else None,
            )
            for entry in entries
        )

    def export_managed_data(self, path: Path) -> ManagedDataResult:
        self._require_data_management_idle()
        return ManagedDataService.from_runtime_paths(self.paths).export_to(path)

    def import_managed_data(self, path: Path) -> ManagedDataResult:
        self._require_data_management_idle()
        return ManagedDataService.from_runtime_paths(self.paths).import_from(path)

    def reset_managed_data(self, scope: str) -> ManagedDataResult:
        self._require_data_management_idle()
        return ManagedDataService.from_runtime_paths(self.paths).reset(scope)

    def _require_data_management_idle(self) -> None:
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._manual_starting or self._current is not None or self.watch_active:
                raise ApplicationOperationError(
                    "録画または自動監視の実行中は管理データを変更できません"
                )

    def get_history_dashboard(
        self, *, limit: int = 200, query: HistoryQuery | None = None
    ) -> RecordingHistoryDashboard:
        return RecordingHistoryDashboard(
            views=self.list_history_views(limit=limit, query=query),
            incomplete_duel_record_count=DuelRecordRepository.from_runtime_paths(
                self.paths
            ).count_incomplete_recordings(),
        )

    def get_history(self, recording_id: str) -> RecordingHistoryEntry:
        entry = RecordingHistoryRepository.from_runtime_paths(self.paths).get(
            recording_id
        )
        if entry is None:
            raise ApplicationOperationError(f"録画履歴が見つかりません: {recording_id}")
        return entry

    def get_duel_record(self, recording_id: str) -> DuelRecord | None:
        return DuelRecordRepository.from_runtime_paths(self.paths).get(recording_id)

    def get_duel_editor_data(self, recording_id: str) -> DuelEditorData:
        record = self.get_duel_record(recording_id)
        catalog = DuelCatalogRepository.from_runtime_paths(self.paths)
        values = (
            record.values
            if record is not None
            else catalog.preferences().to_record_values()
        )
        return DuelEditorData(
            record=record,
            values=values,
            decks=catalog.list(kind="deck"),
            tags=catalog.list(kind="tag"),
            seasons=SeasonRepository.from_runtime_paths(self.paths).list(
                include_archived=True
            ),
        )

    def save_duel_record(
        self,
        recording_id: str,
        values: DuelRecordValues,
        *,
        expected_revision: int,
    ) -> DuelRecord:
        saved = DuelRecordRepository.from_runtime_paths(self.paths).save(
            recording_id,
            values,
            expected_revision=expected_revision,
            source="user",
        )
        DuelCatalogRepository.from_runtime_paths(self.paths).remember_record_values(
            saved.values
        )
        return saved

    def create_manual_duel_record(
        self,
        values: DuelRecordValues,
        *,
        occurred_at: datetime,
    ) -> DuelRecord:
        saved = DuelRecordRepository.from_runtime_paths(self.paths).create_manual(
            values,
            occurred_at=occurred_at,
            source="user",
        )
        DuelCatalogRepository.from_runtime_paths(self.paths).remember_record_values(
            saved.values
        )
        return saved

    def update_duel_record(
        self,
        duel_id: str,
        values: DuelRecordValues,
        *,
        expected_revision: int,
        occurred_at: datetime | None = None,
    ) -> DuelRecord:
        saved = DuelRecordRepository.from_runtime_paths(self.paths).update(
            duel_id,
            values,
            expected_revision=expected_revision,
            occurred_at=occurred_at,
            source="user",
        )
        DuelCatalogRepository.from_runtime_paths(self.paths).remember_record_values(
            saved.values
        )
        return saved

    def list_duel_catalog(self) -> tuple[DuelCatalogEntry, ...]:
        return DuelCatalogRepository.from_runtime_paths(self.paths).list()

    def list_decks(self) -> tuple[DuelCatalogEntry, ...]:
        return DuelCatalogRepository.from_runtime_paths(self.paths).list_decks()

    def list_tags(self) -> tuple[DuelCatalogEntry, ...]:
        return DuelCatalogRepository.from_runtime_paths(self.paths).list_tags()

    def list_seasons(self, *, include_archived: bool = False) -> tuple[Season, ...]:
        return SeasonRepository.from_runtime_paths(self.paths).list(
            include_archived=include_archived
        )

    def add_season(self, **values: object) -> Season:
        return SeasonRepository.from_runtime_paths(self.paths).add(**values)

    def update_season(self, season_id: int, **values: object) -> Season:
        return SeasonRepository.from_runtime_paths(self.paths).update(
            season_id, **values
        )

    def delete_season(self, season_id: int) -> Season:
        return SeasonRepository.from_runtime_paths(self.paths).delete(season_id)

    def get_statistics_dashboard(
        self,
        filters: StatisticsFilter | None = None,
        *,
        granularity: str = "month",
    ) -> StatisticsDashboard:
        repository = DuelStatisticsRepository.from_runtime_paths(self.paths)
        selected = filters or StatisticsFilter()
        return repository.dashboard(selected, granularity=granularity)

    def add_duel_catalog_entry(
        self,
        kind: str,
        name: str,
        *,
        description: str = "",
        color: str | None = None,
    ) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).add(
            kind,
            name,
            description=description,
            color=color,
        )

    def add_deck(
        self,
        name: str,
        *,
        description: str = "",
        color: str = "#2F6B5F",
    ) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).add_deck(
            name,
            description=description,
            color=color,
        )

    def add_tag(
        self,
        name: str,
        *,
        description: str = "",
        color: str = "#4F6F8F",
    ) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).add_tag(
            name,
            description=description,
            color=color,
        )

    def rename_duel_catalog_entry(self, entry_id: int, name: str) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).rename(
            entry_id, name
        )

    def update_deck(
        self,
        entry_id: int,
        *,
        name: str,
        description: str = "",
        color: str = "#2F6B5F",
        opponent_only: bool = False,
        hidden_from_history_statistics: bool = False,
    ) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).update_deck(
            entry_id,
            name=name,
            description=description,
            color=color,
            opponent_only=opponent_only,
            hidden_from_history_statistics=hidden_from_history_statistics,
        )

    def update_tag(
        self,
        entry_id: int,
        *,
        name: str,
        description: str = "",
        color: str = "#4F6F8F",
    ) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).update_tag(
            entry_id,
            name=name,
            description=description,
            color=color,
        )

    def delete_duel_catalog_entry(self, entry_id: int) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).delete(entry_id)

    def duel_record_changes(self, recording_id: str) -> tuple[DuelRecordChange, ...]:
        return DuelRecordRepository.from_runtime_paths(self.paths).changes(recording_id)

    def list_timeline(
        self,
        recording_id: str,
        *,
        status: str | None = None,
        event_type: str | None = None,
    ) -> tuple[DuelEvent, ...]:
        return DuelTimelineRepository.from_runtime_paths(self.paths).list(
            recording_id,
            status=status,
            event_type=event_type,
        )

    def add_timeline_event(
        self,
        recording_id: str,
        *,
        elapsed_ms: int,
        event_type: str,
        actor: str | None = None,
        outcome: str | None = None,
        label: str = "",
    ) -> DuelEvent:
        return DuelTimelineRepository.from_runtime_paths(self.paths).add(
            recording_id,
            elapsed_ms=elapsed_ms,
            event_type=event_type,
            actor=actor,
            outcome=outcome,
            label=label,
            source="manual",
            status="confirmed",
        )

    def confirm_timeline_event(self, event_id: str) -> DuelEvent:
        return DuelTimelineRepository.from_runtime_paths(self.paths).confirm(event_id)

    def reject_timeline_event(self, event_id: str) -> DuelEvent:
        return DuelTimelineRepository.from_runtime_paths(self.paths).reject(event_id)

    def check_history(self) -> tuple[ConsistencyIssue, ...]:
        return RecordingHistoryRepository.from_runtime_paths(
            self.paths
        ).check_consistency()

    def delete_history(self, recording_id: str) -> HistoryDeletionResult:
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._manual_starting or self._current is not None or self.watch_active:
                raise ApplicationOperationError(
                    "録画または自動監視の実行中は履歴を削除できません"
                )
        return RecordingHistoryRepository.from_runtime_paths(self.paths).delete(
            recording_id
        )

    def resolve_recording(self, recording_id: str) -> RecordingReference:
        return self._browser().resolve(recording_id)

    def play_recording(self, recording_id: str) -> RecordingReference:
        return self._browser().play(recording_id)

    def reveal_recording(self, recording_id: str) -> RecordingReference:
        return self._browser().reveal(recording_id)

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

    def process_preparations(
        self, queue_id: str | None = None
    ) -> tuple[UploadPreparationResult, ...]:
        return self._upload_preparation_service().process(queue_id)

    def close(self) -> None:
        with self._lock:
            if self._manual_starting:
                raise ApplicationOperationError("録画の開始処理中は終了できません")
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
        frame_stream: PersistentFfmpegRegionFrameCapture | None = None
        diagnostics: VisualDiagnosticSession | None = None
        confirmed_recording_id: str | None = None
        reported_restart_count = 0
        try:
            loaded = self.load_config()
            config = loaded.config
            if config.auto_start_recording and not config.visual_detection_enabled:
                raise ApplicationOperationError(
                    "自動録画の開始には画面イベント判定が必要です。設定で有効にしてください"
                )
            watch_config = replace(
                config,
                capture_mode="master_duel",
                capture_target_id="",
            )
            report = run_preflight(
                paths=self.paths,
                config=watch_config,
                config_loaded=loaded.config_loaded,
            )
            if not report.succeeded:
                detail = report.error_summary or "失敗項目を取得できません"
                raise ApplicationOperationError(
                    f"自動監視の開始前診断に失敗しました: {detail}"
                )
            discovery = discover_ffmpeg(watch_config.ffmpeg_path)
            if not discovery.found or discovery.executable is None:
                raise ApplicationOperationError(
                    "対戦開始判定に使うFFmpegを再検出できません"
                )
            monitor = GameWindowMonitor(
                process_name=watch_config.game_process_name,
                title_contains=watch_config.game_window_title_contains,
            )
            window_detector = MasterDuelWindowDetector(monitor)
            frame_stream = PersistentFfmpegRegionFrameCapture(
                discovery.executable,
                maximum_fps=watch_config.visual_detection_maximum_fps,
            )
            diagnostics = VisualDiagnosticSession(self.paths.logs)

            def capture_current_window() -> FrameCaptureResult:
                game = monitor.observe()
                if game.status is not GameWindowStatus.VISIBLE or game.window is None:
                    return FrameCaptureResult(None, game.message)
                return frame_stream.capture(game.window)

            def record_analysis(analysis: FrameAnalysis) -> None:
                if diagnostics is not None:
                    diagnostics.record(
                        analysis,
                        restart_count=frame_stream.restart_count,
                    )

            start_monitor = MasterDuelStartMonitor(
                window_detector,
                capture=frame_stream.capture,
                minimum_confidence=max(
                    watch_config.visual_detection_minimum_confidence,
                    watch_config.detection_minimum_confidence,
                ),
                confirmations=max(2, watch_config.start_confirmations),
                on_analysis=record_analysis,
            )
            controller = AutoRecordingController(
                state_machine=DuelDetectionStateMachine(
                    DetectionPolicy(
                        start_confirmations=1,
                        stop_confirmations=watch_config.stop_confirmations,
                        minimum_confidence=0.0,
                        cooldown_seconds=watch_config.detection_cooldown_seconds,
                        automatic_start=watch_config.auto_start_recording,
                        automatic_stop=watch_config.auto_stop_recording,
                    )
                ),
                recording_factory=lambda observation: prepare_recording(
                    paths=self.paths,
                    config=watch_config,
                    capture_input=_automatic_capture_input(observation),
                    visual_frame_capture=capture_current_window,
                    visual_analysis_callback=record_analysis,
                    visual_source=frame_stream.source_description,
                    visual_restart_counter=lambda: frame_stream.restart_count,
                    visual_frame_generation=lambda: frame_stream.generation,
                ),
            )
            self._emit(
                callback,
                ApplicationEvent("watch", "自動監視を開始しました", state="watching"),
            )
            while not self._watch_stop.is_set():
                observation = (
                    start_monitor.observe()
                    if controller.current is None and watch_config.auto_start_recording
                    else window_detector.observe()
                )
                if self._watch_stop.is_set():
                    break
                event = controller.process(observation)
                if event.action is AutoRecordingEventAction.STARTED:
                    diagnostics.transition("candidate_started", elapsed_ms=0)
                    self._save_automatic_start_candidate(
                        event.recording_id,
                        start_monitor.start_candidate,
                        callback,
                    )
                lifecycle_prepared = controller.current
                lifecycle_event = self._apply_automatic_visual_lifecycle(
                    controller,
                    start_monitor,
                    callback,
                )
                if lifecycle_event is not None:
                    transition = "result_stopped"
                    if lifecycle_prepared is not None:
                        if (
                            lifecycle_prepared.visual_abort_reason is not None
                            or not lifecycle_prepared.duel_confirmed
                        ):
                            transition = "candidate_discarded"
                        elif lifecycle_prepared.boundary_detected_monotonic is not None:
                            transition = "boundary_stopped"
                    diagnostics.transition(transition)
                    event = lifecycle_event
                if controller.current is not None:
                    if (
                        controller.current.duel_confirmed
                        and controller.current.target.recording_id
                        != confirmed_recording_id
                    ):
                        confirmed_recording_id = controller.current.target.recording_id
                        diagnostics.transition("duel_confirmed")
                        self._emit(
                            callback,
                            ApplicationEvent(
                                "visual_transition",
                                "対戦盤面を確認し、候補録画を正式履歴へ昇格しました",
                                recording_id=confirmed_recording_id,
                                state="confirmed",
                            ),
                        )
                    self._publish_visual_status(controller.current, callback)
                else:
                    if event.action is AutoRecordingEventAction.STOPPED:
                        if lifecycle_event is None:
                            diagnostics.transition("recording_stopped")
                        start_monitor.reset()
                        confirmed_recording_id = None
                    self._set_visual_status(start_monitor.status, callback)
                current_status = (
                    controller.current.visual_detection_status
                    if controller.current is not None
                    else start_monitor.status
                )
                if current_status.restart_count > reported_restart_count:
                    reported_restart_count = current_status.restart_count
                    diagnostics.transition("stream_restarted")
                    self._emit(
                        callback,
                        ApplicationEvent(
                            "visual_transition",
                            f"判定ストリームを再起動しました ({reported_restart_count}回)",
                            state="degraded",
                        ),
                    )
                if event.action is not AutoRecordingEventAction.NONE:
                    self._emit(callback, _application_event(event))
                interval = (
                    1 / watch_config.visual_detection_maximum_fps
                    if controller.current is None and watch_config.auto_start_recording
                    else watch_config.detection_poll_interval_seconds
                )
                self._watch_stop.wait(interval)
        except Exception as exc:
            self._emit(callback, ApplicationEvent("error", str(exc), state="failed"))
        finally:
            if controller is not None and controller.current is not None:
                event = controller.manual_stop()
                self._emit(callback, _application_event(event))
            if frame_stream is not None:
                frame_stream.stop()
            if diagnostics is not None:
                diagnostics.close()
            self._emit(
                callback,
                ApplicationEvent("watch", "自動監視を停止しました", state="stopped"),
            )

    def _apply_automatic_visual_lifecycle(
        self,
        controller: AutoRecordingController,
        start_monitor: MasterDuelStartMonitor,
        callback: EventCallback | None,
    ) -> AutoRecordingEvent | None:
        prepared = controller.current
        if prepared is None:
            return None
        abort_reason = prepared.visual_abort_reason
        started_at = prepared.session.started_at
        unconfirmed_seconds = (
            max(0.0, (datetime.now(timezone.utc) - started_at).total_seconds())
            if started_at is not None and not prepared.duel_confirmed
            else 0.0
        )
        timed_out = unconfirmed_seconds >= 45.0
        result_detected = prepared.result_detected_monotonic
        boundary_detected = prepared.boundary_detected_monotonic
        post_roll_complete = (
            result_detected is not None and time.monotonic() - result_detected >= 3.0
        ) or (
            boundary_detected is not None
            and time.monotonic() - boundary_detected >= 1.0
        )
        if abort_reason is None and not timed_out and not post_roll_complete:
            return None

        recording_id = prepared.target.recording_id
        event = controller.manual_stop()
        if abort_reason is not None or timed_out:
            reason = abort_reason or "45秒以内に対戦盤面を確認できませんでした"
            try:
                RecordingHistoryRepository.from_runtime_paths(self.paths).delete(
                    recording_id
                )
            except Exception as exc:
                self._emit(
                    callback,
                    ApplicationEvent(
                        "visual",
                        f"候補録画を停止しましたが隔離削除に失敗しました: {exc}",
                        recording_id=recording_id,
                        state="degraded",
                    ),
                )
            else:
                event = replace(
                    event,
                    message=f"対戦不成立として候補録画を取り消しました: {reason}",
                )
        else:
            message = (
                "次の対戦開始を録画境界として前の録画を停止しました"
                if boundary_detected is not None
                else "対戦結果の3秒後に録画を停止しました"
            )
            event = replace(event, message=message)
        start_monitor.reset()
        return event

    def _save_automatic_start_candidate(
        self,
        recording_id: str | None,
        candidate: DetectionCandidate | None,
        callback: EventCallback | None,
    ) -> None:
        if recording_id is None or candidate is None:
            return
        try:
            DuelTimelineRepository.from_runtime_paths(self.paths).add(
                recording_id,
                elapsed_ms=0,
                event_type="duel_start",
                label=f"録画開始前に検出: {candidate.reason}",
                source="detected",
                confidence=candidate.confidence,
                status="candidate",
                detector_id=candidate.detector_id,
                detector_version=candidate.detector_version,
            )
        except Exception as exc:
            self._emit(
                callback,
                ApplicationEvent(
                    "visual",
                    f"録画は継続しますが、開始候補を保存できません: {exc}",
                    recording_id=recording_id,
                    state="degraded",
                ),
            )

    def _collect_manual_terminal_locked(self) -> None:
        if self._current is None:
            return
        state = self._current.poll()
        if state not in {RecordingState.COMPLETED, RecordingState.FAILED}:
            return
        self._visual_status = self._current.visual_detection_status
        self._current.release()
        self._current = None

    def _publish_visual_status(
        self,
        prepared: PreparedRecording,
        callback: EventCallback | None,
    ) -> None:
        self._set_visual_status(prepared.visual_detection_status, callback)

    def _set_visual_status(
        self,
        status: VisualDetectionStatus,
        callback: EventCallback | None,
    ) -> None:
        if status == self._visual_status:
            return
        self._visual_status = status
        self._emit(
            callback, ApplicationEvent("visual", status.message, state=status.state)
        )

    def _manual_snapshot_locked(self) -> RecordingSnapshot:
        if self._current is None:
            return RecordingSnapshot(
                False, RecordingState.COMPLETED, None, None, None, 0.0
            )
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
        validator = UploadMediaValidator(
            ffprobe_executable=find_ffprobe(discovery.executable)
        )
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
        state=event.result.state.value
        if event.result is not None
        else event.action.value,
    )


def _automatic_capture_input(observation: DuelObservation) -> CaptureInput:
    return capture_input_for_window_region(
        WindowSnapshot(
            handle=observation.capture_window_handle or 0,
            pid=observation.capture_process_id or 0,
            title=observation.capture_window_title or "",
            visible=True,
            minimized=False,
            width=observation.capture_width or 0,
            height=observation.capture_height or 0,
            left=observation.capture_left or 0,
            top=observation.capture_top or 0,
        )
    )


def _elapsed(started_at: datetime | None, ended_at: datetime | None = None) -> float:
    if started_at is None:
        return 0.0
    end = ended_at or datetime.now(timezone.utc)
    return max(0.0, (end - started_at).total_seconds())
