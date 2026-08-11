from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

from . import __version__
from .application import ApplicationEvent, RecorderApplicationService
from .capture_targets import CaptureTargetCatalog
from .config import AppConfig, AppConfigError, LoadedAppConfig, load_app_config, save_app_config
from .config_management import ConfigValueError, config_value, config_values, updated_config
from .duel_records import (
    DUEL_TYPES,
    PLAY_ORDERS,
    RESULTS,
    DuelRecordConflictError,
    DuelRecordError,
    DuelRecordRepository,
    DuelRecordValues,
)
from .duel_timeline import (
    ACTORS,
    EVENT_TYPES,
    OUTCOMES,
    STATUSES,
    DuelEvent,
    DuelTimelineError,
    DuelTimelineRepository,
)
from .ffmpeg import discover_ffmpeg, enumerate_windows_inputs
from .game_window import GameWindowMonitor, GameWindowObservation, GameWindowStatus
from .media_recovery import InspectionStatus, MediaRecoveryError, MediaRecoveryService
from .operational_status import collect_operational_status
from .preflight import CheckStatus, PreflightReport, run_preflight
from .recording_history import (
    HISTORY_STATES,
    ConsistencyIssueKind,
    HistoryQuery,
    RecordingHistoryEntry,
    RecordingHistoryError,
    RecordingHistoryRepository,
)
from .recording_browsing import RecordingBrowseError, RecordingBrowseFailure, RecordingBrowser
from .recorder import (
    PreparedRecording,
    RecordingPreparationError,
    RecordingTrackingError,
    prepare_recording,
)
from .recording_session import RecordingResult, RecordingState
from .recovery import InterruptedDetectionKind, RecoveryError, RecoveryManager
from .runtime_paths import (
    RuntimePathError,
    RuntimePaths,
    default_runtime_paths,
    ensure_runtime_dirs,
)
from .upload_export import UploadExporter
from .upload_manifest import UploadManifestWriter
from .upload_media import UploadMediaValidator, find_ffprobe
from .upload_metadata import UploadMetadata, UploadMetadataError, UploadPrivacy
from .upload_preparation import UploadPreparationError, UploadPreparationService
from .upload_queue import UploadQueueError, UploadQueueItem, UploadQueueState, UploadQueueStore


EXIT_SUCCESS = 0
EXIT_CONFIGURATION = 2
EXIT_OPERATION = 3
EXIT_ATTENTION = 4
EXIT_INTERRUPTED = 130


def configure_standard_streams() -> None:
    """地域設定に依存せずCLIのUnicode出力を扱えるようにします。"""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


class CliArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        _print_cli_error(
            "E_ARGUMENT",
            _argument_error_message(message),
            f"{self.prog} --helpで引数を確認してください。",
        )
        self.exit(EXIT_CONFIGURATION)


def _argument_error_message(message: str) -> str:
    replacements = {
        "the following arguments are required:": "必須引数がありません:",
        "unrecognized arguments:": "未対応の引数です:",
        "invalid choice:": "未対応の値です:",
    }
    translated = message
    for source, target in replacements.items():
        translated = translated.replace(source, target)
    return translated


