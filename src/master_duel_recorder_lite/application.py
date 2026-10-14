from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
import shutil
import subprocess
import threading
import time

from .auto_recording import (
    AutoRecordingController,
    AutoRecordingEvent,
    AutoRecordingEventAction,
)
from .audio_loopback import (
    ProcessLoopbackController,
    ProcessLoopbackError,
    new_audio_pipe_name,
    process_loopback_capability,
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
from .clip_export import ClipExportResult, ClipExportService
from .card_analysis_jobs import CARD_ANALYZER_VERSION, CardAnalysisManager
from .card_analysis_models import CardInputFingerprint
from .card_analysis_repository import CardAnalysisError, CardAnalysisRepository
from .card_analysis_statistics import CardAnalysisStatisticsRepository
from .config import (
    AppConfig,
    LoadedAppConfig,
    load_app_config,
    save_app_config,
    validate_app_config,
)
from .config_management import updated_config_values
from .data_management import ManagedDataResult, ManagedDataService
from .data_location import DataRelocationResult, relocate_runtime_data
from .data_protection import (
    BackupInfo,
    DataProtectionService,
    IntegrityReport,
    RestorePreview,
)
from .data_reconciliation import (
    DataReconciliationService,
    DuplicateCandidate,
    RelinkPreview,
)
from .description_template import (
    YouTubePostingTemplate,
    load_youtube_posting_template,
    save_youtube_posting_template,
    youtube_template_aliases,
)
from .detection import DetectionPolicy, DuelDetectionStateMachine, DuelObservation
from .duel_catalog import DuelCatalogEntry, DuelCatalogRepository
from .duel_card_repository import DuelCardRepository
from .duel_csv import DuelCsvImportResult, DuelCsvPreview, DuelCsvService
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
from .duel_workflow import (
    BulkDuelUpdate,
    DuelFilterCriteria,
    DuelInputSuggestion,
    DuelWorkflowService,
    IncompleteDuel,
    SavedDuelFilter,
)
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
from .operation_state import (
    OperationAction,
    OperationSnapshot,
    OperationState,
    OperationStateMachine,
)
from .preflight import PreflightReport, run_preflight
from .preroll import FrozenPreroll, PrerollCaptureBuffer, new_preroll_buffer
from .recorder import AutoWatchDuelDefaults, PreparedRecording, prepare_recording
from .recording_history import (
    ConsistencyIssue,
    HistoryDeletionResult,
    HistoryQuery,
    RecordingHistoryEntry,
    RecordingHistoryRepository,
)
from .recording_profile import RecordingProfile
from .recording_browsing import RecordingBrowser, RecordingReference
from .recording_session import RecordingResult, RecordingState
from .review_viewmodel import (
    ReviewClipExportRequest,
    ReviewMarkerRequest,
    ReviewViewModel,
    build_review_view_model,
)
from .season_report_html import SeasonReportHtmlExporter
from .season_reports import SeasonReport, SeasonReportService
from .seasons import Season, SeasonRepository
from .runtime_paths import default_runtime_paths
from .video_lifecycle import RecordingVideoState, VideoLifecycleRepository
from .upload_export import UploadExporter
from .upload_manifest import UploadManifestWriter
from .upload_media import UploadMediaValidator, find_ffprobe
from .upload_metadata import UploadMetadata, UploadPrivacy
from .upload_preparation import UploadPreparationResult, UploadPreparationService
from .upload_queue import UploadQueueItem, UploadQueueState, UploadQueueStore
from .visual_detection import DetectionCandidate, FrameAnalysis
from .visual_diagnostics import VisualDiagnosticSession
from .visual_worker import VisualDetectionStatus
from .windows_notification import NotificationMessage, WindowsNotificationService
from .windows_process import subprocess_creation_flags
from .youtube_client import YouTubeClient
from .youtube_oauth import (
    CredentialStore,
    WindowsCredentialStore,
    authorize_with_loopback,
    distributed_oauth_client_configured,
    load_distributed_oauth_client,
)
from .youtube_service import YouTubeUploadOutcome, YouTubeUploadService
from .youtube_uploads import YouTubeUploadRepository
from .youtube_materials import YouTubeMaterialService


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
class PreparationCandidate:
    recording_id: str
    label: str
    title: str


@dataclass(frozen=True)
class ApplicationEvent:
    kind: str
    message: str
    recording_id: str | None = None
    state: str = ""


@dataclass(frozen=True)
class DuelEditorRecordingStatus:
    recording_id: str | None
    output_path: Path | None
    file_exists: bool
    youtube_watch_url: str | None
    youtube_video_id: str | None


@dataclass(frozen=True)
class DuelEditorData:
    record: DuelRecord | None
    values: DuelRecordValues
    decks: tuple[DuelCatalogEntry, ...]
    tags: tuple[DuelCatalogEntry, ...]
    deck_tags: dict[int, tuple[DuelCatalogEntry, ...]]
    recording: DuelEditorRecordingStatus
    seasons: tuple[Season, ...]
    suggestion_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class YouTubeUploadDialogData:
    recording_id: str
    title: str
    description: str
    tags: tuple[str, ...]
    privacy: str
    youtube_watch_url: str | None


@dataclass(frozen=True)
class DuelManagementQuery:
    limit: int = 200
    occurred_from: date | None = None
    occurred_to: date | None = None
    season_id: int | None = None
    own_deck_id: int | None = None
    opponent_deck_id: int | None = None
    tag_entry_ids: tuple[int, ...] = ()
    coin_face: str | None = None
    entry_origin: str | None = None
    incomplete_only: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise ValueError("limitは整数である必要があります")
        if not 1 <= self.limit <= 1000:
            raise ValueError("limitは1から1000である必要があります")
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_from > self.occurred_to
        ):
            raise ValueError("開始日は終了日以前である必要があります")
        if self.entry_origin not in {None, "recording", "manual", "import"}:
            raise ValueError(f"未対応の登録元です: {self.entry_origin}")


@dataclass(frozen=True)
class RecordingHistoryView:
    entry: RecordingHistoryEntry | None
    duel_record: DuelRecord | None
    own_deck_color: str | None = None
    video_state: RecordingVideoState | None = None

    @property
    def row_id(self) -> str:
        if self.duel_record is not None:
            return self.duel_record.duel_id
        assert self.entry is not None
        return f"recording:{self.entry.recording_id}"

    @property
    def recording_id(self) -> str | None:
        if self.duel_record is not None:
            return self.duel_record.recording_id
        return self.entry.recording_id if self.entry is not None else None

    @property
    def occurred_at(self) -> datetime:
        if self.duel_record is not None:
            return self.duel_record.occurred_at
        assert self.entry is not None
        return self.entry.started_at or self.entry.created_at

    @property
    def entry_origin(self) -> str:
        return (
            self.duel_record.entry_origin
            if self.duel_record is not None
            else "recording"
        )

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
    def duel_type(self) -> str:
        return (
            self.duel_record.values.duel_type
            if self.duel_record is not None
            else "other"
        )

    @property
    def own_deck(self) -> str:
        return self.duel_record.values.own_deck if self.duel_record is not None else ""

    @property
    def opponent_deck(self) -> str:
        return (
            self.duel_record.values.opponent_deck
            if self.duel_record is not None
            else ""
        )


@dataclass(frozen=True)
class RecordingHistoryDashboard:
    views: tuple[RecordingHistoryView, ...]
    incomplete_duel_record_count: int


@dataclass(frozen=True)
class ActiveSeasonSummary:
    season: Season
    statistics: StatisticsDashboard


@dataclass(frozen=True)
class YouTubeConnectionStatus:
    state: str
    message: str
    scope: str = ""
    can_connect: bool = False


@dataclass(frozen=True)
class YouTubePreparationStatus:
    state: str
    message: str
    queue_id: str = ""


EventCallback = Callable[[ApplicationEvent], None]


def _ffmpeg_peak_level(stderr: str) -> float | None:
    marker = "Peak level dB:"
    values: list[float] = []
    for line in stderr.splitlines():
        if marker not in line:
            continue
        raw = line.rsplit(marker, 1)[1].strip()
        try:
            values.append(float(raw))
        except ValueError:
            continue
    return max(values) if values else None


