"""通常GUIが明示的に呼ぶ、起動時の動画整理と後処理の順序です。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .data_protection import initialize_protected_history_database
from .post_upload_cleanup import PostUploadCleanupHandler, UploadCleanupOutcome
from .recording_lock import RecordingBusyError
from .recording_retention import RecordingDeletionService, RetentionResult, VideoDeletionResult
from .runtime_paths import RuntimePaths
from .video_lifecycle import VideoLifecycleRepository


@dataclass(frozen=True)
class StartupMaintenanceResult:
    operation_allowed: bool
    message: str
    recovery: tuple[VideoDeletionResult, ...] = ()
    retention: RetentionResult | None = None
    upload_retries: tuple[UploadCleanupOutcome, ...] = ()


def run_startup_maintenance(
    paths: RuntimePaths,
    *,
    deletion_service: RecordingDeletionService | None = None,
    cleanup_handler: PostUploadCleanupHandler | None = None,
    progress: Callable[[str], None] | None = None,
) -> StartupMaintenanceResult:
    """呼出し側が通常起動に限定し、一度だけ実行してからworkerを解禁します。"""
    recovery: tuple[VideoDeletionResult, ...] = ()
    retention = None
    retries: list[UploadCleanupOutcome] = []
    messages: list[str] = []

    def report(message: str) -> None:
        if progress is not None:
            try:
                progress(message)
            except Exception:
                # 表示先の終了・通信失敗を動画操作の許可に影響させません。
                messages.append("起動処理の進捗表示を更新できませんでした")

    def result(*, allowed: bool, message: str) -> StartupMaintenanceResult:
        details = [message, *messages]
        if retention is not None and retention.message:
            details.append(retention.message)
        if retries:
            deleted = sum(item.status in {"deleted", "already_deleted"} for item in retries)
            waiting = sum(item.status in {"skipped", "retry_pending"} for item in retries)
            failed = sum(item.status == "failed" for item in retries)
            details.append(f"投稿後削除の再試行: 削除済み{deleted}件、見送り{waiting}件、失敗{failed}件")
        return StartupMaintenanceResult(allowed, "\n".join(details), recovery, retention, tuple(retries))

    report("履歴の保存構造と移行結果を確認しています")
    try:
        initialize_protected_history_database(paths)
        service = deletion_service or RecordingDeletionService(paths)
        if service.paths.root.resolve() != paths.root.resolve():
            raise ValueError("動画整理サービスの保存先が起動先と一致しません")
        repository: VideoLifecycleRepository = service.repository
    except Exception as exc:
        return result(allowed=False, message=f"履歴を安全に準備できません。設定・診断を確認してください: {exc}")

    try:
        # 全段階で同じサービスの排他を保持し、段階の隙間で別操作を始めさせません。
        with service.operation_lock():
            report("前回中断した動画削除を回復しています")
            recovery = service.recover_pending()
            if repository.pending_intents():
                return result(allowed=False, message="動画削除の回復が未完了です。設定・診断から確認し、通常起動で再回復してください")

            report("保存期限と保存容量に従って元動画を確認しています")
            try:
                retention = service.run_retention()
            except Exception as exc:
                messages.append(f"期限・容量による動画整理を見送りました: {exc}")
            if repository.pending_intents():
                return result(allowed=False, message="動画整理が中断したため、回復が完了するまで録画・動画操作を待機します")

            report("保存された投稿後削除の再試行要求を確認しています")
            requests = repository.pending_cleanup_requests()
            if requests:
                handler = cleanup_handler or PostUploadCleanupHandler(
                    paths, deletion_service=service, lifecycle_repository=repository,
                )
                for request in requests:
                    try:
                        outcome = handler.retry_request(request)
                        if not isinstance(outcome, UploadCleanupOutcome):
                            raise ValueError("投稿後削除の再試行結果が不正です")
                    except Exception as exc:
                        outcome = UploadCleanupOutcome("failed", "retry_failed", f"投稿後削除の再試行に失敗しました: {exc}")
                    retries.append(outcome)
                    if repository.pending_intents():
                        return result(allowed=False, message="投稿後削除が中断したため、回復が完了するまで録画・動画操作を待機します")
            report("動画保存チェックを完了しました")
            return result(allowed=True, message="起動時の動画保存チェックを完了しました")
    except RecordingBusyError:
        # 他の録画・動画操作は自身の排他で守る。今回の整理だけを見送ります。
        try:
            pending = bool(repository.pending_intents())
        except Exception as exc:
            return result(allowed=False, message=f"動画削除の回復状態を確認できません: {exc}")
        return result(allowed=not pending, message="別の録画・動画操作が使用中のため、起動時の動画整理を見送りました")
    except Exception as exc:
        # 完了を確認できないjournalは操作を解禁する根拠にしません。
        return result(allowed=False, message=f"起動時の動画保存チェックを確認できません。設定・診断を確認してください: {exc}")