def build_parser() -> argparse.ArgumentParser:
    parser = CliArgumentParser(
        prog="mdrl",
        description="Master Duelの録画、履歴、復旧、アップロード準備を管理します。",
        epilog="安全確認: resetと修復はhelpで影響を確認してから実行してください。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--init-user-data", action="store_true", help="user_data フォルダを作成します。")
    parser.add_argument("--write-default-config", action="store_true", help="既定の app.toml を作成します。")
    parser.add_argument("--show-config", action="store_true", help="現在の設定読み込み結果を表示します。")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="user_data を作成する基準フォルダです。EXEではLocalAppDataが既定です。",
    )
    parser.add_argument("--user-data-dir", type=Path, default=None, help="user_data の場所を直接指定します。")
    parser.add_argument("--verbose", action="store_true", help="失敗時に内部診断を追加表示します。")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "doctor",
        help="録画環境を検査します。",
        description="設定、FFmpeg、入力、保存先、空き容量を検査します。",
        epilog="例: mdrl doctor\n終了コード2は録画環境の修正が必要です。",
    )
    status_parser = subparsers.add_parser(
        "status",
        help="全中核サブシステムの状態を診断します。",
        description="既存データを削除・上書きせず、録画環境、実行状態、履歴、復旧、準備キューを診断します。",
        epilog="例: mdrl status --json\nJSONには秘密情報と実行時データの絶対パスを含めません。",
    )
    status_parser.add_argument("--json", action="store_true", help="機械可読JSONで表示します。")
    subparsers.add_parser("list-inputs", help="Windowsの画面・音声入力候補を表示します。")
    targets_parser = subparsers.add_parser(
        "targets",
        help="録画可能なデスクトップ、モニター、ウィンドウを表示します。",
    )
    targets_parser.add_argument("--json", action="store_true", help="機械可読JSONで表示します。")
    config_parser = subparsers.add_parser(
        "config",
        help="非シークレット設定を安全に管理します。",
        description="app.tomlを検証し、既存内容を退避して原子的に更新します。",
        epilog="例: mdrl config set recorder.frame_rate 60\nOAuthトークンやAPIキーは扱いません。",
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_subparsers.add_parser("init", help="設定がない場合だけ既定設定を作成します。")
    config_show = config_subparsers.add_parser("show", help="設定可能な全項目を表示します。")
    config_show.add_argument("--json", action="store_true", help="機械可読JSONで表示します。")
    config_get = config_subparsers.add_parser("get", help="指定した設定値を表示します。")
    config_get.add_argument("key", metavar="KEY")
    config_get.add_argument("--json", action="store_true", help="機械可読JSONで表示します。")
    config_set = config_subparsers.add_parser("set", help="検証後に設定値を原子的に変更します。")
    config_set.add_argument("key", metavar="KEY")
    config_set.add_argument("value", metavar="VALUE")
    config_reset = config_subparsers.add_parser("reset", help="既存設定を退避して既定値へ戻します。")
    config_reset.add_argument(
        "--yes",
        action="store_true",
        help="設定を既定値へ戻すことを明示的に確認します。",
    )
    record_parser = subparsers.add_parser(
        "record",
        help="画面と設定済み音声を録画します。",
        description="preflight成功後に録画を開始し、録画IDを履歴へ保存します。",
        epilog="例: mdrl record --duration 30\nCtrl+Cでも正常停止を試み、部分ファイルは削除しません。",
    )
    record_parser.add_argument(
        "--duration",
        type=_positive_seconds,
        default=None,
        metavar="SECONDS",
        help="指定秒数後に正常停止します。省略時はCtrl+Cまで録画します。",
    )
    watch_parser = subparsers.add_parser("watch", help="Master Duelウィンドウに連動して録画を補助します。")
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="現在のゲーム状態だけを表示し、録画や継続監視を行いません。",
    )
    history_parser = subparsers.add_parser(
        "history",
        help="録画履歴を表示・診断します。",
        description="録画IDを共通識別子として履歴を参照します。",
        epilog="例: mdrl history show RECORDING_ID\nhistory checkはファイルを変更しません。",
    )
    history_subparsers = history_parser.add_subparsers(dest="history_command", required=True)
    history_list = history_subparsers.add_parser("list", help="録画履歴を新しい順に一覧します。")
    history_list.add_argument("--state", choices=sorted(HISTORY_STATES), default=None)
    history_list.add_argument("--since", type=_iso_datetime, default=None, metavar="DATETIME")
    history_list.add_argument("--until", type=_iso_datetime, default=None, metavar="DATETIME")
    history_list.add_argument("--limit", type=_history_limit, default=50)
    history_list.add_argument("--offset", type=_nonnegative_integer, default=0)
    history_show = history_subparsers.add_parser("show", help="録画履歴1件の詳細を表示します。")
    history_show.add_argument("recording_id")
    history_play = history_subparsers.add_parser(
        "play",
        help="録画をWindows既定プレイヤーで再生します。",
    )
    history_play.add_argument("recording_id")
    history_reveal = history_subparsers.add_parser(
        "reveal",
        help="録画ファイルを選択してExplorerで表示します。",
    )
    history_reveal.add_argument("recording_id")
    history_subparsers.add_parser("check", help="履歴と録画ファイルの不整合を診断します。")
    duel_parser = subparsers.add_parser(
        "duel",
        help="録画に関連付けた対戦記録を管理します。",
        description="勝敗、先後、デッキ、対戦種別、タグ、メモを後から編集します。",
    )
    duel_subparsers = duel_parser.add_subparsers(dest="duel_command", required=True)
    duel_show = duel_subparsers.add_parser("show", help="対戦記録1件を表示します。")
    duel_show.add_argument("recording_id")
    duel_show.add_argument("--json", action="store_true")
    duel_set = duel_subparsers.add_parser("set", help="対戦記録を作成または更新します。")
    duel_set.add_argument("recording_id")
    duel_set.add_argument("--revision", type=_nonnegative_integer, required=True)
    duel_set.add_argument("--result", choices=sorted(RESULTS), default=None)
    duel_set.add_argument("--play-order", choices=sorted(PLAY_ORDERS), default=None)
    duel_set.add_argument("--own-deck", default=None)
    duel_set.add_argument("--opponent-deck", default=None)
    duel_set.add_argument("--duel-type", choices=sorted(DUEL_TYPES), default=None)
    duel_set.add_argument("--tag", action="append", default=None)
    duel_set.add_argument("--notes", default=None)
    duel_set.add_argument("--json", action="store_true")
    duel_confirm = duel_subparsers.add_parser("confirm", help="対戦記録を確認済みにします。")
    duel_confirm.add_argument("recording_id")
    duel_confirm.add_argument("--revision", type=_nonnegative_integer, required=True)
    duel_confirm.add_argument("--json", action="store_true")
    duel_history = duel_subparsers.add_parser("history", help="対戦記録の変更履歴を表示します。")
    duel_history.add_argument("recording_id")
    duel_history.add_argument("--json", action="store_true")
    timeline_parser = subparsers.add_parser(
        "timeline",
        help="録画中の対戦イベントを管理します。",
        description="対戦開始、ターン切り替え、勝敗、手動マーカーを録画時刻へ関連付けます。",
    )
    timeline_subparsers = timeline_parser.add_subparsers(dest="timeline_command", required=True)
    timeline_list = timeline_subparsers.add_parser("list", help="イベントを時刻順に表示します。")
    timeline_list.add_argument("recording_id")
    timeline_list.add_argument("--status", choices=sorted(STATUSES), default=None)
    timeline_list.add_argument("--type", choices=sorted(EVENT_TYPES), default=None)
    timeline_list.add_argument("--json", action="store_true")
    timeline_add = timeline_subparsers.add_parser("add", help="手動イベントを追加します。")
    timeline_add.add_argument("recording_id")
    timeline_add.add_argument("--elapsed-ms", type=_nonnegative_integer, required=True)
    timeline_add.add_argument("--type", choices=sorted(EVENT_TYPES), required=True)
    timeline_add.add_argument("--actor", choices=sorted(ACTORS), default=None)
    timeline_add.add_argument("--outcome", choices=sorted(OUTCOMES), default=None)
    timeline_add.add_argument("--label", default="")
    timeline_add.add_argument("--json", action="store_true")
    timeline_confirm = timeline_subparsers.add_parser("confirm", help="候補イベントを確認します。")
    timeline_confirm.add_argument("event_id")
    timeline_confirm.add_argument("--json", action="store_true")
    timeline_reject = timeline_subparsers.add_parser("reject", help="候補イベントを却下します。")
    timeline_reject.add_argument("event_id")
    timeline_reject.add_argument("--json", action="store_true")
    recovery_parser = subparsers.add_parser(
        "recovery",
        help="中断録画を検出・検査・修復します。",
        description="録画IDを指定し、元録画を保持したまま検査・別ファイル修復します。",
        epilog="例: mdrl recovery repair RECORDING_ID --dry-run\nrepairは元録画を上書きしません。",
    )
    recovery_subparsers = recovery_parser.add_subparsers(dest="recovery_command", required=True)
    recovery_subparsers.add_parser("list", help="復旧対象を一覧します。")
    recovery_subparsers.add_parser("detect", help="中断された録画を検出します。")
    recovery_inspect = recovery_subparsers.add_parser("inspect", help="元録画を変更せず検査します。")
    recovery_inspect.add_argument("recording_id")
    recovery_repair = recovery_subparsers.add_parser("repair", help="別ファイルへ修復します。")
    recovery_repair.add_argument("recording_id")
    recovery_repair.add_argument("--dry-run", action="store_true", help="予定だけを表示します。")
    recovery_ignore = recovery_subparsers.add_parser("ignore", help="復旧対象を手動で無視します。")
    recovery_ignore.add_argument("recording_id")
    prepare_parser = subparsers.add_parser(
        "prepare",
        help="アップロード用動画と情報を準備します。",
        description="完了済み録画IDをキューへ登録し、再実行可能なMP4準備を行います。",
        epilog="例: mdrl prepare enqueue RECORDING_ID --title 対戦記録\n直接アップロードとOAuthは行いません。",
    )
    prepare_subparsers = prepare_parser.add_subparsers(dest="prepare_command", required=True)
    prepare_enqueue = prepare_subparsers.add_parser("enqueue", help="録画を準備キューへ追加します。")
    prepare_enqueue.add_argument("recording_id")
    prepare_enqueue.add_argument("--title", required=True)
    prepare_enqueue.add_argument("--description", default="")
    prepare_enqueue.add_argument("--tag", action="append", default=[])
    prepare_enqueue.add_argument("--privacy", choices=("private", "unlisted"), default=None)
    prepare_subparsers.add_parser("list", help="準備キューを一覧します。")
    prepare_show = prepare_subparsers.add_parser("show", help="準備キュー1件を表示します。")
    prepare_show.add_argument("queue_id")
    prepare_run = prepare_subparsers.add_parser("run", help="待機中の準備処理を実行します。")
    prepare_run.add_argument("queue_id", nargs="?", default=None)
    prepare_cancel = prepare_subparsers.add_parser("cancel", help="待機中の準備処理を取消します。")
    prepare_cancel.add_argument("queue_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    configure_standard_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = default_runtime_paths(project_root=args.project_root, user_data_dir=args.user_data_dir)

    operational_commands = {"record", "watch", "history", "duel", "timeline", "recovery", "prepare"}
    skip_automatic_detection = (
        args.command == "recovery" and getattr(args, "recovery_command", None) == "detect"
    )
    if args.command in operational_commands and not skip_automatic_detection:
        if not _detect_interrupted_on_startup(paths):
            return 3

    if args.command == "doctor":
        loaded = _load_config_or_report(args.project_root, args.user_data_dir)
        if loaded is None:
            return 2
        report = run_preflight(paths=paths, config=loaded.config, config_loaded=loaded.config_loaded)
        _print_preflight_report(report)
        return report.exit_code

    if args.command == "status":
        loaded = _load_config_or_report(args.project_root, args.user_data_dir)
        if loaded is None:
            return 2
        status = collect_operational_status(paths=paths, loaded=loaded)
        if args.json:
            print(json.dumps(status.document, ensure_ascii=False, sort_keys=True))
        else:
            _print_operational_status(status.document)
        return status.exit_code

    if args.command == "list-inputs":
        loaded = _load_config_or_report(args.project_root, args.user_data_dir)
        if loaded is None:
            return 2
        discovery = discover_ffmpeg(loaded.config.ffmpeg_path)
        if not discovery.found:
            _print_cli_error("E_FFMPEG_NOT_FOUND", "FFmpegが見つかりません。", "doctorで詳細を確認してください。")
            return 2
        assert discovery.executable is not None
        inputs = enumerate_windows_inputs(discovery.executable)
        for item in inputs.inputs:
            print(f"{item.kind}: {item.display_name} [{item.input_format}:{item.identifier}]")
        for warning in inputs.warnings:
            print(f"[WARN] {warning}")
        for error in inputs.errors:
            _print_cli_error("E_INPUT_ENUMERATION", error, "doctorでFFmpegと入力設定を確認してください。")
        return 0 if inputs.succeeded else 2

    if args.command == "targets":
        loaded = _load_config_or_report(args.project_root, args.user_data_dir)
        if loaded is None:
            return 2
        try:
            monitor = GameWindowMonitor(
                process_name=loaded.config.game_process_name,
                title_contains=loaded.config.game_window_title_contains,
            )
            targets = CaptureTargetCatalog().list_targets(master_duel_monitor=monitor)
        except (OSError, RuntimeError, ValueError) as exc:
            _print_cli_error(
                "E_CAPTURE_TARGETS",
                str(exc),
                "Windowsの画面構成と実行権限を確認してください。",
            )
            return 2
        if args.json:
            document = [
                {
                    "mode": target.mode.value,
                    "id": target.identifier,
                    "label": target.label,
                    "available": target.available,
                    "detail": target.detail,
                }
                for target in targets
            ]
            print(json.dumps(document, ensure_ascii=False, sort_keys=True))
        else:
            for target in targets:
                selected = target.mode.value == loaded.config.capture_mode and (
                    not loaded.config.capture_target_id
                    or target.identifier == loaded.config.capture_target_id
                )
                marker = "*" if selected else " "
                state = "利用可" if target.available else "利用不可"
                print(
                    f"{marker} [{target.mode.value}] {target.identifier} | "
                    f"{state} | {target.label} | {target.detail}"
                )
        return 0

    if args.command == "config":
        return _run_config_command(paths=paths, args=args)

    if args.command == "record":
        return _run_record_command(
            project_root=args.project_root,
            user_data_dir=args.user_data_dir,
            duration_seconds=args.duration,
            verbose=args.verbose,
        )

    if args.command == "watch":
        return _run_watch_command(
            project_root=args.project_root,
            user_data_dir=args.user_data_dir,
            once=args.once,
        )

    if args.command == "history":
        return _run_history_command(paths=paths, args=args)

    if args.command == "duel":
        return _run_duel_command(paths=paths, args=args)

    if args.command == "timeline":
        return _run_timeline_command(paths=paths, args=args)

    if args.command == "recovery":
        return _run_recovery_command(
            paths=paths,
            args=args,
            project_root=args.project_root,
            user_data_dir=args.user_data_dir,
        )

    if args.command == "prepare":
        return _run_prepare_command(
            paths=paths,
            args=args,
            project_root=args.project_root,
            user_data_dir=args.user_data_dir,
        )

    if args.init_user_data:
        ensure_runtime_dirs(paths)
        print(f"user_data を作成しました: {paths.root}")

    if args.write_default_config:
        ensure_runtime_dirs(paths)
        try:
            config_path = save_app_config(paths=paths, config=AppConfig(), overwrite=False)
        except AppConfigError as exc:
            _print_cli_error("E_CONFIG_EXISTS", str(exc), "config showで現在値を確認してください。")
            return EXIT_ATTENTION
        print(f"既定設定を書き込みました: {config_path}")

    if args.show_config:
        loaded = load_app_config(project_root=args.project_root, user_data_dir=args.user_data_dir)
        print(f"config path: {loaded.config_path}")
        print(f"config loaded: {loaded.config_loaded}")
        print(f"ffmpeg path: {loaded.config.ffmpeg_path}")
        print(f"recording format: {loaded.config.recording_format}")
        print(f"screen input: {loaded.config.screen_input_format}:{loaded.config.screen_input}")
        print(f"audio input: {loaded.config.audio_input_format}:{loaded.config.audio_input or '(disabled)'}")
        print(f"video encoder: {loaded.config.video_encoder}")
        print(f"frame rate: {loaded.config.frame_rate}")
        resolution = (
            f"{loaded.config.capture_width}x{loaded.config.capture_height}"
            if loaded.config.capture_width and loaded.config.capture_height
            else "source"
        )
        print(f"capture resolution: {resolution}")
        print(f"video bitrate: {loaded.config.video_bitrate_kbps} kbps")
        print(f"audio bitrate: {loaded.config.audio_bitrate_kbps} kbps")
        print(f"game process: {loaded.config.game_process_name}")
        print(f"game window title contains: {loaded.config.game_window_title_contains or '(any)'}")
        print(f"auto start recording: {loaded.config.auto_start_recording}")
        print(f"auto stop recording: {loaded.config.auto_stop_recording}")
        print(f"start confirmations: {loaded.config.start_confirmations}")
        print(f"stop confirmations: {loaded.config.stop_confirmations}")
        print(f"detection confidence: {loaded.config.detection_minimum_confidence}")
        print(f"detection poll interval: {loaded.config.detection_poll_interval_seconds} seconds")
        print(f"detection cooldown: {loaded.config.detection_cooldown_seconds} seconds")
        print(f"upload privacy: {loaded.config.upload_privacy_status}")
        print(f"auto create user_data: {loaded.config.auto_create_user_data}")

    if args.init_user_data or args.write_default_config or args.show_config:
        return 0

    print("master-duel-recorder-lite")
    print(f"version: {__version__}")
    print(f"runtime data: {paths.root}")
    print("次の確認: python -m master_duel_recorder_lite --init-user-data --write-default-config --show-config")
    return 0


def _load_config_or_report(project_root: Path, user_data_dir: Path | None) -> LoadedAppConfig | None:
    try:
        return load_app_config(project_root=project_root, user_data_dir=user_data_dir)
    except AppConfigError as exc:
        _print_cli_error("E_CONFIG_READ", str(exc), "config showで設定内容を確認してください。")
        return None


def _print_cli_error(code: str, summary: str, action: str) -> None:
    print(f"[ERROR] {code}: {summary}", file=sys.stderr)
    print(f"対処: {action}", file=sys.stderr)


def _run_config_command(*, paths: RuntimePaths, args: argparse.Namespace) -> int:
    command = args.config_command
    if command == "init":
        try:
            ensure_runtime_dirs(paths)
            path = save_app_config(paths=paths, config=AppConfig(), overwrite=False)
        except AppConfigError as exc:
            _print_cli_error("E_CONFIG_EXISTS", str(exc), "config showで現在値を確認してください。")
            return 4
        except RuntimePathError as exc:
            _print_cli_error("E_RUNTIME_PATH", str(exc), "保存先の権限とパスを確認してください。")
            return 3
        print(f"既定設定を作成しました: {path}")
        return 0

    if command == "reset":
        if not args.yes:
            _print_cli_error(
                "E_CONFIRMATION",
                "設定の初期化には--yesが必要です。",
                "影響を確認し、実行する場合だけconfig reset --yesを指定してください。",
            )
            return 2
        try:
            path = save_app_config(paths=paths, config=AppConfig())
        except AppConfigError as exc:
            _print_cli_error("E_CONFIG_WRITE", str(exc), "保存先の権限と空き容量を確認してください。")
            return 3
        print(f"設定を既定値へ戻しました: {path}")
        print("直前の設定はapp.toml.previousへ保持しました。")
        return 0

    loaded = _load_config_or_report(paths.root.parent, paths.root)
    if loaded is None:
        return 2

    if command == "show":
        values = config_values(loaded.config)
        if args.json:
            print(
                json.dumps(
                    {"schema_version": 1, "loaded": loaded.config_loaded, "values": values},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        else:
            source = "app.toml" if loaded.config_loaded else "既定値（app.toml未作成）"
            print(f"設定元: {source}")
            for key, value in values.items():
                print(f"{key} = {json.dumps(value, ensure_ascii=False)}")
        return 0

    if command == "get":
        try:
            value = config_value(loaded.config, args.key)
        except ConfigValueError as exc:
            _print_cli_error("E_CONFIG_KEY", str(exc), "config showで利用可能なキーを確認してください。")
            return 2
        if args.json:
            print(json.dumps({"key": args.key, "value": value}, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(value, ensure_ascii=False))
        return 0

    if command == "set":
        try:
            candidate = updated_config(loaded.config, args.key, args.value)
            path = save_app_config(paths=paths, config=candidate)
        except ConfigValueError as exc:
            _print_cli_error("E_CONFIG_VALUE", str(exc), "値と制約をconfig --helpまたは設定文書で確認してください。")
            return 2
        except AppConfigError as exc:
            _print_cli_error("E_CONFIG_WRITE", str(exc), "保存先の権限と空き容量を確認してください。")
            return 3
        print(f"設定を更新しました: {args.key} = {json.dumps(config_value(candidate, args.key), ensure_ascii=False)}")
        print(f"保存先: {path}")
        return 0

    raise AssertionError(f"未対応のconfigコマンドです: {command}")


def _print_preflight_report(report: PreflightReport) -> None:
    status_labels = {
        CheckStatus.OK: "OK",
        CheckStatus.WARNING: "WARN",
        CheckStatus.ERROR: "ERROR",
    }
    for check in report.checks:
        print(f"[{status_labels[check.status]}] {check.label}: {check.message}")
    print("録画環境を利用できます。" if report.succeeded else "録画環境に解決が必要な項目があります。")


def _print_operational_status(document: dict[str, object]) -> None:
    overall_labels = {"ok": "正常", "warning": "要確認", "error": "エラー"}
    overall = str(document["overall"])
    print(f"全体状態: {overall_labels.get(overall, overall)}")
    environment = document["environment"]
    assert isinstance(environment, dict)
    for check in environment["checks"]:
        assert isinstance(check, dict)
        print(f"[{str(check['status']).upper()}] {check['label']}: {check['message']}")
    recording = document["recording"]
    history = document["history"]
    recovery = document["recovery"]
    queue = document["upload_queue"]
    assert isinstance(recording, dict)
    assert isinstance(history, dict)
    assert isinstance(recovery, dict)
    assert isinstance(queue, dict)
    print(f"録画状態: {recording['state']}")
    print(f"履歴: {history['total']}件、不整合 {history['consistency_issues']}件")
    print(f"復旧待ち: {recovery['pending']}件")
    print(f"準備キュー: {queue['total']}件")
    errors = document["errors"]
    assert isinstance(errors, list)
    for item in errors:
        assert isinstance(item, dict)
        _print_cli_error(str(item["code"]), str(item["summary"]), str(item["action"]))


def _run_record_command(
    *,
    project_root: Path,
    user_data_dir: Path | None,
    duration_seconds: float | None,
    verbose: bool,
) -> int:
    loaded = _load_config_or_report(project_root, user_data_dir)
    if loaded is None:
        return 2
    paths = default_runtime_paths(project_root=project_root, user_data_dir=user_data_dir)
    report = run_preflight(paths=paths, config=loaded.config, config_loaded=loaded.config_loaded)
    _print_preflight_report(report)
    if not report.succeeded:
        return 2

    try:
        prepared = prepare_recording(
            paths=paths,
            config=loaded.config,
            enable_visual_detection=False,
        )
    except RecordingPreparationError as exc:
        _print_cli_error("E_RECORDING_PREPARE", str(exc), "doctorで録画環境と保存先を確認してください。")
        return 3

    session = prepared.session
    try:
        try:
            state = prepared.start(source="manual", detection_reason="recordコマンドによる手動録画")
        except RecordingTrackingError as exc:
            _print_cli_error("E_RECORDING_START", str(exc), "recovery listで履歴状態を確認してください。")
            return 3
        if state is RecordingState.FAILED:
            assert session.result is not None
            _print_recording_failure(session.result, verbose=verbose)
            return 3

        print(f"録画を開始しました: id={prepared.target.recording_id}")
        print(f"保存先: {prepared.target.path}")
        print(f"自動判定: {prepared.visual_detection_status.message}")
        if duration_seconds is None:
            print("停止するにはCtrl+Cを押してください。")

        try:
            _wait_for_recording(prepared, duration_seconds)
        except KeyboardInterrupt:
            print("停止要求を受け付けました。")

        try:
            result = (
                session.result
                if session.state in {RecordingState.COMPLETED, RecordingState.FAILED}
                else prepared.stop()
            )
        except RecordingTrackingError as exc:
            _print_cli_error("E_RECORDING_STOP", str(exc), "recovery listで中断状態を確認してください。")
            return 3
        assert result is not None
        if not result.succeeded:
            _print_recording_failure(result, verbose=verbose)
            return 3
        print(f"録画を保存しました: {result.output_path}")
        print(f"size: {result.size_bytes} bytes")
        visual_status = prepared.visual_detection_status
        print(
            f"自動判定: {visual_status.state} / 候補 {visual_status.candidate_count} / "
            f"処理 {visual_status.processed_frames} / 破棄 {visual_status.dropped_frames}"
        )
        return 0
    finally:
        if session.state in {RecordingState.STARTING, RecordingState.RECORDING, RecordingState.STOPPING}:
            try:
                prepared.stop()
            except RecordingTrackingError as exc:
                _print_cli_error("E_RECORDING_CLEANUP", str(exc), "recovery listで中断状態を確認してください。")
        prepared.release()


def _wait_for_recording(prepared: PreparedRecording, duration_seconds: float | None) -> None:
    deadline = time.monotonic() + duration_seconds if duration_seconds is not None else None
    while prepared.poll() is RecordingState.RECORDING:
        if deadline is not None and time.monotonic() >= deadline:
            return
        wait_seconds = 0.2 if deadline is None else min(0.2, max(0.0, deadline - time.monotonic()))
        time.sleep(wait_seconds)


def _print_recording_failure(result: RecordingResult, *, verbose: bool) -> None:
    _print_cli_error(
        "E_RECORDING_FAILED",
        f"録画に失敗しました: {result.error or '原因不明'}",
        "recovery listで部分録画と復旧状態を確認してください。",
    )
    if verbose and result.diagnostics:
        print(f"[DETAIL] FFmpeg: {result.diagnostics[-1]}", file=sys.stderr)


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("SECONDSは数値で指定してください") from exc
    if not 0 < seconds <= 86_400:
        raise argparse.ArgumentTypeError("SECONDSは0より大きく86400以下で指定してください")
    return seconds


def _iso_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("DATETIMEはISO 8601形式で指定してください") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("DATETIMEにはタイムゾーンを含めてください")
    return parsed.astimezone(timezone.utc)


def _history_limit(value: str) -> int:
    number = _nonnegative_integer(value)
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError("limitは1から1000で指定してください")
    return number


def _nonnegative_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("0以上の整数で指定してください") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("0以上の整数で指定してください")
    return number


def _run_history_command(*, paths: RuntimePaths, args: argparse.Namespace) -> int:
    try:
        repository = RecordingHistoryRepository.from_runtime_paths(paths)
        if args.history_command == "list":
            entries = repository.query(
                HistoryQuery(
                    state=args.state,
                    since=args.since,
                    until=args.until,
                    limit=args.limit,
                    offset=args.offset,
                )
            )
            if not entries:
                print("録画履歴はありません。")
                return 0
            for entry in entries:
                _print_history_summary(entry)
            return 0
        if args.history_command == "show":
            entry = repository.get(args.recording_id)
            if entry is None:
                _print_cli_error("E_HISTORY_NOT_FOUND", f"録画履歴が見つかりません: {args.recording_id}", "history listで録画IDを確認してください。")
                return 4
            _print_history_detail(entry, paths.recordings)
            return 0
        if args.history_command in {"play", "reveal"}:
            browser = RecordingBrowser(repository=repository, recordings_root=paths.recordings)
            try:
                reference = (
                    browser.play(args.recording_id)
                    if args.history_command == "play"
                    else browser.reveal(args.recording_id)
                )
            except RecordingBrowseError as exc:
                operational = {
                    RecordingBrowseFailure.PLATFORM,
                    RecordingBrowseFailure.LAUNCH_FAILED,
                }
                _print_cli_error(
                    "E_HISTORY_OPEN",
                    str(exc),
                    "history checkで録画ファイルを確認してください。",
                )
                return 3 if exc.kind in operational else 4
            action = "再生を開始しました" if args.history_command == "play" else "保存場所を開きました"
            print(f"{action}: id={reference.recording_id} file={reference.path}")
            for warning in reference.warnings:
                print(f"[WARN] {warning}")
            return 0
        if args.history_command == "check":
            issues = repository.check_consistency()
            if not issues:
                print("録画履歴と録画ファイルに不整合はありません。")
                return 0
            labels = {
                ConsistencyIssueKind.MISSING: "MISSING",
                ConsistencyIssueKind.UNTRACKED: "UNTRACKED",
                ConsistencyIssueKind.SIZE_MISMATCH: "SIZE_MISMATCH",
                ConsistencyIssueKind.INVALID_REFERENCE: "INVALID_REFERENCE",
            }
            for issue in issues:
                identifier = f" id={issue.recording_id}" if issue.recording_id else ""
                print(f"[{labels[issue.kind]}]{identifier} {issue.path}: {issue.message}")
            print(f"不整合を{len(issues)}件検出しました。ファイルは変更していません。")
            return 4
    except (OSError, ValueError, RecordingHistoryError) as exc:
        _print_cli_error("E_HISTORY", f"録画履歴を処理できません: {exc}", "history checkで整合性を確認してください。")
        return 3
    raise RuntimeError(f"未対応のhistoryコマンドです: {args.history_command}")


def _print_history_summary(entry: RecordingHistoryEntry) -> None:
    timestamp = entry.started_at or entry.created_at
    duration = f"{entry.duration_seconds:.1f}s" if entry.duration_seconds is not None else "-"
    size = f"{entry.size_bytes}B" if entry.size_bytes is not None else "-"
    print(
        f"{timestamp.isoformat()} {entry.state:<9} id={entry.recording_id} "
        f"duration={duration} size={size} file={entry.output_path}"
    )


def _print_history_detail(entry: RecordingHistoryEntry, recordings_root: Path) -> None:
    print(f"recording id: {entry.recording_id}")
    print(f"state: {entry.state}")
    print(f"source: {entry.source}")
    print(f"detection reason: {entry.detection_reason or '-'}")
    print(f"file: {recordings_root / entry.output_path}")
    print(f"container: {entry.container}")
    print(f"created at: {entry.created_at.isoformat()}")
    print(f"started at: {entry.started_at.isoformat() if entry.started_at else '-'}")
    print(f"ended at: {entry.ended_at.isoformat() if entry.ended_at else '-'}")
    print(f"duration: {entry.duration_seconds if entry.duration_seconds is not None else '-'}")
    print(f"size: {entry.size_bytes if entry.size_bytes is not None else '-'}")
    print(f"return code: {entry.returncode if entry.returncode is not None else '-'}")
    print(f"error: {entry.error or '-'}")
    print(f"failure code: {entry.failure_code or '-'}")
    print(f"recovery policy: {entry.recovery_policy or '-'}")
    print(f"recovery state: {entry.recovery_state}")
    print(f"recovery attempts: {entry.recovery_attempts}")
    print(f"recovery message: {entry.recovery_message or '-'}")
    if entry.diagnostics:
        print("diagnostics:")
        for line in entry.diagnostics:
            print(f"  {line}")


def _detect_interrupted_on_startup(paths: RuntimePaths) -> bool:
    try:
        detections = RecoveryManager(paths=paths).detect_interrupted()
    except (OSError, RecordingHistoryError, RecoveryError) as exc:
        _print_cli_error("E_RECOVERY_DETECT", f"中断録画を確認できません: {exc}", "recovery detectを再実行してください。")
        return False
    for detection in detections:
        if detection.kind is InterruptedDetectionKind.INTERRUPTED:
            print(f"[RECOVERY] id={detection.recording_id} {detection.message}")
    return True


def _run_duel_command(*, paths: RuntimePaths, args: argparse.Namespace) -> int:
    repository = DuelRecordRepository.from_runtime_paths(paths)
    try:
        if args.duel_command == "show":
            record = repository.get(args.recording_id)
            if record is None:
                _print_cli_error(
                    "E_DUEL_NOT_FOUND",
                    f"対戦記録が見つかりません: {args.recording_id}",
                    "duel set RECORDING_ID --revision 0で作成してください。",
                )
                return EXIT_ATTENTION
            _print_duel_record(record, as_json=args.json)
            return EXIT_SUCCESS
        if args.duel_command == "set":
            current = repository.get(args.recording_id)
            base = current.values if current is not None else DuelRecordValues()
            values = DuelRecordValues(
                status=base.status,
                result=args.result if args.result is not None else base.result,
                play_order=args.play_order if args.play_order is not None else base.play_order,
                own_deck=args.own_deck if args.own_deck is not None else base.own_deck,
                opponent_deck=(
                    args.opponent_deck
                    if args.opponent_deck is not None
                    else base.opponent_deck
                ),
                duel_type=args.duel_type if args.duel_type is not None else base.duel_type,
                tags=tuple(args.tag) if args.tag is not None else base.tags,
                notes=args.notes if args.notes is not None else base.notes,
            )
            saved = repository.save(
                args.recording_id,
                values,
                expected_revision=args.revision,
                source="user",
            )
            _print_duel_record(saved, as_json=args.json)
            return EXIT_SUCCESS
        if args.duel_command == "confirm":
            saved = repository.confirm(
                args.recording_id,
                expected_revision=args.revision,
            )
            _print_duel_record(saved, as_json=args.json)
            return EXIT_SUCCESS
        if args.duel_command == "history":
            changes = repository.changes(args.recording_id)
            if args.json:
                document = [
                    {
                        "change_id": change.change_id,
                        "recording_id": change.recording_id,
                        "revision": change.revision,
                        "source": change.source,
                        "before": change.before,
                        "after": change.after,
                        "changed_at": change.changed_at.isoformat(),
                    }
                    for change in changes
                ]
                print(json.dumps(document, ensure_ascii=False, sort_keys=True))
            elif not changes:
                print("変更履歴はありません。")
            else:
                for change in changes:
                    print(
                        f"revision={change.revision} source={change.source} "
                        f"changed_at={change.changed_at.isoformat()}"
                    )
            return EXIT_SUCCESS
    except DuelRecordConflictError as exc:
        _print_cli_error("E_DUEL_CONFLICT", str(exc), "duel showで最新revisionを確認してください。")
        return EXIT_ATTENTION
    except (DuelRecordError, ValueError) as exc:
        _print_cli_error("E_DUEL", str(exc), "録画IDと入力値を確認してください。")
        return EXIT_ATTENTION
    raise RuntimeError(f"未対応のduelコマンドです: {args.duel_command}")


def _print_duel_record(record: object, *, as_json: bool) -> None:
    document = record.to_dict()
    if as_json:
        print(json.dumps(document, ensure_ascii=False, sort_keys=True))
        return
    print(f"recording id: {document['recording_id']}")
    print(f"status: {document['status']}")
    print(f"result: {document['result']}")
    print(f"play order: {document['play_order']}")
    print(f"own deck: {document['own_deck'] or '-'}")
    print(f"opponent deck: {document['opponent_deck'] or '-'}")
    print(f"duel type: {document['duel_type']}")
    print(f"tags: {', '.join(document['tags']) or '-'}")
    print(f"notes: {document['notes'] or '-'}")
    print(f"revision: {document['revision']}")
    print(f"updated at: {document['updated_at']}")


def _run_timeline_command(*, paths: RuntimePaths, args: argparse.Namespace) -> int:
    repository = DuelTimelineRepository.from_runtime_paths(paths)
    try:
        if args.timeline_command == "list":
            events = repository.list(
                args.recording_id,
                status=args.status,
                event_type=args.type,
            )
            if args.json:
                print(json.dumps([event.to_dict() for event in events], ensure_ascii=False, sort_keys=True))
            elif not events:
                print("対戦イベントはありません。")
            else:
                for event in events:
                    _print_timeline_event(event, as_json=False)
            return EXIT_SUCCESS
        if args.timeline_command == "add":
            event = repository.add(
                args.recording_id,
                elapsed_ms=args.elapsed_ms,
                event_type=args.type,
                actor=args.actor,
                outcome=args.outcome,
                label=args.label,
                source="manual",
                status="confirmed",
            )
            _print_timeline_event(event, as_json=args.json)
            return EXIT_SUCCESS
        if args.timeline_command == "confirm":
            event = repository.confirm(args.event_id)
            _print_timeline_event(event, as_json=args.json)
            return EXIT_SUCCESS
        if args.timeline_command == "reject":
            event = repository.reject(args.event_id)
            _print_timeline_event(event, as_json=args.json)
            return EXIT_SUCCESS
    except (DuelTimelineError, ValueError) as exc:
        _print_cli_error("E_TIMELINE", str(exc), "録画ID、イベント時刻、状態を確認してください。")
        return EXIT_ATTENTION
    raise RuntimeError(f"未対応のtimelineコマンドです: {args.timeline_command}")


def _print_timeline_event(event: DuelEvent, *, as_json: bool) -> None:
    document = event.to_dict()
    if as_json:
        print(json.dumps(document, ensure_ascii=False, sort_keys=True))
        return
    details = event.label or event.outcome or event.actor or "-"
    print(
        f"{event.elapsed_ms:>8}ms {event.event_type:<12} "
        f"status={event.status} source={event.source} detail={details} id={event.event_id}"
    )


def _run_recovery_command(
    *,
    paths: RuntimePaths,
    args: argparse.Namespace,
    project_root: Path,
    user_data_dir: Path | None,
) -> int:
    try:
        repository = RecordingHistoryRepository.from_runtime_paths(paths)
        if args.recovery_command == "list":
            entries = repository.recovery_entries()
            if not entries:
                print("復旧対象の録画はありません。")
                return 0
            for entry in entries:
                print(
                    f"{entry.recovery_state:<13} id={entry.recording_id} "
                    f"code={entry.failure_code or '-'} attempts={entry.recovery_attempts} "
                    f"file={entry.output_path}"
                )
                if entry.recovery_message:
                    print(f"  {entry.recovery_message}")
            return 0
        if args.recovery_command == "detect":
            detections = RecoveryManager(paths=paths, repository=repository).detect_interrupted()
            if not detections:
                print("中断された録画はありません。")
                return 0
            for detection in detections:
                label = "ACTIVE" if detection.kind is InterruptedDetectionKind.ACTIVE else "INTERRUPTED"
                print(f"[{label}] id={detection.recording_id} {detection.message}")
            return 0
        if args.recovery_command == "ignore":
            entry = repository.set_recovery_state(
                args.recording_id,
                state="ignored",
                message="ユーザー操作により復旧対象から除外しました。",
                diagnostic="recovery ignore command",
            )
            print(f"復旧対象から除外しました: id={entry.recording_id}")
            return 0

        loaded = _load_config_or_report(project_root, user_data_dir)
        if loaded is None:
            return 2
        discovery = discover_ffmpeg(loaded.config.ffmpeg_path)
        if not discovery.found or discovery.executable is None:
            _print_cli_error("E_FFMPEG_NOT_FOUND", "FFmpegが見つかりません。", "doctorで詳細を確認してください。")
            return 2
        service = MediaRecoveryService(
            repository=repository,
            ffmpeg_executable=discovery.executable,
        )
        if args.recovery_command == "inspect":
            inspection = service.inspect(args.recording_id)
            print(f"status: {inspection.status.value}")
            print(f"file: {inspection.path}")
            print(f"message: {inspection.message}")
            print(
                "duration: "
                + (f"{inspection.duration_seconds:.3f}" if inspection.duration_seconds is not None else "-")
            )
            print(f"streams: {','.join(inspection.stream_types) or '-'}")
            return 4 if inspection.status in {InspectionStatus.UNRECOVERABLE, InspectionStatus.RETRYABLE} else 0
        if args.recovery_command == "repair":
            try:
                result = service.repair(args.recording_id, dry_run=args.dry_run)
            except KeyboardInterrupt:
                print("復旧処理の停止要求を受け付けました。")
                print("元録画と部分成果物は削除していません。recovery listで状態を確認してください。")
                return EXIT_INTERRUPTED
            print(f"original: {result.original_path}")
            print(f"output: {result.output_path}")
            print(result.message)
            if result.dry_run:
                return 0
            return 0 if result.succeeded else 4
    except (OSError, ValueError, MediaRecoveryError, RecordingHistoryError, RecoveryError) as exc:
        _print_cli_error("E_RECOVERY", f"復旧処理に失敗しました: {exc}", "recovery listで対象状態を確認してください。")
        return 3
    raise RuntimeError(f"未対応のrecoveryコマンドです: {args.recovery_command}")


def _run_prepare_command(
    *,
    paths: RuntimePaths,
    args: argparse.Namespace,
    project_root: Path,
    user_data_dir: Path | None,
) -> int:
    try:
        repository = RecordingHistoryRepository.from_runtime_paths(paths)
        queue = UploadQueueStore(paths)
        restored = queue.restore_interrupted()
        for item in restored:
            print(f"[RESTORED] id={item.queue_id} 前回中断した準備処理を待機状態へ戻しました。")

        if args.prepare_command == "enqueue":
            loaded = _load_config_or_report(project_root, user_data_dir)
            if loaded is None:
                return 2
            history = repository.get(args.recording_id)
            if history is None or history.state != "completed":
                _print_cli_error("E_PREPARE_HISTORY", "正常完了した録画履歴だけを登録できます。", "history showで録画状態を確認してください。")
                return 4
            source = (paths.recordings / history.output_path).resolve()
            if not source.is_file() or source.stat().st_size <= 0:
                _print_cli_error("E_PREPARE_SOURCE", f"録画ファイルが存在しないか空です: {source}", "history checkで録画ファイルを確認してください。")
                return 4
            privacy_value = args.privacy or loaded.config.upload_privacy_status
            metadata = UploadMetadata(
                title=args.title,
                description=args.description,
                tags=tuple(args.tag),
                privacy=UploadPrivacy(privacy_value),
            )
            item = queue.enqueue(recording_id=args.recording_id, metadata=metadata)
            print(f"準備キューへ追加しました: id={item.queue_id} recording={item.recording_id}")
            print(f"privacy: {item.metadata.privacy.value}")
            return 0
        if args.prepare_command == "list":
            items = queue.list()
            if not items:
                print("アップロード準備キューは空です。")
                return 0
            for item in items:
                print(
                    f"{item.state.value:<10} id={item.queue_id} recording={item.recording_id} "
                    f"attempts={item.attempts} privacy={item.metadata.privacy.value}"
                )
            return 0
        if args.prepare_command == "show":
            item = queue.get(args.queue_id)
            if item is None:
                _print_cli_error("E_QUEUE_NOT_FOUND", f"キュー項目が見つかりません: {args.queue_id}", "prepare listでキューIDを確認してください。")
                return 4
            _print_prepare_item(item)
            return 0
        if args.prepare_command == "cancel":
            item = queue.get(args.queue_id)
            if item is None:
                _print_cli_error("E_QUEUE_NOT_FOUND", f"キュー項目が見つかりません: {args.queue_id}", "prepare listでキューIDを確認してください。")
                return 4
            cancelled = queue.transition(
                item.queue_id,
                UploadQueueState.CANCELLED,
                error="ユーザー操作によりキャンセルしました",
            )
            print(f"準備キューをキャンセルしました: id={cancelled.queue_id}")
            return 0
        if args.prepare_command == "run":
            if args.queue_id is None and not any(
                item.state is UploadQueueState.WAITING for item in queue.list()
            ):
                print("処理対象の準備キューはありません。")
                return 0
            loaded = _load_config_or_report(project_root, user_data_dir)
            if loaded is None:
                return 2
            discovery = discover_ffmpeg(loaded.config.ffmpeg_path)
            if not discovery.found or discovery.executable is None:
                _print_cli_error("E_FFMPEG_NOT_FOUND", "FFmpegが見つかりません。", "doctorで詳細を確認してください。")
                return 2
            validator = UploadMediaValidator(
                ffprobe_executable=find_ffprobe(discovery.executable),
            )
            service = UploadPreparationService(
                paths=paths,
                repository=repository,
                queue=queue,
                exporter=UploadExporter(
                    paths=paths,
                    ffmpeg_executable=discovery.executable,
                    validator=validator,
                ),
                manifest_writer=UploadManifestWriter(paths),
            )
            try:
                results = service.process(
                    args.queue_id,
                    progress=lambda item: print(
                        f"[STARTED] id={item.queue_id} recording={item.recording_id} "
                        "アップロード準備を開始します。"
                    ),
                )
            except KeyboardInterrupt:
                print("アップロード準備の停止要求を受け付けました。")
                print("処理中状態と部分出力を保持しました。次回実行時に待機状態へ戻します。")
                return EXIT_INTERRUPTED
            if not results:
                print("処理対象の準備キューはありません。")
                return 0
            for result in results:
                print(
                    f"[{result.state.value.upper()}] id={result.queue_id} "
                    f"recording={result.recording_id} {result.message}"
                )
            return 0 if all(result.succeeded for result in results) else 4
    except (OSError, ValueError, UploadMetadataError, UploadPreparationError, UploadQueueError) as exc:
        _print_cli_error("E_PREPARE", f"アップロード準備を処理できません: {exc}", "prepare listで状態を確認してください。")
        return 3
    raise RuntimeError(f"未対応のprepareコマンドです: {args.prepare_command}")


def _print_prepare_item(item: UploadQueueItem) -> None:
    print(f"queue id: {item.queue_id}")
    print(f"recording id: {item.recording_id}")
    print(f"state: {item.state.value}")
    print(f"attempts: {item.attempts}")
    print(f"title: {item.metadata.title}")
    print(f"description: {item.metadata.description or '-'}")
    print(f"tags: {', '.join(item.metadata.tags) or '-'}")
    print(f"privacy: {item.metadata.privacy.value}")
    print(f"export: {item.export_path or '-'}")
    print(f"manifest: {item.manifest_path or '-'}")
    print(f"error: {item.error or '-'}")


def _run_watch_command(*, project_root: Path, user_data_dir: Path | None, once: bool) -> int:
    loaded = _load_config_or_report(project_root, user_data_dir)
    if loaded is None:
        return 2
    config = loaded.config
    try:
        monitor = GameWindowMonitor(
            process_name=config.game_process_name,
            title_contains=config.game_window_title_contains,
        )
    except RuntimeError as exc:
        _print_cli_error("E_WINDOW_MONITOR", str(exc), "Windows環境と検出設定を確認してください。")
        return 2

    if once:
        game = monitor.observe()
        _print_game_window_observation(game)
        return 2 if game.status is GameWindowStatus.ERROR else 0

    service = RecorderApplicationService(project_root=project_root, user_data_dir=user_data_dir)
    failed = False

    def report_event(event: ApplicationEvent) -> None:
        nonlocal failed
        if event.kind == "visual":
            return
        if event.kind == "error":
            failed = True
        output = sys.stderr if event.kind == "error" else sys.stdout
        recording = f" id={event.recording_id}" if event.recording_id else ""
        print(f"[{event.kind.upper()}]{recording} {event.message}", file=output)

    service.start_watch(report_event)
    print("Master Duel対戦画面の監視を開始しました。停止するにはCtrl+Cを押してください。")
    try:
        while service.watch_active:
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("監視の停止要求を受け付けました。")
    finally:
        service.stop_watch()
    return 3 if failed else 0


def _print_game_window_observation(game: GameWindowObservation) -> None:
    status_labels = {
        GameWindowStatus.NOT_RUNNING: "NOT_RUNNING",
        GameWindowStatus.RUNNING_NO_WINDOW: "NO_WINDOW",
        GameWindowStatus.MINIMIZED: "MINIMIZED",
        GameWindowStatus.VISIBLE: "VISIBLE",
        GameWindowStatus.ERROR: "ERROR",
    }
    print(f"[{status_labels[game.status]}] {game.message}")
    if game.process is not None:
        print(f"pid: {game.process.pid}")
    if game.window is not None:
        print(f"window: {game.window.handle} {game.window.width}x{game.window.height}")


if __name__ == "__main__":
    raise SystemExit(main())