class RecorderApplicationService:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        user_data_dir: Path | None = None,
        target_catalog: CaptureTargetCatalog | None = None,
        recording_browser: RecordingBrowser | None = None,
        ffmpeg_installer: FfmpegInstaller | None = None,
        youtube_credential_store: CredentialStore | None = None,
        youtube_client: YouTubeClient | None = None,
        youtube_oauth_environ: Mapping[str, str] | None = None,
        require_gui_startup: bool = False,
    ) -> None:
        self.project_root = project_root
        self.user_data_dir = user_data_dir
        self.paths = default_runtime_paths(
            project_root=project_root, user_data_dir=user_data_dir
        )
        self._target_catalog = target_catalog
        self._recording_browser = recording_browser
        self._ffmpeg_installer = ffmpeg_installer or FfmpegInstaller()
        self._youtube_credential_store = youtube_credential_store or WindowsCredentialStore()
        self._youtube_client = youtube_client
        self._youtube_oauth_environ = youtube_oauth_environ
        self._lock = threading.RLock()
        self._current: PreparedRecording | None = None
        self._automatic_snapshot: RecordingSnapshot | None = None
        self._watch_duel_defaults = AutoWatchDuelDefaults()
        self._manual_starting = False
        self._watch_thread: threading.Thread | None = None
        self._watch_stop = threading.Event()
        self._visual_status = VisualDetectionStatus(
            "disabled", "録画開始後に自動判定状態を表示します", 0, 0, 0
        )
        self._operation_state = OperationStateMachine()
        self._notifications = WindowsNotificationService()
        self._recording_history_revision = 0
        self._card_repository: CardAnalysisRepository | None = None
        self._video_repository: VideoLifecycleRepository | None = None
        self._card_manager: CardAnalysisManager | None = None
        self._closed = False
        self._card_recording_starting = False
        self._gui_startup_required = require_gui_startup
        self._gui_startup_completed = False
        self._gui_startup_running = False
        self._gui_startup_result = None
        self.startup_maintenance_message = "起動時の動画保存チェックを待っています"
        self.card_analysis_error = ""
        if not require_gui_startup and self.load_config().config.card_analysis_enabled:
            self._ensure_card_manager()

    @property
    def gui_startup_ready(self) -> bool:
        return not self._gui_startup_required or (
            self._gui_startup_completed and bool(getattr(self._gui_startup_result, "operation_allowed", False))
        )

    def run_gui_startup_maintenance(self):
        from .startup_maintenance import run_startup_maintenance

        with self._lock:
            if self._gui_startup_completed:
                return self._gui_startup_result
            if self._gui_startup_running:
                raise ApplicationOperationError("起動時の動画保存チェックを実行中です")
            self._gui_startup_required = True
            self._gui_startup_running = True
        try:
            def progress(message: str) -> None:
                self.startup_maintenance_message = message
            result = run_startup_maintenance(self.paths, progress=progress)
            with self._lock:
                self._gui_startup_result = result
                self._gui_startup_completed = True
                self.startup_maintenance_message = result.message
            if result.operation_allowed and not self._closed and self.load_config().config.card_analysis_enabled:
                self._ensure_card_manager()
            return result
        finally:
            self._gui_startup_running = False

    def _require_gui_startup_ready(self) -> None:
        if not self.gui_startup_ready:
            raise ApplicationOperationError(self.startup_maintenance_message)

    def _get_video_repository(self) -> VideoLifecycleRepository:
        with self._lock:
            if self._video_repository is None:
                self._video_repository = VideoLifecycleRepository.from_runtime_paths(self.paths)
            return self._video_repository

    def get_recording_video_state(self, recording_id: str) -> RecordingVideoState:
        repository = self._get_video_repository()
        repository.ensure_current_asset(recording_id)
        return repository.get_recording_video_state(recording_id)

    def set_recording_video_protection(
        self, recording_id: str, protected: bool, *, expected_revision: int | None = None,
    ):
        self._require_duel_write_idle()
        result = self._get_video_repository().set_protection(
            recording_id, protected, expected_revision=expected_revision,
        )
        with self._lock:
            self._recording_history_revision += 1
        return result

    def delete_recording_video(self, recording_id: str, *, allow_protected: bool = False):
        from .recording_retention import RecordingDeletionService

        self._require_data_management_idle()
        result = RecordingDeletionService(self.paths, repository=self._get_video_repository()).delete_video(
            recording_id, reason="manual", allow_protected=allow_protected,
        )
        with self._lock:
            self._recording_history_revision += 1
        return result

    @property
    def recording_history_revision(self) -> int:
        """録画完了をGUIのポーリング間隔に関係なく伝える。"""
        with self._lock:
            return self._recording_history_revision

    def operation_snapshot(self) -> OperationSnapshot:
        return self._operation_state.snapshot

    def _transition_operation(self, state: OperationState, message: str) -> None:
        self._operation_state.transition(state, message)

    def _notify(self, event: str, message: str, key: str) -> None:
        try:
            self._notifications.enabled = (
                self.load_config().config.windows_notifications_enabled
            )
            self._notifications.notify(
                NotificationMessage(event, "Master Duel Recorder Lite", message, key)
            )
        except Exception:
            # OS notifications are supplementary and must never interrupt recording.
            return

    def load_config(self) -> LoadedAppConfig:
        return load_app_config(
            project_root=self.project_root, user_data_dir=self.user_data_dir
        )

    def save_settings(self, values: Mapping[str, str]) -> AppConfig:
        loaded = self.load_config()
        config = updated_config_values(loaded.config, values)
        save_app_config(paths=self.paths, config=config)
        if config.card_analysis_enabled and self.gui_startup_ready:
            self._ensure_card_manager().wake()
        elif self._card_manager is not None:
            self._card_manager.interrupt()
        return config

    def _get_card_repository(self) -> CardAnalysisRepository:
        with self._lock:
            if self._card_repository is None:
                self._card_repository = CardAnalysisRepository.from_runtime_paths(self.paths)
                self._card_repository.seed_presets()
            return self._card_repository

    def _card_analysis_can_run(self) -> bool:
        # workerからはアプリlockを取得せず、録画開始側とのlock逆転を避ける。
        return (self.gui_startup_ready and not self._closed and not self._manual_starting and not self._card_recording_starting
                and self._current is None
                and self._operation_state.snapshot.state in {
                    OperationState.IDLE, OperationState.WATCH_WAITING, OperationState.FAILED,
                })

    def _ensure_card_manager(self) -> CardAnalysisManager:
        with self._lock:
            if self._closed:
                raise CardAnalysisError("終了後はカード分析を開始できません")
            if self._card_manager is None:
                self._card_manager = CardAnalysisManager(
                    paths=self.paths, repository=self._get_card_repository(),
                    settings=lambda: self.load_config().config,
                    can_run=self._card_analysis_can_run,
                )
            self._card_manager.start()
            return self._card_manager

    def _interrupt_card_analysis(self) -> None:
        if self._card_manager is not None:
            self._card_manager.interrupt()

    def list_card_targets(self, *, include_disabled: bool = True):
        return self._get_card_repository().list_targets(include_disabled=include_disabled)

    def save_card_target(self, *, name: str, aliases=(), purpose: str = "", enabled: bool = True,
                         target_id: str | None = None, expected_revision: int | None = None):
        return self._get_card_repository().save_target(
            name=name, aliases=tuple(aliases), purpose=purpose, enabled=enabled,
            target_id=target_id, expected_revision=expected_revision,
        )

    def _get_duel_card_repository(self) -> DuelCardRepository:
        return DuelCardRepository(self._get_card_repository().database_path)

    def list_card_identities(self):
        return self._get_duel_card_repository().list_identities()

    def save_card_identity(self, *, name: str):
        self._require_duel_write_idle()
        return self._get_duel_card_repository().save_identity(name=name)

    def get_duel_card_review(self, duel_id: str):
        return self._get_duel_card_repository().get_review(duel_id)

    def save_duel_card_information(self, duel_id: str, card_id: str, actor: str, state: str, *,
                                  expected_revision: int | None = None, is_opener: bool | None = None,
                                  opener_expected_revision: int | None = None):
        self._require_duel_write_idle()
        return self._get_duel_card_repository().save_information(
            duel_id, card_id, actor, state, expected_revision=expected_revision,
            is_opener=is_opener, opener_expected_revision=opener_expected_revision,
        )

    def set_duel_card_opener(self, duel_id: str, actor: str, *, card_id: str | None = None,
                             status: str = "confirmed", expected_revision: int | None = None):
        self._require_duel_write_idle()
        return self._get_duel_card_repository().set_opener(
            duel_id, actor, card_id=card_id, status=status, expected_revision=expected_revision,
        )

    def get_card_analysis_review(self, recording_id: str):
        return self._get_card_repository().get_review(recording_id)

    def get_card_analysis_jobs(self, recording_id: str | None = None):
        return self._get_card_repository().list_jobs(recording_id)

    def get_card_analysis_job(self, job_id: str):
        return self._get_card_repository().get_job(job_id)

    def request_card_analysis(self, recording_id: str, *, reanalyse: bool = False):
        self._require_gui_startup_ready()
        config = self.load_config().config
        if not config.card_analysis_enabled:
            raise CardAnalysisError("設定でカード分析をONにしてください")
        history = self.get_history(recording_id)
        if history.state != "completed":
            raise CardAnalysisError("正常完了した録画だけを分析できます")
        repository = self._get_card_repository()
        review = repository.get_review(recording_id)
        if not reanalyse and review.successful_job is not None:
            return review.successful_job
        path = self.resolve_recording(recording_id).path.resolve()
        stat = path.stat()
        # 大きな動画のSHAは低優先workerで計算し、録画完了を待たせない。
        job = repository.create_job(
            recording_id, fingerprint=CardInputFingerprint(str(path), stat.st_size, stat.st_mtime_ns),
            analyzer_version=CARD_ANALYZER_VERSION, sample_fps=config.card_analysis_fps,
        )
        if not self._closed:
            self._ensure_card_manager().wake()
        return job

    def _enqueue_completed_card_analysis(self, recording_id: str) -> None:
        try:
            if not self.load_config().config.card_analysis_enabled:
                return
            self.request_card_analysis(recording_id)
            self.card_analysis_error = ""
        except Exception as exc:
            # 分析の失敗は録画の正常完了と分けて扱う。
            self.card_analysis_error = str(exc)

    def update_card_observation(self, event_id: str, *, expected_revision: int,
                                status: str | None = None, is_opener: bool | None = None,
                                opener_expected_revision: int | None = None,
                                information_expected_revision: int | None = None,
                                expected_generation: int | None = None, **changes):
        repository = self._get_card_repository()
        current = repository.get_observation(event_id)
        if current is None:
            raise CardAnalysisError("カード観測がありません")
        aliases = {"card_target_id": "target_id", "actual_use_ms": "actual_ms", "name": "card_name"}
        values = replace(current.values, **{aliases.get(key, key): value for key, value in changes.items()})
        if status == "confirmed":
            if values.actor == "unknown" or values.action == "unknown":
                raise CardAnalysisError("使用を確定するには使用者と行動を選んでください")
            values = replace(values, evidence={**values.evidence, "supports_use": True,
                                               "manually_confirmed_use": True})
            if information_expected_revision is None and values.target_id is not None:
                target = repository.get_target(values.target_id)
                review = repository.get_review(current.recording_id)
                information = next((item for item in review.information if target is not None
                                    and item.card_id == target.card_id and item.actor == values.actor), None)
                information_expected_revision = information.revision if information else 0
        return repository.review_observation(
            event_id, expected_revision=expected_revision, values=values, status=status,
            is_opener=is_opener, opener_expected_revision=opener_expected_revision,
            information_expected_revision=information_expected_revision,
            expected_generation=current.generation if expected_generation is None else expected_generation,
        )

    def confirm_card_observation(self, event_id: str, *, expected_revision: int):
        return self.update_card_observation(event_id, expected_revision=expected_revision, status="confirmed")

    def reject_card_observation(self, event_id: str, *, expected_revision: int):
        return self.update_card_observation(event_id, expected_revision=expected_revision, status="rejected")

    def set_card_nonuse(self, recording_id: str, target_id: str, actor: str, confirmed: bool = True,
                       *, expected_revision: int | None = None):
        return self._get_card_repository().set_nonuse(
            recording_id, target_id, actor, confirmed, expected_revision=expected_revision,
        )

    def set_card_opener(self, recording_id: str, actor: str, *, event_id: str | None = None,
                       target_id: str | None = None, status: str = "confirmed",
                       expected_revision: int | None = None):
        return self._get_card_repository().set_opener(
            recording_id, actor, event_id=event_id, target_id=target_id, status=status,
            expected_revision=expected_revision,
        )

    def get_card_statistics(self, filters: StatisticsFilter | None = None, *, target_id: str | None = None,
                            actor: str = "self", opener_target_id: str | None = None,
                            include_candidates: bool = False):
        if target_id is None:
            targets = self.list_card_identities()
            if not targets:
                raise CardAnalysisError("カード情報または対象カードを追加してください")
            target_id = targets[0].card_id
        return CardAnalysisStatisticsRepository.from_runtime_paths(self.paths).dashboard(
            filters, target_id=target_id, actor=actor, opener_target_id=opener_target_id,
            include_candidates=include_candidates,
        )

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

    def relocate_runtime_data(self, destination: Path) -> DataRelocationResult:
        self._require_data_management_idle()
        return relocate_runtime_data(self.paths, destination)

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

    def select_ffmpeg_executable(self, executable: Path) -> Path:
        selected = executable.expanduser().resolve()
        discovery = discover_ffmpeg(str(selected))
        if (
            not selected.is_file()
            or not discovery.found
            or discovery.version is None
            or not discovery.version.is_supported
        ):
            raise ApplicationOperationError(
                "FFmpeg 6.0以上のffmpeg.exeを選択してください"
            )
        self.save_settings({"recorder.ffmpeg_path": str(selected)})
        return selected

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

    def test_process_audio(self) -> AudioInputTestResult:
        capability = process_loopback_capability()
        if not capability.supported or capability.helper_path is None:
            return AudioInputTestResult("unavailable", capability.message)
        config = self.load_config().config
        observation = GameWindowMonitor(
            process_name=config.game_process_name,
            title_contains=config.game_window_title_contains,
        ).observe()
        if observation.process is None:
            return AudioInputTestResult(
                "unavailable", "Master Duelを起動してからテストしてください"
            )
        discovery = discover_ffmpeg(config.ffmpeg_path)
        if not discovery.found or discovery.executable is None:
            return AudioInputTestResult("unavailable", "FFmpegが見つかりません")
        pipe_name = new_audio_pipe_name("audio-test")
        controller = ProcessLoopbackController(
            helper_path=capability.helper_path,
            process_id=observation.process.pid,
            pipe_name=pipe_name,
        )
        try:
            controller.start()
            completed = subprocess.run(
                [
                    str(discovery.executable),
                    "-hide_banner",
                    "-loglevel",
                    "info",
                    "-f",
                    "s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-i",
                    pipe_name,
                    "-t",
                    "3",
                    "-af",
                    "astats=metadata=0:reset=0",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8.0,
                check=False,
                creationflags=subprocess_creation_flags(),
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip().splitlines()
                return AudioInputTestResult(
                    "unavailable",
                    "Master Duel単体音声を取得できません: "
                    + (detail[-1] if detail else f"終了コード{completed.returncode}"),
                )
            peak = _ffmpeg_peak_level(completed.stderr)
            if peak is None or peak <= -90.0:
                return AudioInputTestResult(
                    "silent",
                    "取得経路は正常ですが、3秒間は無音でした。ゲーム音を再生して再確認できます",
                )
            return AudioInputTestResult(
                "available", f"Master Duel単体音声を取得できました（ピーク {peak:.1f} dB）"
            )
        except (OSError, ProcessLoopbackError, subprocess.TimeoutExpired) as exc:
            return AudioInputTestResult("unavailable", f"単体音声テストに失敗しました: {exc}")
        finally:
            controller.stop()

    def start_recording(self, target: CaptureTarget | None = None) -> RecordingSnapshot:
        self._require_gui_startup_ready()
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._manual_starting:
                raise ApplicationOperationError("録画の開始処理はすでに実行中です")
            if self._current is not None:
                raise ApplicationOperationError("録画はすでに実行中です")
            if self.watch_active:
                raise ApplicationOperationError("自動監視中は手動録画を開始できません")
            try:
                self._operation_state.require(OperationAction.START_MANUAL)
            except RuntimeError as exc:
                raise ApplicationOperationError(str(exc)) from exc
            self._manual_starting = True
            self._transition_operation(OperationState.MANUAL_STARTING, "手動録画を開始しています")
            self._interrupt_card_analysis()

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
                self._transition_operation(OperationState.MANUAL_RECORDING, "手動録画中")
                self._notify(
                    "recording_started",
                    "手動録画を開始しました",
                    f"{prepared.target.recording_id}:started",
                )
                return self._manual_snapshot_locked()
        except BaseException:
            try:
                if prepared is not None:
                    prepared.release()
            finally:
                with self._lock:
                    self._manual_starting = False
                    self._transition_operation(OperationState.FAILED, "手動録画を開始できませんでした")
            raise

    def recording_snapshot(self) -> RecordingSnapshot:
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._current is not None:
                self._visual_status = self._current.visual_detection_status
                return self._manual_snapshot_locked()
            if self._automatic_snapshot is not None:
                return replace(
                    self._automatic_snapshot,
                    elapsed_seconds=_elapsed(self._automatic_snapshot.started_at),
                )
            return self._manual_snapshot_locked()

    def visual_detection_status(self) -> VisualDetectionStatus:
        with self._lock:
            if self._current is not None:
                self._visual_status = self._current.visual_detection_status
            return self._visual_status

    def export_visual_diagnostics(self, destination: Path) -> Path:
        source = self.paths.logs / "visual-monitor"
        files = sorted(source.glob("*.json"), key=lambda item: item.stat().st_mtime_ns)
        if not files:
            raise ApplicationOperationError("出力できる自動監視診断がありません")
        target = destination.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        import zipfile

        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files[-10:]:
                archive.write(path, arcname=path.name)
        shutil.move(str(temporary), str(target))
        return target

    def stop_recording(self) -> RecordingSnapshot:
        with self._lock:
            try:
                self._operation_state.require(OperationAction.STOP_RECORDING)
            except RuntimeError as exc:
                raise ApplicationOperationError(str(exc)) from exc
            if self._manual_starting:
                raise ApplicationOperationError("録画の開始処理中は停止できません")
            if self._current is None:
                raise ApplicationOperationError("実行中の手動録画はありません")
            prepared = self._current
            self._transition_operation(OperationState.STOPPING, "手動録画を停止しています")
            result: RecordingResult | None = None
            try:
                result = prepared.stop()
            finally:
                self._visual_status = prepared.visual_detection_status
                prepared.release()
                self._current = None
                self._recording_history_revision += 1
                self._transition_operation(
                    OperationState.IDLE
                    if result is not None and result.state is not RecordingState.FAILED
                    else OperationState.FAILED,
                    "待機中"
                    if result is not None and result.state is not RecordingState.FAILED
                    else "録画停止に失敗しました",
                )
            assert result is not None
            if result.state is RecordingState.COMPLETED:
                self._enqueue_completed_card_analysis(prepared.target.recording_id)
            self._notify(
                "recording_stopped"
                if result.state is not RecordingState.FAILED
                else "recording_failed",
                "録画を停止しました"
                if result.state is not RecordingState.FAILED
                else "録画に失敗しました",
                f"{prepared.target.recording_id}:{result.state.value}",
            )
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

    def start_watch(
        self,
        callback: EventCallback | None = None,
        *,
        duel_defaults: AutoWatchDuelDefaults | None = None,
    ) -> None:
        self._require_gui_startup_ready()
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
            try:
                self._operation_state.require(OperationAction.START_WATCH)
            except RuntimeError as exc:
                raise ApplicationOperationError(str(exc)) from exc
            self._watch_duel_defaults = (
                duel_defaults.normalized()
                if duel_defaults is not None
                else AutoWatchDuelDefaults.from_config(self.load_config().config)
            )
            self._watch_stop.clear()
            self._transition_operation(OperationState.WATCH_STARTING, "自動監視を開始しています")
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
            if not thread.is_alive():
                self._watch_thread = None
                return
            snapshot = self._operation_state.snapshot
            if snapshot.state not in {
                OperationState.STOPPING,
                OperationState.CLOSING,
                OperationState.FAILED,
            }:
                self._transition_operation(OperationState.STOPPING, "自動監視を停止しています")
            self._watch_stop.set()
        thread.join(timeout_seconds)
        if thread.is_alive():
            raise ApplicationOperationError("自動監視を正常停止できません")
        with self._lock:
            self._watch_thread = None
            if self._operation_state.snapshot.state not in {
                OperationState.IDLE,
                OperationState.FAILED,
            }:
                self._transition_operation(OperationState.IDLE, "待機中")

    def list_history(
        self, *, limit: int = 200, query: HistoryQuery | None = None
    ) -> tuple[RecordingHistoryEntry, ...]:
        selected = query or HistoryQuery(limit=limit)
        return RecordingHistoryRepository.from_runtime_paths(self.paths).query(selected)

    def list_history_views(
        self, *, limit: int = 200, query: DuelManagementQuery | None = None
    ) -> tuple[RecordingHistoryView, ...]:
        selected = query or DuelManagementQuery(limit=limit)
        entries = self.list_history(limit=1000)
        records = DuelRecordRepository.from_runtime_paths(self.paths).list(limit=1000)
        catalog = DuelCatalogRepository.from_runtime_paths(self.paths)
        decks = catalog.list_decks(include_archived=True)
        tags = catalog.list_tags(include_archived=True)
        deck_colors = {item.name.casefold(): item.color for item in decks}
        entries_by_id = {item.recording_id: item for item in entries}
        consumed: set[str] = set()
        views: list[RecordingHistoryView] = []
        for record in records:
            entry = entries_by_id.get(record.recording_id or "")
            if entry is not None:
                consumed.add(entry.recording_id)
            views.append(
                RecordingHistoryView(
                    entry,
                    record,
                    deck_colors.get(record.values.own_deck.casefold()),
                )
            )
        views.extend(
            RecordingHistoryView(entry, None)
            for entry in entries
            if entry.recording_id not in consumed
        )
        deck_names = {item.entry_id: item.name.casefold() for item in decks}
        tag_names = {item.entry_id: item.name.casefold() for item in tags}

        def matches(view: RecordingHistoryView) -> bool:
            record = view.duel_record
            if selected.incomplete_only and record is not None and record.values.status == "confirmed":
                return False
            occurred_date = view.occurred_at.astimezone().date()
            if selected.occurred_from is not None and occurred_date < selected.occurred_from:
                return False
            if selected.occurred_to is not None and occurred_date > selected.occurred_to:
                return False
            if (
                selected.entry_origin is not None
                and view.entry_origin != selected.entry_origin
            ):
                return False
            if any(
                value is not None
                for value in (
                    selected.season_id,
                    selected.own_deck_id,
                    selected.opponent_deck_id,
                    selected.coin_face,
                )
            ) or selected.tag_entry_ids:
                if record is None:
                    return False
            if record is None:
                return True
            values = record.values
            if (
                selected.season_id is not None
                and values.season_id != selected.season_id
            ):
                return False
            if selected.own_deck_id is not None and (
                values.own_deck.casefold() != deck_names.get(selected.own_deck_id)
            ):
                return False
            if selected.opponent_deck_id is not None and (
                values.opponent_deck.casefold()
                != deck_names.get(selected.opponent_deck_id)
            ):
                return False
            if selected.coin_face is not None and values.coin_face != selected.coin_face:
                return False
            if selected.tag_entry_ids:
                wanted = {tag_names[item] for item in selected.tag_entry_ids if item in tag_names}
                if not wanted.intersection(tag.casefold() for tag in values.tags):
                    return False
            return True

        filtered = sorted(
            (view for view in views if matches(view)),
            key=lambda view: (view.occurred_at, view.row_id),
            reverse=True,
        )
        visible = []
        for view in filtered[: selected.limit]:
            if view.recording_id is not None:
                view = replace(view, video_state=self.get_recording_video_state(view.recording_id))
            visible.append(view)
        return tuple(visible)

    def export_managed_data(self, path: Path) -> ManagedDataResult:
        self._require_data_management_idle()
        return ManagedDataService.from_runtime_paths(self.paths).export_to(path)

    def import_managed_data(self, path: Path) -> ManagedDataResult:
        self._require_data_management_idle()
        return ManagedDataService.from_runtime_paths(self.paths).import_from(path)

    def reset_managed_data(self, scope: str) -> ManagedDataResult:
        self._require_data_management_idle()
        return ManagedDataService.from_runtime_paths(self.paths).reset(scope)

    def export_duel_csv(self, path: Path) -> Path:
        return DuelCsvService(self.paths).export(path)

    def export_duel_csv_sample(self, path: Path) -> Path:
        return DuelCsvService(self.paths).export_sample(path)

    def preview_duel_csv(self, path: Path) -> DuelCsvPreview:
        return DuelCsvService(self.paths).preview(path)

    def import_duel_csv(self, preview: DuelCsvPreview) -> DuelCsvImportResult:
        self._require_data_management_idle()
        return DuelCsvService(self.paths).apply(preview)

    def list_data_backups(self) -> tuple[BackupInfo, ...]:
        return DataProtectionService(self.paths).list_backups()

    def create_data_backup(self, reason: str = "manual") -> BackupInfo:
        self._require_data_management_idle()
        return DataProtectionService(self.paths).create_backup(reason)

    def preview_data_restore(self, path: Path) -> RestorePreview:
        self._require_data_management_idle()
        return DataProtectionService(self.paths).preview_restore(path)

    def restore_data_backup(self, path: Path) -> RestorePreview:
        self._require_data_management_idle()
        preview = DataProtectionService(self.paths).restore(path)
        self._recording_browser = None
        self._card_repository = None
        self._video_repository = None
        if self._get_video_repository().pending_intents():
            self._gui_startup_required = True
            self._gui_startup_completed = True
            from .startup_maintenance import StartupMaintenanceResult
            self._gui_startup_result = StartupMaintenanceResult(
                False, "復元した未完了の動画削除を、次の通常起動で確認してください",
            )
            self.startup_maintenance_message = self._gui_startup_result.message
        with self._lock:
            self._recording_history_revision += 1
        return preview

    def diagnose_data_integrity(self) -> IntegrityReport:
        return DataProtectionService(self.paths).diagnose()

    def preview_recording_relink(
        self, recording_id: str, path: Path
    ) -> RelinkPreview:
        self._require_data_management_idle()
        history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        return DataReconciliationService(history).preview_relink(recording_id, path)

    def relink_recording(self, preview: RelinkPreview) -> None:
        self._require_data_management_idle()
        history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        DataReconciliationService(history).relink(preview)
        self._recording_browser = None
        with self._lock:
            self._recording_history_revision += 1

    def duplicate_duel_candidates(self) -> tuple[DuplicateCandidate, ...]:
        history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        return DataReconciliationService(history).duplicate_candidates()

    def _require_data_management_idle(self) -> None:
        self._require_gui_startup_ready()
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._manual_starting:
                raise ApplicationOperationError("録画開始処理の実行中は管理データを変更できません")
            try:
                self._operation_state.require(OperationAction.MANAGE_DATA)
            except RuntimeError as exc:
                raise ApplicationOperationError(
                    "録画または自動監視の実行中は管理データを変更できません"
                ) from exc

    def get_history_dashboard(
        self, *, limit: int = 200, query: DuelManagementQuery | None = None
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

    def youtube_connection_status(self) -> YouTubeConnectionStatus:
        client_configured = distributed_oauth_client_configured(
            environ=self._youtube_oauth_environ,
            project_root=self.project_root,
        )
        try:
            credentials = self._youtube_credential_store.read()
        except Exception as exc:
            return YouTubeConnectionStatus("error", f"YouTube連携状態を確認できません: {exc}")
        if credentials is None:
            if not client_configured:
                return YouTubeConnectionStatus(
                    "unconfigured",
                    "このビルドではYouTube連携を開始できません。配布者のOAuth client_id設定が必要です。",
                    can_connect=False,
                )
            return YouTubeConnectionStatus(
                "disconnected",
                "YouTubeは未連携です",
                can_connect=True,
            )
        return YouTubeConnectionStatus(
            "connected",
            "YouTube資格情報は保存済みです。有効性は投稿時に確認します。"
            "認証エラーが出た場合は「連携する」で再認証してください。",
            credentials.scope,
            can_connect=client_configured,
        )

    def connect_youtube(self, *, timeout_seconds: float = 180.0) -> YouTubeConnectionStatus:
        try:
            client = load_distributed_oauth_client(
                environ=self._youtube_oauth_environ,
                project_root=self.project_root,
            )
            result = authorize_with_loopback(client, timeout_seconds=timeout_seconds)
            self._youtube_credential_store.write(result.credentials)
        except Exception as exc:
            raise ApplicationOperationError(f"YouTube連携を完了できません: {exc}") from exc
        return self.youtube_connection_status()

    def youtube_preparation_status(self, recording_id: str) -> YouTubePreparationStatus:
        self.get_history(recording_id)
        items = [
            item for item in UploadQueueStore(self.paths).list() if item.recording_id == recording_id
        ]
        completed = next((item for item in items if item.state is UploadQueueState.COMPLETED), None)
        if completed is not None:
            return YouTubePreparationStatus(
                "completed",
                "投稿用MP4は準備済みです。既存の準備結果を再利用します。",
                completed.queue_id,
            )
        active = next(
            (
                item
                for item in items
                if item.state in {UploadQueueState.WAITING, UploadQueueState.PROCESSING}
            ),
            None,
        )
        if active is not None:
            return YouTubePreparationStatus(
                active.state.value,
                "投稿用MP4準備は待機中または処理中です。投稿時に同じ準備キューを確認します。",
                active.queue_id,
            )
        failed = next((item for item in items if item.state is UploadQueueState.FAILED), None)
        if failed is not None:
            return YouTubePreparationStatus(
                "failed",
                f"前回の投稿用MP4準備に失敗しています。投稿時に再準備が必要です: {failed.error or '詳細なし'}",
                failed.queue_id,
            )
        return YouTubePreparationStatus(
            "not_prepared",
            "投稿用MP4は未準備です。投稿時に元録画を保持したまま自動で準備します。",
        )

    def disconnect_youtube(self) -> YouTubeConnectionStatus:
        try:
            self._youtube_credential_store.delete()
        except Exception as exc:
            raise ApplicationOperationError(f"YouTube連携を解除できません: {exc}") from exc
        return self.youtube_connection_status()

    def upload_history_to_youtube(
        self,
        *,
        recording_id: str,
        title: str,
        description: str = "",
        tags: tuple[str, ...] = (),
        privacy: str = "private",
        force_new_upload: bool = False,
    ) -> YouTubeUploadOutcome:
        self._require_data_management_idle()
        self.get_history(recording_id)
        try:
            metadata = UploadMetadata(
                title,
                description=description,
                tags=tags,
                privacy=UploadPrivacy(privacy),
            )
        except ValueError as exc:
            raise ApplicationOperationError(f"YouTube投稿メタデータが不正です: {exc}") from exc
        service = self._youtube_upload_service()
        outcome = service.upload_recording(
            recording_id=recording_id,
            metadata=metadata,
            force_new_upload=force_new_upload,
        )
        if outcome.upload.state.value != "completed":
            raise ApplicationOperationError(outcome.message)
        return outcome

    def get_youtube_upload_dialog_data(self, recording_id: str) -> YouTubeUploadDialogData:
        history = self.get_history(recording_id)
        record = self.get_duel_record(recording_id)
        upload = YouTubeUploadRepository.from_runtime_paths(self.paths).completed_for_recording(
            recording_id
        )
        if upload is not None:
            return YouTubeUploadDialogData(
                recording_id=recording_id,
                title=upload.metadata.title,
                description=upload.metadata.description,
                tags=upload.metadata.tags,
                privacy=upload.metadata.privacy.value,
                youtube_watch_url=upload.watch_url,
            )
        materials = YouTubeMaterialService(self.paths).generate(
            history=history,
            duel_record=record,
        )
        return YouTubeUploadDialogData(
            recording_id=recording_id,
            title=materials.title,
            description=materials.description,
            tags=materials.tags,
            privacy="private",
            youtube_watch_url=None,
        )

    def get_youtube_posting_template(self) -> YouTubePostingTemplate:
        return load_youtube_posting_template(self.paths.config)

    def save_youtube_posting_template(
        self,
        *,
        title: str,
        description: str,
        tags: str,
    ) -> YouTubePostingTemplate:
        return save_youtube_posting_template(
            self.paths.config,
            YouTubePostingTemplate(title=title, description=description, tags=tags),
        )

    def youtube_posting_template_aliases(self) -> tuple[tuple[str, str], ...]:
        return youtube_template_aliases()

    def get_duel_record(self, recording_id: str) -> DuelRecord | None:
        return DuelRecordRepository.from_runtime_paths(self.paths).get(recording_id)

    def get_duel_editor_data(self, identifier: str | None = None) -> DuelEditorData:
        record = self.get_duel_record(identifier) if identifier is not None else None
        catalog = DuelCatalogRepository.from_runtime_paths(self.paths)
        suggestion = (
            DuelWorkflowService.from_runtime_paths(self.paths).input_suggestion()
            if record is None
            else None
        )
        values = record.values if record is not None else suggestion.values
        return DuelEditorData(
            record=record,
            values=values,
            decks=catalog.list_decks(),
            tags=catalog.list_tags(include_deck_only=False),
            deck_tags=catalog.deck_tags_by_deck(),
            recording=self._duel_editor_recording_status(record, identifier),
            seasons=SeasonRepository.from_runtime_paths(self.paths).list(
                include_archived=True
            ),
            suggestion_reasons=suggestion.reasons if suggestion is not None else (),
        )

    def _duel_editor_recording_status(
        self, record: DuelRecord | None, identifier: str | None
    ) -> DuelEditorRecordingStatus:
        recording_id = record.recording_id if record is not None else identifier
        if not recording_id:
            return DuelEditorRecordingStatus(None, None, False, None, None)
        entry = RecordingHistoryRepository.from_runtime_paths(self.paths).get(
            recording_id
        )
        output_path = self.paths.recordings / entry.output_path if entry is not None else None
        upload = YouTubeUploadRepository.from_runtime_paths(self.paths).completed_for_recording(
            recording_id
        )
        return DuelEditorRecordingStatus(
            recording_id=recording_id,
            output_path=output_path,
            file_exists=output_path.is_file() if output_path is not None else False,
            youtube_watch_url=upload.watch_url if upload is not None else None,
            youtube_video_id=upload.video_id if upload is not None else None,
        )

    def get_duel_input_suggestion(
        self, *, occurred_on: date | None = None
    ) -> DuelInputSuggestion:
        return DuelWorkflowService.from_runtime_paths(self.paths).input_suggestion(
            occurred_on=occurred_on
        )

    def list_incomplete_duels(self) -> tuple[IncompleteDuel, ...]:
        return DuelWorkflowService.from_runtime_paths(self.paths).list_incomplete()

    def bulk_update_duel_records(
        self, duel_ids: tuple[str, ...], update: BulkDuelUpdate
    ) -> tuple[DuelRecord, ...]:
        self._require_duel_write_idle()
        saved = DuelWorkflowService.from_runtime_paths(self.paths).bulk_update(
            duel_ids, update
        )
        catalog = DuelCatalogRepository.from_runtime_paths(self.paths)
        for record in saved:
            catalog.remember_record_values(record.values)
        return saved

    def list_saved_duel_filters(self) -> tuple[SavedDuelFilter, ...]:
        return DuelWorkflowService.from_runtime_paths(self.paths).list_filters()

    def save_duel_filter(
        self,
        name: str,
        criteria: DuelFilterCriteria,
        *,
        filter_id: str | None = None,
    ) -> SavedDuelFilter:
        return DuelWorkflowService.from_runtime_paths(self.paths).save_filter(
            name, criteria, filter_id=filter_id
        )

    def delete_duel_filter(self, filter_id: str) -> SavedDuelFilter:
        return DuelWorkflowService.from_runtime_paths(self.paths).delete_filter(filter_id)

    def save_duel_record(
        self,
        recording_id: str,
        values: DuelRecordValues,
        *,
        expected_revision: int,
    ) -> DuelRecord:
        self._require_duel_write_idle()
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
        self._require_duel_write_idle()
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
        self._require_duel_write_idle()
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

    def delete_duel_record(self, duel_id: str) -> DuelRecord:
        self._require_duel_write_idle()
        return DuelRecordRepository.from_runtime_paths(self.paths).delete_manual(duel_id)

    def duel_write_block_reason(self) -> str | None:
        with self._lock:
            self._collect_manual_terminal_locked()
            snapshot = self._operation_state.snapshot
            if self.watch_active:
                return "自動監視中のため更新できません"
            if self._manual_starting or self._current is not None:
                return "録画中のため更新できません"
            if snapshot.allows(OperationAction.WRITE_DUEL):
                return None
            if snapshot.state in {
                OperationState.WATCH_STARTING,
                OperationState.WATCH_WAITING,
                OperationState.CANDIDATE_RECORDING,
                OperationState.AUTOMATIC_RECORDING,
            }:
                return "自動監視中のため更新できません"
            return "録画中のため更新できません"

    def _require_duel_write_idle(self) -> None:
        self._require_gui_startup_ready()
        reason = self.duel_write_block_reason()
        if reason is not None:
            raise ApplicationOperationError(reason)

    def active_season_summaries(
        self, *, today: date | None = None, limit: int = 2
    ) -> tuple[ActiveSeasonSummary, ...]:
        target = today or datetime.now().astimezone().date()
        active = [season for season in self.list_seasons() if season.contains(target)]
        active.sort(
            key=lambda season: (
                0 if season.season_type == "ranked" else 1,
                season.end_date,
                season.name.casefold(),
            )
        )
        return tuple(
            ActiveSeasonSummary(
                season,
                self.get_statistics_dashboard(StatisticsFilter(season_id=season.season_id)),
            )
            for season in active[:limit]
        )

    def list_duel_catalog(self) -> tuple[DuelCatalogEntry, ...]:
        return DuelCatalogRepository.from_runtime_paths(self.paths).list()

    def list_decks(self) -> tuple[DuelCatalogEntry, ...]:
        return DuelCatalogRepository.from_runtime_paths(self.paths).list_decks()

    def list_tags(self) -> tuple[DuelCatalogEntry, ...]:
        return DuelCatalogRepository.from_runtime_paths(self.paths).list_tags()

    def list_record_tags(self) -> tuple[DuelCatalogEntry, ...]:
        return DuelCatalogRepository.from_runtime_paths(self.paths).list_tags(
            include_deck_only=False
        )

    def list_deck_tags(self, deck_entry_id: int) -> tuple[DuelCatalogEntry, ...]:
        return DuelCatalogRepository.from_runtime_paths(self.paths).list_deck_tags(
            deck_entry_id
        )

    def set_deck_tags(
        self, deck_entry_id: int, tag_entry_ids: tuple[int, ...]
    ) -> tuple[DuelCatalogEntry, ...]:
        return DuelCatalogRepository.from_runtime_paths(self.paths).set_deck_tags(
            deck_entry_id, tag_entry_ids
        )

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
        repository = SeasonRepository.from_runtime_paths(self.paths)
        if repository.reference_count(season_id) > 0:
            return repository.delete(season_id)
        DataProtectionService(self.paths).create_backup("pre-season-delete")
        return repository.delete(season_id)

    def season_reference_count(self, season_id: int) -> int:
        return SeasonRepository.from_runtime_paths(self.paths).reference_count(season_id)

    def get_season_report(
        self,
        season_id: int,
        *,
        comparison_season_id: int | None = None,
        use_default_comparison: bool = True,
    ) -> SeasonReport:
        return SeasonReportService(self.paths).build(
            season_id,
            comparison_season_id=comparison_season_id,
            use_default_comparison=use_default_comparison,
        )

    def update_season_report(
        self,
        season_id: int,
        *,
        report_notes: str,
        report_goal: str,
        report_highlights: str,
        report_challenges: str,
        report_next_plan: str,
        expected_revision: int,
    ) -> Season:
        return SeasonRepository.from_runtime_paths(self.paths).update_report(
            season_id,
            report_notes=report_notes,
            report_goal=report_goal,
            report_highlights=report_highlights,
            report_challenges=report_challenges,
            report_next_plan=report_next_plan,
            expected_revision=expected_revision,
        )

    def archive_season_report(self, season_id: int) -> Season:
        DataProtectionService(self.paths).create_backup("pre-season-archive")
        return SeasonRepository.from_runtime_paths(self.paths).archive(season_id)

    def export_season_report(
        self,
        report: SeasonReport,
        destination: Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        return SeasonReportHtmlExporter().export(
            report, destination, overwrite=overwrite
        )

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
        deck_only: bool = False,
    ) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).add(
            kind,
            name,
            description=description,
            color=color,
            deck_only=deck_only,
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
        deck_only: bool = False,
    ) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).add_tag(
            name,
            description=description,
            color=color,
            deck_only=deck_only,
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
        deck_only: bool = False,
    ) -> DuelCatalogEntry:
        return DuelCatalogRepository.from_runtime_paths(self.paths).update_tag(
            entry_id,
            name=name,
            description=description,
            color=color,
            deck_only=deck_only,
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

    def get_review_view_model(self, recording_id: str) -> ReviewViewModel:
        history = self.get_history(recording_id)
        reference = self.resolve_recording(recording_id)
        upload = YouTubeUploadRepository.from_runtime_paths(self.paths).completed_for_recording(
            recording_id
        )
        from .recording_retention import safe_file_path
        from .video_lifecycle import VideoLifecycleRepository

        asset = VideoLifecycleRepository.from_runtime_paths(self.paths).get_current_asset(recording_id)
        stable = False
        if asset is not None and asset.state == "present" and asset.sha256:
            try:
                actual = safe_file_path(self.paths.recordings, asset.path).stat()
                stable = (actual.st_size, actual.st_mtime_ns) == (asset.size_bytes, asset.mtime_ns)
            except (RuntimeError, OSError, ValueError):
                pass
        observations = self.get_card_analysis_review(recording_id).observations
        if not stable:
            observations = tuple(replace(observation, video_verified=False) for observation in observations)
        return build_review_view_model(
            history=history,
            reference=reference,
            duel_record=self.get_duel_record(recording_id),
            timeline=self.list_timeline(recording_id),
            youtube_watch_url=upload.watch_url if upload is not None else None,
            card_observations=observations,
        )

    def verify_recording_video_sources(self, recording_id: str) -> int:
        """背景処理で現物を一度照合し、普通・カードマーカーへ同じ検証結果を渡します。"""
        from contextlib import closing
        from .history_database import connect_history_database
        from .recording_retention import fingerprint_file
        from .video_lifecycle import VideoLifecycleRepository, _object
        from .video_source_use import video_source_use

        lifecycle = VideoLifecycleRepository.from_runtime_paths(self.paths)
        asset = lifecycle.get_current_asset(recording_id)
        if asset is None:
            return 0
        with closing(connect_history_database(lifecycle.database_path)) as connection:
            rows = []
            for table in ("duel_event_sources", "card_observation_links", "card_analysis_job_sources"):
                rows.extend(connection.execute(
                    f"SELECT input_fingerprint_json FROM {table} WHERE video_asset_id=? AND video_generation=? AND time_basis=?",
                    (asset.asset_id, asset.generation, asset.time_basis),
                ).fetchall())
        known = False
        for row in rows:
            try:
                proof = _object(row[0], "動画の証拠")
            except RuntimeError:
                continue
            digest, size, inode = proof.get("sha256"), proof.get("size_bytes"), proof.get("inode")
            full = (isinstance(digest, str) and len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
                    and isinstance(size, int) and not isinstance(size, bool) and size > 0)
            capture = (proof.get("schema") == "mdrl-timeline-capture-v1"
                       and proof.get("verification_origin", "local_capture") == "local_capture"
                       and isinstance(inode, int) and not isinstance(inode, bool) and inode > 0)
            known = known or full or capture
        if not known:
            return 0
        with video_source_use(self.paths, recording_id, purpose="review_verification") as source:
            stat = source.path.stat()
            key = (source.asset.asset_id, source.asset.generation, str(source.path),
                   stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            with self._lock:
                cache = getattr(self, "_review_video_fingerprint_cache", None)
                if cache is None:
                    cache = self._review_video_fingerprint_cache = {}
                fingerprint = cache.get(key)
            if fingerprint is None:
                fingerprint = fingerprint_file(source.path)
                with self._lock:
                    # SHAの計算中はアプリ全体の操作ロックを保持しません。
                    self._review_video_fingerprint_cache = {key: fingerprint}
            events = DuelTimelineRepository.from_runtime_paths(self.paths).verify_video_sources(
                recording_id, fingerprint=fingerprint,
            )
            changed = self._get_card_repository().verify_video_sources(
                recording_id, CardInputFingerprint(str(source.path), fingerprint.size_bytes,
                                                   fingerprint.mtime_ns, fingerprint.sha256),
            )
        return sum(event.video_verified for event in events) + changed

    def add_review_marker(self, request: ReviewMarkerRequest) -> DuelEvent:
        return self.add_timeline_event(
            request.recording_id,
            elapsed_ms=request.elapsed_ms,
            event_type="marker",
            label=request.label,
        )

    def update_review_marker_label(self, event_id: str, label: str) -> DuelEvent:
        return DuelTimelineRepository.from_runtime_paths(self.paths).update_marker_label(
            event_id, label
        )

    def export_review_clip(self, request: ReviewClipExportRequest) -> ClipExportResult:
        self._require_data_management_idle()
        discovery = discover_ffmpeg(self.load_config().config.ffmpeg_path)
        if not discovery.found or discovery.executable is None:
            raise ApplicationOperationError("FFmpegが見つかりません")
        service = ClipExportService(
            paths=self.paths,
            repository=RecordingHistoryRepository.from_runtime_paths(self.paths),
            ffmpeg_executable=discovery.executable,
            validator=UploadMediaValidator(
                ffprobe_executable=find_ffprobe(discovery.executable)
            ),
        )
        return service.export_clip(
            recording_id=request.recording_id,
            center_seconds=request.center_seconds,
            before_seconds=request.before_seconds,
            after_seconds=request.after_seconds,
        )

    def check_history(self) -> tuple[ConsistencyIssue, ...]:
        return RecordingHistoryRepository.from_runtime_paths(
            self.paths
        ).check_consistency()

    def delete_history(self, recording_id: str, *, allow_protected: bool = False) -> HistoryDeletionResult:
        self._require_data_management_idle()
        if self.get_recording_video_state(recording_id).protected and not allow_protected:
            raise ApplicationOperationError("削除対象外の動画です。追加の削除確認が必要です")
        with self._lock:
            self._collect_manual_terminal_locked()
            if self._manual_starting or self._current is not None or self.watch_active:
                raise ApplicationOperationError(
                    "録画または自動監視の実行中は履歴を削除できません"
                )
        return RecordingHistoryRepository.from_runtime_paths(self.paths).delete(
            recording_id, allow_protected=allow_protected,
        )

    def resolve_recording(self, recording_id: str) -> RecordingReference:
        return self._browser().resolve(recording_id)

    def play_recording(self, recording_id: str) -> RecordingReference:
        return self._browser().play(recording_id)

    def reveal_recording(self, recording_id: str) -> RecordingReference:
        return self._browser().reveal(recording_id)

    def list_preparations(self) -> tuple[UploadQueueItem, ...]:
        return UploadQueueStore(self.paths).list()

    def list_preparation_candidates(self) -> tuple[PreparationCandidate, ...]:
        candidates: list[PreparationCandidate] = []
        for view in self.list_history_views(limit=1000):
            entry = view.entry
            if entry is None or entry.state != "completed" or view.recording_id is None:
                continue
            try:
                reference = self.resolve_recording(view.recording_id)
            except Exception:
                continue
            occurred = view.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M")
            deck = view.own_deck or "デッキ未設定"
            result = {
                "win": "勝ち",
                "loss": "負け",
                "draw": "引分",
            }.get(view.result, "勝敗未設定")
            candidates.append(
                PreparationCandidate(
                    view.recording_id,
                    f"{occurred} | {deck} | {result} | {reference.path.name}",
                    f"{occurred} {deck} {result}",
                )
            )
        return tuple(candidates)

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
            try:
                self._operation_state.require(OperationAction.CLOSE)
            except RuntimeError as exc:
                raise ApplicationOperationError(str(exc)) from exc
            self._transition_operation(OperationState.CLOSING, "終了処理中")
            self._closed = True
            self._interrupt_card_analysis()
        if self._card_manager is not None:
            self._card_manager.close()
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
            if self._operation_state.snapshot.state is not OperationState.IDLE:
                self._transition_operation(OperationState.IDLE, "終了しました")
            self._notifications.close()

    def _watch_loop(self, callback: EventCallback | None) -> None:
        controller: AutoRecordingController | None = None
        frame_stream: PersistentFfmpegRegionFrameCapture | None = None
        diagnostics: VisualDiagnosticSession | None = None
        confirmed_recording_id: str | None = None
        reported_restart_count = 0
        reserved_audio: ProcessLoopbackController | None = None
        reserved_audio_reported = False
        preroll_buffer: PrerollCaptureBuffer | None = None
        preroll_target_key: tuple[int, int] | None = None
        try:
            loaded = self.load_config()
            config = loaded.config
            with self._lock:
                watch_duel_defaults = self._watch_duel_defaults
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
            preroll_profile = RecordingProfile.from_config(watch_config)
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

            def stop_reserved_audio() -> None:
                nonlocal reserved_audio
                if reserved_audio is not None:
                    reserved_audio.stop()
                    reserved_audio = None

            def discard_frozen_preroll(frozen: FrozenPreroll | None) -> None:
                if frozen is None:
                    return
                for segment in frozen.segments:
                    try:
                        segment.unlink(missing_ok=True)
                    except OSError:
                        pass
                parents = {segment.parent for segment in frozen.segments}
                for parent in parents:
                    try:
                        parent.rmdir()
                    except OSError:
                        pass

            def stop_preroll_buffer() -> None:
                nonlocal preroll_buffer, preroll_target_key
                if preroll_buffer is not None:
                    preroll_buffer.discard()
                preroll_buffer = None
                preroll_target_key = None

            def ensure_preroll_buffer(observation: DuelObservation) -> None:
                nonlocal preroll_buffer, preroll_target_key
                if not watch_config.preroll_enabled or not watch_config.auto_start_recording:
                    return
                target_key = observation.capture_target_key
                if target_key is None:
                    stop_preroll_buffer()
                    return
                if preroll_buffer is not None:
                    if preroll_target_key == target_key and preroll_buffer.active:
                        preroll_buffer.poll()
                        return
                    stop_preroll_buffer()
                try:
                    preroll_buffer = new_preroll_buffer(
                        paths=self.paths,
                        executable=discovery.executable,
                        profile=preroll_profile,
                        capture_input=_automatic_capture_input(observation),
                        seconds=watch_config.preroll_seconds,
                        max_megabytes=watch_config.preroll_max_megabytes,
                    )
                    preroll_buffer.start()
                    preroll_target_key = target_key
                    self._emit(
                        callback,
                        ApplicationEvent(
                            "preroll",
                            "プリロール用の短い一時バッファを開始しました",
                            state="ready",
                        ),
                    )
                except Exception as exc:
                    preroll_buffer = None
                    preroll_target_key = None
                    self._emit(
                        callback,
                        ApplicationEvent(
                            "preroll",
                            f"プリロール一時バッファを開始できません: {exc}。通常の自動録画を継続します",
                            state="degraded",
                        ),
                    )

            def reserve_process_audio(observation: DuelObservation) -> None:
                nonlocal reserved_audio, reserved_audio_reported
                if watch_config.audio_mode != "process":
                    return
                pid = observation.capture_process_id
                if pid is None:
                    stop_reserved_audio()
                    return
                if reserved_audio is not None:
                    if reserved_audio.process_id == pid and reserved_audio.poll() is None:
                        return
                    stop_reserved_audio()
                capability = process_loopback_capability()
                if not capability.supported or capability.helper_path is None:
                    return
                pipe_name = new_audio_pipe_name(f"watch-{pid}")
                candidate = ProcessLoopbackController(
                    helper_path=capability.helper_path,
                    process_id=pid,
                    pipe_name=pipe_name,
                )
                try:
                    candidate.start()
                except ProcessLoopbackError as exc:
                    candidate.stop()
                    if not reserved_audio_reported:
                        reserved_audio_reported = True
                        self._emit(
                            callback,
                            ApplicationEvent(
                                "audio",
                                f"単体音声を事前待機できません: {exc}。映像のみ継続します",
                                state="degraded",
                            ),
                        )
                    return
                reserved_audio = candidate
                reserved_audio_reported = False
                self._emit(
                    callback,
                    ApplicationEvent(
                        "audio",
                        "Master Duel単体音声を事前待機しています",
                        state="ready",
                    ),
                )

            def prepare_automatic_recording(
                observation: DuelObservation,
            ) -> PreparedRecording:
                nonlocal reserved_audio, preroll_buffer, preroll_target_key
                self._card_recording_starting = True
                try:
                    self._interrupt_card_analysis()
                    reservation = reserved_audio
                    frozen_preroll: FrozenPreroll | None = None
                    if (
                        watch_config.preroll_enabled
                        and preroll_buffer is not None
                        and preroll_target_key == observation.capture_target_key
                    ):
                        frozen_preroll = preroll_buffer.freeze()
                        preroll_buffer = None
                        preroll_target_key = None
                    try:
                        prepared = prepare_recording(
                            paths=self.paths,
                            config=watch_config,
                            capture_input=_automatic_capture_input(observation),
                            visual_frame_capture=capture_current_window,
                            visual_analysis_callback=record_analysis,
                            visual_source=frame_stream.source_description,
                            visual_restart_counter=lambda: frame_stream.restart_count,
                            visual_frame_generation=lambda: frame_stream.generation,
                            audio_process_id=observation.capture_process_id,
                            reserved_process_audio=reservation,
                            frozen_preroll=frozen_preroll,
                            auto_watch_duel_defaults=watch_duel_defaults,
                        )
                    except Exception:
                        discard_frozen_preroll(frozen_preroll)
                        if reservation is not None:
                            reservation.stop()
                            reserved_audio = None
                        raise
                    if reservation is not None:
                        reserved_audio = None
                    return prepared
                finally:
                    self._card_recording_starting = False

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
                recording_factory=prepare_automatic_recording,
            )
            self._emit(
                callback,
                ApplicationEvent("watch", "自動監視を開始しました", state="watching"),
            )
            self._transition_operation(OperationState.WATCH_WAITING, "対戦を待機しています")
            while not self._watch_stop.is_set():
                iteration_started = time.monotonic()
                observation = (
                    start_monitor.observe()
                    if controller.current is None and watch_config.auto_start_recording
                    else window_detector.observe()
                )
                if self._watch_stop.is_set():
                    break
                if controller.current is None:
                    ensure_preroll_buffer(observation)
                    reserve_process_audio(observation)
                event = controller.process(observation)
                if event.action is AutoRecordingEventAction.STARTED:
                    self._transition_operation(
                        OperationState.CANDIDATE_RECORDING, "対戦候補を録画しています"
                    )
                    diagnostics.transition("candidate_started", elapsed_ms=0)
                    self._save_automatic_start_candidate(
                        event.recording_id,
                        start_monitor.start_candidate,
                        callback,
                        elapsed_ms=event.preroll_offset_ms,
                    )
                    self._notify(
                        "candidate_started",
                        "対戦候補の録画を開始しました",
                        f"{event.recording_id}:candidate",
                    )
                lifecycle_prepared = controller.current
                lifecycle_event = self._apply_automatic_visual_lifecycle(
                    controller,
                    start_monitor,
                    callback,
                )
                if lifecycle_event is not None:
                    transition = "result_stopped"
                    boundary_candidate = None
                    if lifecycle_prepared is not None:
                        if (
                            lifecycle_prepared.visual_abort_reason is not None
                            or not lifecycle_prepared.duel_confirmed
                        ):
                            transition = "candidate_discarded"
                        elif lifecycle_prepared.boundary_detected_monotonic is not None:
                            transition = "boundary_stopped"
                            boundary_candidate = lifecycle_prepared.boundary_candidate
                    diagnostics.transition(
                        transition,
                        elapsed_ms=(
                            boundary_candidate.elapsed_ms
                            if boundary_candidate is not None
                            else None
                        ),
                        details=(
                            _boundary_diagnostic_details(lifecycle_prepared, boundary_candidate)
                            if lifecycle_prepared is not None
                            and boundary_candidate is not None
                            else None
                        ),
                    )
                    event = lifecycle_event
                    if (
                        transition == "boundary_stopped"
                        and lifecycle_event.action is AutoRecordingEventAction.STOPPED
                        and boundary_candidate is not None
                    ):
                        self._emit(callback, _application_event(lifecycle_event))
                        handoff_started = time.monotonic()
                        event = controller.start_from_boundary(observation, boundary_candidate)
                        if event.action is AutoRecordingEventAction.STARTED:
                            confirmed_recording_id = None
                            self._transition_operation(
                                OperationState.CANDIDATE_RECORDING,
                                "次の対戦候補を録画しています",
                            )
                            diagnostics.transition(
                                "boundary_handoff_started",
                                elapsed_ms=0,
                                details={
                                    "source_elapsed_ms": boundary_candidate.elapsed_ms,
                                    "handoff_ms": round(
                                        max(0.0, time.monotonic() - handoff_started) * 1000
                                    ),
                                    "confidence": round(boundary_candidate.confidence, 4),
                                    "evidence": boundary_candidate.evidence,
                                },
                            )
                            self._save_automatic_start_candidate(
                                event.recording_id,
                                boundary_candidate,
                                callback,
                                elapsed_ms=event.preroll_offset_ms,
                            )
                            self._notify(
                                "boundary_handoff_started",
                                "次の対戦へ録画を引き継ぎました",
                                f"{event.recording_id}:boundary-handoff",
                            )
                        else:
                            confirmed_recording_id = None
                            self._transition_operation(
                                OperationState.WATCH_WAITING,
                                "次の対戦録画を開始できませんでした",
                            )
                if controller.current is not None:
                    self._publish_automatic_snapshot(controller.current)
                    if (
                        controller.current.duel_confirmed
                        and controller.current.target.recording_id
                        != confirmed_recording_id
                    ):
                        confirmed_recording_id = controller.current.target.recording_id
                        self._transition_operation(
                            OperationState.AUTOMATIC_RECORDING, "対戦を録画しています"
                        )
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
                        self._notify(
                            "recording_confirmed",
                            "対戦を検出し録画しています",
                            f"{confirmed_recording_id}:confirmed",
                        )
                    self._publish_visual_status(controller.current, callback)
                else:
                    self._clear_automatic_snapshot()
                    if event.action is AutoRecordingEventAction.STOPPED:
                        if lifecycle_event is None:
                            diagnostics.transition("recording_stopped")
                        start_monitor.reset()
                        confirmed_recording_id = None
                        self._transition_operation(
                            OperationState.WATCH_WAITING, "次の対戦を待機しています"
                        )
                        self._notify(
                            "recording_stopped",
                            "対戦録画を停止し、次の対戦を待機します",
                            f"{event.recording_id}:stopped",
                        )
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
                self._watch_stop.wait(
                    _remaining_poll_delay(
                        interval,
                        iteration_started,
                        time.monotonic(),
                    )
                )
        except Exception as exc:
            self._transition_operation(OperationState.FAILED, str(exc))
            self._notify("watch_failed", str(exc), f"watch:failed:{type(exc).__name__}")
            self._emit(callback, ApplicationEvent("error", str(exc), state="failed"))
        finally:
            if controller is not None and controller.current is not None:
                if diagnostics is not None:
                    diagnostics.transition("watch_stopped_with_active_recording")
                event = controller.manual_stop()
                self._emit(callback, _application_event(event))
            if reserved_audio is not None:
                reserved_audio.stop()
            if preroll_buffer is not None:
                preroll_buffer.discard()
            self._clear_automatic_snapshot()
            if frame_stream is not None:
                frame_stream.stop()
            if diagnostics is not None:
                diagnostics.close()
            self._emit(
                callback,
                ApplicationEvent("watch", "自動監視を停止しました", state="stopped"),
            )
            state = self._operation_state.snapshot.state
            if state in {
                OperationState.WATCH_STARTING,
                OperationState.WATCH_WAITING,
                OperationState.CANDIDATE_RECORDING,
                OperationState.AUTOMATIC_RECORDING,
            }:
                self._transition_operation(OperationState.STOPPING, "自動監視を停止しています")
                state = self._operation_state.snapshot.state
            if state in {
                OperationState.STOPPING,
            }:
                self._transition_operation(OperationState.IDLE, "待機中")

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
        ) or boundary_detected is not None
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
        *,
        elapsed_ms: int = 0,
    ) -> None:
        if recording_id is None or candidate is None:
            return
        try:
            DuelTimelineRepository.from_runtime_paths(self.paths).add(
                recording_id,
                elapsed_ms=max(0, elapsed_ms),
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
        recording_id = self._current.target.recording_id
        self._current.release()
        self._current = None
        self._recording_history_revision += 1
        current = self._operation_state.snapshot.state
        if state is RecordingState.FAILED:
            if current in {
                OperationState.MANUAL_STARTING,
                OperationState.MANUAL_RECORDING,
                OperationState.STOPPING,
            }:
                self._transition_operation(OperationState.FAILED, "録画に失敗しました")
        elif current is OperationState.MANUAL_RECORDING:
            self._transition_operation(OperationState.STOPPING, "録画を終了しています")
            self._transition_operation(OperationState.IDLE, "待機中")
        if state is RecordingState.COMPLETED:
            self._enqueue_completed_card_analysis(recording_id)

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

    def _publish_automatic_snapshot(self, prepared: PreparedRecording) -> None:
        session = prepared.session
        snapshot = RecordingSnapshot(
            session.state is RecordingState.RECORDING,
            session.state,
            prepared.target.recording_id,
            prepared.target.path,
            session.started_at,
            _elapsed(session.started_at),
            session.result,
        )
        with self._lock:
            self._automatic_snapshot = snapshot

    def _clear_automatic_snapshot(self) -> None:
        with self._lock:
            self._automatic_snapshot = None

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

    def _youtube_upload_service(self) -> YouTubeUploadService:
        from .post_upload_cleanup import PostUploadCleanupHandler

        return YouTubeUploadService(
            paths=self.paths,
            upload_repository=YouTubeUploadRepository.from_runtime_paths(self.paths),
            queue=UploadQueueStore(self.paths),
            credential_store=self._youtube_credential_store,
            youtube_client=self._youtube_client,
            preparation_service=self._upload_preparation_service(),
            post_upload_cleanup=PostUploadCleanupHandler(self.paths),
        )

    def _emit(self, callback: EventCallback | None, event: ApplicationEvent) -> None:
        if event.recording_id and event.kind in {"stopped", "error"}:
            with self._lock:
                self._recording_history_revision += 1
        if event.recording_id and event.kind == "stopped" and event.state == "completed":
            self._enqueue_completed_card_analysis(event.recording_id)
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


def _boundary_diagnostic_details(
    prepared: PreparedRecording,
    candidate: DetectionCandidate,
) -> dict[str, object]:
    status = prepared.visual_detection_status
    return {
        "confidence": round(candidate.confidence, 4),
        "evidence": candidate.evidence,
        "profile": status.profile,
        "resolution": status.resolution,
        "agreement": status.agreement,
        "scores": {
            "coin": round(status.coin_score, 4),
            "board": round(status.board_score, 4),
            "turn": round(status.turn_score, 4),
            "result": round(status.result_score, 4),
            "error": round(status.error_score, 4),
            "replay": round(status.replay_score, 4),
            "overlay": round(status.overlay_score, 4),
        },
    }


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


def _remaining_poll_delay(
    interval_seconds: float,
    iteration_started: float,
    iteration_finished: float,
) -> float:
    return max(0.0, interval_seconds - max(0.0, iteration_finished - iteration_started))
