from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from .config import AppConfig, LoadedAppConfig, load_app_config, validate_app_config
from .data_protection import BackupInfo, DataProtectionError, DataProtectionService
from .history_database import connect_history_database
from .recording_lock import RecordingBusyError, RecordingLock
from .runtime_paths import RuntimePaths
from .upload_queue import UploadQueueState, UploadQueueStore
from .video_file_usage import exclusive_delete_guard, query_file_use
from .video_lifecycle import DeletionIntent, VideoAsset, VideoLifecycleError, VideoLifecycleRepository


STAGING_DIRECTORY = ".mdrl-video-staging"
DELETION_REASONS = {"capacity", "retention", "youtube_uploaded", "manual"}
VIDEO_SUFFIXES = {".mkv", ".mp4", ".mov", ".avi", ".webm", ".m4v", ".ts", ".pending"}


@dataclass(frozen=True)
class VideoDeletionResult:
    status: str
    recording_id: str
    reason: str
    message: str
    retryable: bool = False
    journal_started: bool = False
    intent_id: str | None = None


@dataclass(frozen=True)
class RetentionResult:
    deletions: tuple[VideoDeletionResult, ...]
    total_bytes: int
    capacity_bytes: int
    capacity_met: bool
    message: str


@dataclass(frozen=True)
class FileFingerprint:
    size_bytes: int
    mtime_ns: int
    sha256: str
    device: int
    inode: int


class _SkipDeletion(RuntimeError):
    def __init__(self, reason: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


class RecordingDeletionService:
    """明示された単件削除・起動整理だけを実行する。生成や参照では削除しない。"""

    def __init__(
        self,
        paths: RuntimePaths,
        *,
        repository: VideoLifecycleRepository | None = None,
        protection: DataProtectionService | None = None,
        config_loader: Callable[[], AppConfig | LoadedAppConfig] | None = None,
        now: Callable[[], datetime] | None = None,
        fault_hook: Callable[[str, str], None] | None = None,
    ) -> None:
        self.paths = paths
        self.repository = repository or VideoLifecycleRepository.from_runtime_paths(paths)
        self.protection = protection or DataProtectionService(paths)
        self._config_loader = config_loader or (lambda: load_app_config(user_data_dir=paths.root))
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._fault_hook = fault_hook or (lambda _phase, _intent_id: None)
        self._operation_local = threading.local()

    def _settings(self) -> AppConfig:
        loaded = self._config_loader()
        settings = loaded.config if isinstance(loaded, LoadedAppConfig) else loaded
        validate_app_config(settings)
        return settings

    @contextmanager
    def operation_lock(self) -> Iterator[None]:
        if getattr(self._operation_local, "active", False):
            yield
            return
        with RecordingLock.acquire(
            self.paths.data / "video-operations.lock", recording_id="video-operations"
        ):
            with RecordingLock.acquire(
                self.paths.data / "recording.lock", recording_id="video-maintenance"
            ):
                self._operation_local.active = True
                try:
                    yield
                finally:
                    self._operation_local.active = False

    def delete_video(
        self,
        recording_id: str,
        *,
        reason: str = "manual",
        expected_asset_id: str | None = None,
        expected_generation: int | None = None,
        expected_sha256: str | None = None,
        upload_id: str | None = None,
        prepare_queue_id: str | None = None,
        provenance: dict[str, object] | None = None,
        allow_protected: bool = False,
    ) -> VideoDeletionResult:
        if reason not in DELETION_REASONS or (allow_protected and reason != "manual"):
            return VideoDeletionResult("skipped", recording_id, "invalid_request", "削除操作の指定が不正です")
        try:
            with self.operation_lock():
                if self.repository.pending_intents():
                    raise _SkipDeletion("recovery_pending", "未完了の動画削除を通常起動で回復してください", retryable=True)
                asset = self.repository.ensure_current_asset(recording_id)
                previous = self.repository.deletion_for_asset(asset.asset_id)
                if previous is not None:
                    return VideoDeletionResult("already_deleted", recording_id, previous.reason, "元動画は削除済みです")
                if ((expected_asset_id is not None and asset.asset_id != expected_asset_id)
                        or (expected_generation is not None and asset.generation != expected_generation)):
                    raise _SkipDeletion("source_mismatch", "元動画の世代が投稿時と一致しません")
                fixed_now = self._now()
                self._check_eligibility(asset, reason, fixed_now, allow_protected=allow_protected)
                source = safe_file_path(self.paths.recordings, asset.path)
                fingerprint = fingerprint_file(source)
                self._check_fingerprint(asset, fingerprint, expected_sha256)
                if reason == "youtube_uploaded":
                    self._verify_upload_provenance(asset, fingerprint, upload_id, prepare_queue_id, provenance)
                with self.retained_backup("pre-video-delete") as backup:
                    return self._delete_locked(
                        asset, fingerprint, reason, fixed_now, backup,
                        upload_id=upload_id, prepare_queue_id=prepare_queue_id,
                        provenance=provenance, allow_protected=allow_protected,
                    )
        except _SkipDeletion as exc:
            return VideoDeletionResult("skipped", recording_id, exc.reason, str(exc), retryable=exc.retryable)
        except (RecordingBusyError, DataProtectionError) as exc:
            return VideoDeletionResult("skipped", recording_id, "temporarily_unavailable", str(exc), retryable=True)
        except (OSError, sqlite3.Error, VideoLifecycleError, ValueError) as exc:
            return VideoDeletionResult("failed", recording_id, "validation_failed", f"動画を削除できません: {exc}")

    @contextmanager
    def retained_backup(self, reason: str) -> Iterator[BackupInfo]:
        """同じ管理操作の動画削除と履歴削除で、保持中の一世代を共有します。"""
        if not getattr(self._operation_local, "active", False):
            raise ValueError("動画操作の排他を取得してからバックアップを保持してください")
        current = getattr(self._operation_local, "backup", None)
        if current is not None:
            self._verify_backup(current)
            yield current
            return
        with self.protection.retained_backup(reason) as backup:
            self._operation_local.backup = backup
            try:
                yield backup
            finally:
                self._operation_local.backup = None

    def run_retention(self, *, now: datetime | None = None) -> RetentionResult:
        """通常GUIの起動gateから一度だけ呼ぶ。全履歴を固定時刻で走査する。"""
        results: list[VideoDeletionResult] = []
        limit = 0
        try:
            with self.operation_lock():
                settings = self._settings()
                limit = settings.recording_storage_max_bytes
                fixed_now = now or self._now()
                if fixed_now.tzinfo is None:
                    raise ValueError("保存期限の判定時刻にはタイムゾーンが必要です")
                if self.repository.pending_intents():
                    raise _SkipDeletion("recovery_pending", "未完了の動画削除が残っているため整理を見送ります")
                if settings.recording_retention_seconds == 0 and limit == 0:
                    return RetentionResult((), self.total_original_bytes(), 0, True, "動画の保存容量・期限は無制限です")
                with closing(connect_history_database(self.repository.database_path)) as connection:
                    rows = connection.execute(
                        "SELECT recording_id, started_at, created_at FROM recordings"
                    ).fetchall()
                dated: list[tuple[datetime, str]] = []
                for row in rows:
                    try:
                        dated.append((_baseline(row[1], row[2]), str(row[0])))
                    except ValueError:
                        results.append(VideoDeletionResult("skipped", str(row[0]), "unknown_date", "録画日時を確認できません"))
                dated.sort(key=lambda item: (item[0], item[1]))
                expiry = [identifier for stamp, identifier in dated
                          if settings.recording_retention_seconds > 0
                          and (fixed_now - stamp).total_seconds() > settings.recording_retention_seconds]
                expired_ids = set(expiry)
                candidates = expiry + [identifier for _, identifier in dated if identifier not in expired_ids]
                # 期限を先に処理し、その後に実容量を再評価する。同じ動画を重複処理しない。
                with self.protection.retained_backup("pre-retention") as backup:
                    for identifier in candidates:
                        reason = "retention" if identifier in expired_ids else "capacity"
                        if reason == "capacity" and (limit == 0 or self.total_original_bytes() <= limit):
                            break
                        try:
                            asset = self.repository.ensure_current_asset(identifier)
                            if self.repository.deletion_for_asset(asset.asset_id) is not None:
                                continue
                            self._check_eligibility(asset, reason, fixed_now)
                            path = safe_file_path(self.paths.recordings, asset.path)
                            fingerprint = fingerprint_file(path)
                            self._check_fingerprint(asset, fingerprint, None)
                            result = self._delete_locked(asset, fingerprint, reason, fixed_now, backup)
                        except _SkipDeletion as exc:
                            result = VideoDeletionResult("skipped", identifier, exc.reason, str(exc), retryable=exc.retryable)
                        except (OSError, ValueError, sqlite3.Error, VideoLifecycleError) as exc:
                            result = VideoDeletionResult("failed", identifier, "validation_failed", str(exc))
                        results.append(result)
                        if result.journal_started and result.status == "failed":
                            break
                total = self.total_original_bytes()
                met = limit == 0 or total <= limit
                message = "動画の保存チェックが完了しました" if met else "保護・未登録・使用中などの動画が残り、保存容量の上限に収まりませんでした"
                return RetentionResult(tuple(results), total, limit, met, message)
        except Exception as exc:
            # 設定/排他/backup失敗を、削除や正常完了に置き換えない。
            try:
                total = self.total_original_bytes()
            except Exception:
                total = 0
            return RetentionResult(tuple(results), total, limit, False, f"動画整理を見送りました: {exc}")

    def recover_pending(self) -> tuple[VideoDeletionResult, ...]:
        results: list[VideoDeletionResult] = []
        try:
            with self.operation_lock():
                for intent in self.repository.pending_intents():
                    try:
                        result = self._recover_intent(intent)
                    except Exception as exc:
                        result = VideoDeletionResult(
                            "failed", intent.recording_id, "recovery_failed", f"削除の回復が必要です: {exc}",
                            journal_started=True, intent_id=intent.intent_id,
                        )
                    results.append(result)
        except (RecordingBusyError, OSError) as exc:
            results.append(VideoDeletionResult("skipped", "", "operation_busy", str(exc), retryable=True))
        return tuple(results)

    def _delete_locked(
        self, asset: VideoAsset, fingerprint: FileFingerprint, reason: str, now: datetime,
        backup: BackupInfo, *, upload_id: str | None = None, prepare_queue_id: str | None = None,
        provenance: dict[str, object] | None = None, allow_protected: bool = False,
    ) -> VideoDeletionResult:
        intent: DeletionIntent | None = None
        try:
            self._check_eligibility(asset, reason, now, allow_protected=allow_protected)
            source = safe_file_path(self.paths.recordings, asset.path)
            if fingerprint_file(source) != fingerprint:
                raise _SkipDeletion("source_changed", "削除の確認中に元動画が変化しました")
            _assert_file_not_in_use(source)
            self._verify_backup(backup)
            if reason == "youtube_uploaded":
                self._verify_upload_provenance(asset, fingerprint, upload_id, prepare_queue_id, provenance)
            self.repository.set_verified_asset(
                asset.asset_id, size_bytes=fingerprint.size_bytes,
                mtime_ns=fingerprint.mtime_ns, sha256=fingerprint.sha256,
            )
            staging = self.paths.recordings / STAGING_DIRECTORY
            if staging.exists():
                _assert_no_links(self.paths.recordings, staging)
            staging.mkdir(exist_ok=True)
            _assert_no_links(self.paths.recordings, staging)
            staged_relative = f"{STAGING_DIRECTORY}/{uuid.uuid4().hex}.pending"
            intent = self.repository.create_intent(
                recording_id=asset.recording_id, asset_id=asset.asset_id, generation=asset.generation,
                reason=reason, original_path=asset.path, staged_path=staged_relative,
                size_bytes=fingerprint.size_bytes, mtime_ns=fingerprint.mtime_ns,
                sha256=fingerprint.sha256, backup_path=str(backup.path),
                upload_id=upload_id, prepare_queue_id=prepare_queue_id, allow_protected=allow_protected,
                metadata={"provenance": provenance or {}, "device": fingerprint.device, "inode": fingerprint.inode},
            )
            self._fault_hook("after_intent", intent.intent_id)
            self._check_eligibility(asset, reason, now, allow_protected=allow_protected, own_intent=intent.intent_id)
            if fingerprint_file(source) != fingerprint:
                raise _SkipDeletion("source_changed", "元動画が変化したため削除を中止します")
            staged = safe_file_path(self.paths.recordings, staged_relative, must_exist=False, allow_staging=True)
            if staged.exists():
                raise _SkipDeletion("staging_collision", "動画の退避先が既に存在します")
            _assert_file_not_in_use(source)
            with exclusive_delete_guard(source):
                if source.stat().st_ino != fingerprint.inode:
                    raise _SkipDeletion("source_changed", "動画が差し替えられたため削除を中止します")
                os.rename(source, staged)
            self._fault_hook("after_rename", intent.intent_id)
            self.repository.update_intent(intent.intent_id, "staged")
            intent = self.repository.get_intent(intent.intent_id)
            assert intent is not None
            return self._unlink_staged(intent, asset, now, backup, allow_protected=allow_protected)
        except Exception as exc:
            if intent is not None:
                return self._interrupted(intent, exc)
            if isinstance(exc, _SkipDeletion):
                return VideoDeletionResult("skipped", asset.recording_id, exc.reason, str(exc), retryable=exc.retryable)
            return VideoDeletionResult("failed", asset.recording_id, "deletion_failed", str(exc), retryable=True)

    def _unlink_staged(
        self, intent: DeletionIntent, asset: VideoAsset, now: datetime, backup: BackupInfo,
        *, allow_protected: bool = False,
    ) -> VideoDeletionResult:
        staged = safe_file_path(self.paths.recordings, intent.staged_path, allow_staging=True)
        current = fingerprint_file(staged)
        if (current.size_bytes, current.mtime_ns, current.sha256) != (intent.size_bytes, intent.mtime_ns, intent.sha256):
            raise _SkipDeletion("source_changed", "退避した動画の同一性を確認できません")
        self._check_eligibility(asset, intent.reason, now, allow_protected=allow_protected, own_intent=intent.intent_id)
        self._verify_backup(backup)
        if intent.reason == "youtube_uploaded":
            self._verify_upload_provenance(asset, current, intent.upload_id, intent.prepare_queue_id, intent.metadata.get("provenance"))
        self._fault_hook("before_unlink", intent.intent_id)
        # fault_hookや表示更新を挟んだ後も、破壊操作直前に保存値・保護・現物を再照合します。
        self._check_eligibility(asset, intent.reason, now, allow_protected=allow_protected, own_intent=intent.intent_id)
        if fingerprint_file(staged) != current:
            raise _SkipDeletion("source_changed", "退避した動画が変化しました")
        self._verify_backup(backup)
        if intent.reason == "youtube_uploaded":
            self._verify_upload_provenance(asset, current, intent.upload_id, intent.prepare_queue_id, intent.metadata.get("provenance"))
        _assert_file_not_in_use(staged)
        with exclusive_delete_guard(staged):
            if staged.stat().st_ino != current.inode:
                raise _SkipDeletion("source_changed", "退避動画が差し替えられたため削除を中止します")
            staged.unlink()
        self._fault_hook("after_unlink", intent.intent_id)
        self.repository.update_intent(intent.intent_id, "unlinked")
        self._fault_hook("before_complete", intent.intent_id)
        record = self.repository.complete_deletion(intent.intent_id)
        return VideoDeletionResult("deleted", asset.recording_id, record.reason, "元動画を削除しました", journal_started=True, intent_id=intent.intent_id)

    def _interrupted(self, intent: DeletionIntent, exc: Exception) -> VideoDeletionResult:
        # unlink後のDB障害では履歴・マーカーを今ここで戻さず、次の通常起動で確定します。
        try:
            current = self.repository.get_intent(intent.intent_id)
            assert current is not None
            original = safe_file_path(self.paths.recordings, intent.original_path, must_exist=False)
            staged = safe_file_path(self.paths.recordings, intent.staged_path, must_exist=False, allow_staging=True)
            if staged.is_file() and not original.exists():
                fingerprint = fingerprint_file(staged)
                if fingerprint.sha256 == intent.sha256 and fingerprint.size_bytes == intent.size_bytes:
                    os.rename(staged, original)
                    self.repository.update_intent(intent.intent_id, "cancelled", error=str(exc))
                else:
                    self.repository.update_intent(intent.intent_id, current.state, error=str(exc))
            elif original.is_file() and not staged.exists() and current.state == "intent":
                self.repository.update_intent(intent.intent_id, "cancelled", error=str(exc))
            else:
                self.repository.update_intent(intent.intent_id, current.state, error=str(exc))
        except Exception:
            pass
        return VideoDeletionResult("failed", intent.recording_id, "deletion_interrupted", f"動画削除を確定できません: {exc}", journal_started=True, intent_id=intent.intent_id)

    def _recover_intent(self, intent: DeletionIntent) -> VideoDeletionResult:
        if not self.paths.recordings.is_dir():
            raise ValueError("動画の保存領域を確認できないため、削除済みと判断しません")
        with os.scandir(self.paths.recordings) as entries:
            next(entries, None)
        original = safe_file_path(self.paths.recordings, intent.original_path, must_exist=False)
        staged = safe_file_path(self.paths.recordings, intent.staged_path, must_exist=False, allow_staging=True)
        asset = self.repository.get_asset(intent.asset_id)
        if asset is None or asset.recording_id != intent.recording_id or asset.generation != intent.generation:
            raise ValueError("削除journalと動画の世代が一致しません")
        if intent.state == "unlinked" or (intent.state == "staged" and not staged.exists() and not original.exists()):
            if intent.state != "unlinked" and (not staged.parent.is_dir() or not original.parent.is_dir()):
                raise ValueError("退避先・元動画の領域が確認できないため、削除済みと判断しません")
            if staged.exists():
                raise ValueError("削除済みjournalの退避動画が残っています")
            if intent.state != "unlinked" and intent.metadata.get("restored_from_backup") is True:
                # 復元snapshotのstagedは、現在の動画をunlinkした証拠ではありません。
                stamp = self._now().isoformat()
                metadata = json.dumps({"restored_from_backup": True, "original_deletion_time_known": False}, ensure_ascii=False)
                with closing(connect_history_database(self.repository.database_path)) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "INSERT INTO video_deletion_history(deletion_id,recording_id,asset_id,generation,reason,deleted_at,"
                        "original_path,size_bytes,sha256,cause_known,metadata_json) VALUES (?,?,?,?,'restored_deleted',?,?,?,?,0,?) "
                        "ON CONFLICT(asset_id,generation) DO NOTHING",
                        (f"restored-{intent.intent_id}", intent.recording_id, intent.asset_id, intent.generation,
                         stamp, intent.original_path, intent.size_bytes, intent.sha256, metadata),
                    )
                    connection.execute("UPDATE recording_video_assets SET state='deleted',updated_at=? WHERE asset_id=?", (stamp, intent.asset_id))
                    connection.execute(
                        "UPDATE video_deletion_journal SET state='cancelled',updated_at=?,error=? WHERE intent_id=?",
                        (stamp, "復元時に元動画・退避動画は不在でした。元の削除原因は確認できません", intent.intent_id),
                    )
                return VideoDeletionResult("skipped", intent.recording_id, "restored_deleted", "復元時削除済み。元の削除原因は不明です", journal_started=True, intent_id=intent.intent_id)
            self.repository.update_intent(intent.intent_id, "unlinked")
            record = self.repository.complete_deletion(intent.intent_id)
            return VideoDeletionResult("deleted", intent.recording_id, record.reason, "前回削除した動画の記録を回復しました", journal_started=True, intent_id=intent.intent_id)
        if not staged.exists():
            if not original.is_file() or fingerprint_file(original).sha256 != intent.sha256:
                raise ValueError("中断した動画の所在・同一性を確認できません")
            self.repository.update_intent(intent.intent_id, "cancelled", error="物理削除前に中断したため中止しました")
            return VideoDeletionResult("skipped", intent.recording_id, "recovered_cancelled", "元動画を保持して前回の削除を中止しました", journal_started=True, intent_id=intent.intent_id)
        if original.exists():
            raise ValueError("元動画と退避動画が両方あります。自動で上書き・削除しません")
        fingerprint = fingerprint_file(staged)
        if (fingerprint.sha256, fingerprint.size_bytes, fingerprint.mtime_ns) != (intent.sha256, intent.size_bytes, intent.mtime_ns):
            raise ValueError("退避動画が削除journalの内容と一致しません")
        try:
            if intent.reason == "manual":
                raise _SkipDeletion("manual_interrupted", "手動削除の確認は再利用しません")
            self._check_eligibility(asset, intent.reason, self._now(), own_intent=intent.intent_id)
            if intent.reason == "youtube_uploaded":
                self._verify_upload_provenance(asset, fingerprint, intent.upload_id, intent.prepare_queue_id, intent.metadata.get("provenance"))
        except _SkipDeletion as exc:
            os.rename(staged, original)
            self.repository.update_intent(intent.intent_id, "cancelled", error=str(exc))
            return VideoDeletionResult("skipped", intent.recording_id, exc.reason, "退避動画を戻し、前回の削除を中止しました", journal_started=True, intent_id=intent.intent_id)
        with self.protection.retained_backup("pre-video-recovery") as backup:
            with closing(connect_history_database(self.repository.database_path)) as connection, connection:
                connection.execute("UPDATE video_deletion_journal SET backup_path = ? WHERE intent_id = ?", (str(backup.path), intent.intent_id))
            self.repository.update_intent(intent.intent_id, "staged")
            return self._unlink_staged(intent, asset, self._now(), backup)

    def _check_eligibility(
        self, asset: VideoAsset, reason: str, now: datetime, *, allow_protected: bool = False,
        own_intent: str | None = None,
    ) -> None:
        current = self.repository.get_current_asset(asset.recording_id)
        if current is None or current.asset_id != asset.asset_id or current.generation != asset.generation or current.state == "deleted":
            raise _SkipDeletion("source_mismatch", "現在の元動画の世代が一致しません")
        if self.repository.get_protection(asset.recording_id).protected and not (reason == "manual" and allow_protected):
            raise _SkipDeletion("protected", "この動画は削除対象外に設定されています")
        if self.repository.list_uses(asset.asset_id, discard_dead=True):
            raise _SkipDeletion("source_in_use", "元動画が使用中です", retryable=True)
        with closing(connect_history_database(self.repository.database_path)) as connection:
            row = connection.execute("SELECT * FROM recordings WHERE recording_id = ?", (asset.recording_id,)).fetchone()
            if row is None or row["output_path"] != asset.path:
                raise _SkipDeletion("source_mismatch", "録画履歴の元動画参照が一致しません")
            if row["state"] not in {"completed", "failed"} or (row["state"] == "failed" and not row["ended_at"]):
                raise _SkipDeletion("recording_active", "録画の終了を確認できません", retryable=True)
            if connection.execute("SELECT 1 FROM card_analysis_jobs WHERE recording_id = ? AND state = 'running'", (asset.recording_id,)).fetchone():
                raise _SkipDeletion("source_in_use", "カード解析が元動画を使用中です", retryable=True)
            if connection.execute("SELECT 1 FROM youtube_uploads WHERE recording_id = ? AND state IN ('preparing','uploading')", (asset.recording_id,)).fetchone():
                raise _SkipDeletion("source_in_use", "動画の投稿処理が実行中です", retryable=True)
            if connection.execute("SELECT 1 FROM video_deletion_journal WHERE recording_id = ? AND state IN ('intent','staged','unlinked') AND intent_id <> ?", (asset.recording_id, own_intent or "")).fetchone():
                raise _SkipDeletion("recovery_pending", "前回の動画削除が未完了です", retryable=True)
            other_paths = connection.execute("SELECT output_path FROM recordings WHERE recording_id <> ?", (asset.recording_id,)).fetchall()
        path = safe_file_path(self.paths.recordings, asset.path, must_exist=False)
        if any((self.paths.recordings / str(item[0])).resolve() == path for item in other_paths):
            raise _SkipDeletion("shared_reference", "複数の履歴が同じ元動画を参照しています")
        if any(item.recording_id == asset.recording_id and item.state == UploadQueueState.PROCESSING for item in UploadQueueStore(self.paths).list()):
            raise _SkipDeletion("source_in_use", "投稿用動画の準備が元動画を使用中です", retryable=True)
        if reason == "manual":
            return
        try:
            settings = self._settings()
        except Exception as exc:
            raise _SkipDeletion("settings_invalid", f"保存済み設定を確認できません: {exc}") from exc
        if reason == "youtube_uploaded" and not settings.upload_delete_source_after_upload:
            raise _SkipDeletion("disabled", "YouTube投稿後の動画削除は無効です")
        if reason == "retention":
            if settings.recording_retention_seconds == 0:
                raise _SkipDeletion("disabled", "動画の保存期限は無制限です")
            try:
                stamp = _baseline(row["started_at"], row["created_at"])
            except ValueError as exc:
                raise _SkipDeletion("unknown_date", "録画日時を確認できません") from exc
            if (now - stamp).total_seconds() <= settings.recording_retention_seconds:
                raise _SkipDeletion("within_retention", "動画は保存期限内です")
        if reason == "capacity":
            try:
                _baseline(row["started_at"], row["created_at"])
            except ValueError as exc:
                raise _SkipDeletion("unknown_date", "録画日時を確認できません") from exc
            if settings.recording_storage_max_bytes == 0 or self.total_original_bytes() <= settings.recording_storage_max_bytes:
                raise _SkipDeletion("within_capacity", "動画は保存容量の上限内です")

    def _check_fingerprint(self, asset: VideoAsset, fingerprint: FileFingerprint, expected_sha256: str | None) -> None:
        if asset.state in {"deleted", "unavailable"}:
            raise _SkipDeletion("source_unavailable", "元動画を確認できません")
        if ((asset.size_bytes is not None and asset.size_bytes != fingerprint.size_bytes)
                or (not asset.sha256 and asset.mtime_ns is not None and asset.mtime_ns != fingerprint.mtime_ns)
                or (asset.sha256 and asset.sha256 != fingerprint.sha256)
                or (expected_sha256 is not None and expected_sha256 != fingerprint.sha256)):
            raise _SkipDeletion("source_mismatch", "元動画の内容が登録・投稿時と一致しません")

    def _verify_backup(self, backup: BackupInfo) -> None:
        if self.protection.inspect_backup(backup.path).sha256 != backup.sha256:
            raise _SkipDeletion("backup_changed", "削除前バックアップを保持・検証できません", retryable=True)

    def _verify_upload_provenance(
        self, asset: VideoAsset, fingerprint: FileFingerprint, upload_id: str | None,
        prepare_queue_id: str | None, provenance: object,
    ) -> None:
        try:
            if not isinstance(provenance, dict) or provenance.get("schema") != "mdrl-upload-cleanup-v1":
                raise ValueError("投稿した動画の出自情報がありません")
            expected = {"upload_id": upload_id, "queue_id": prepare_queue_id, "recording_id": asset.recording_id,
                        "asset_id": asset.asset_id, "generation": asset.generation}
            if not upload_id or not prepare_queue_id or any(provenance.get(key) != value for key, value in expected.items()):
                raise ValueError("投稿と元動画の参照が一致しません")
            with closing(connect_history_database(self.repository.database_path)) as connection:
                upload = connection.execute("SELECT * FROM youtube_uploads WHERE upload_id = ?", (upload_id,)).fetchone()
            if (upload is None or upload["state"] != "completed" or upload["recording_id"] != asset.recording_id
                    or upload["prepare_queue_id"] != prepare_queue_id or not upload["video_id"] or not upload["watch_url"]
                    or upload["video_id"] != provenance.get("video_id") or upload["watch_url"] != provenance.get("watch_url")):
                raise ValueError("YouTube投稿成功の保存結果を確認できません")
            source = provenance.get("source")
            if not isinstance(source, dict) or (source.get("path"), source.get("size_bytes"), source.get("sha256")) != (asset.path, fingerprint.size_bytes, fingerprint.sha256):
                raise ValueError("投稿時の元動画と現在の動画が一致しません")
            checked: dict[str, Path] = {}
            for key, root in (("export", self.paths.exports), ("manifest", self.paths.queue)):
                member = provenance.get(key)
                if not isinstance(member, dict):
                    raise ValueError("投稿したMP4・manifestの出自情報が不正です")
                path = safe_file_path(root, member.get("path"))
                actual = fingerprint_file(path)
                if (actual.size_bytes, actual.sha256) != (member.get("size_bytes"), member.get("sha256")):
                    raise ValueError("投稿したMP4・manifestが差し替わっています")
                checked[key] = path
            queued = UploadQueueStore(self.paths).get(prepare_queue_id)
            if (queued is None or queued.state != UploadQueueState.COMPLETED or queued.recording_id != asset.recording_id
                    or queued.export_path is None or (self.paths.root / queued.export_path).resolve() != checked["export"]
                    or queued.manifest_path is None or (self.paths.root / queued.manifest_path).resolve() != checked["manifest"]):
                raise ValueError("準備キューと投稿した動画の対応が一致しません")
            from .upload_manifest import validate_upload_manifest

            manifest = json.loads(checked["manifest"].read_text(encoding="utf-8"))
            validate_upload_manifest(manifest)
            if manifest["queue_id"] != prepare_queue_id or manifest["recording_id"] != asset.recording_id:
                raise ValueError("manifestの投稿参照が一致しません")
            for key, target in (("source", self.paths.recordings / asset.path), ("export", checked["export"])):
                member = manifest[key]
                root_path = safe_file_path(self.paths.root, member["path"], must_exist=False, allow_reserved=True)
                expected_member = provenance[key]
                if (root_path != target.resolve() or member["size_bytes"] != expected_member["size_bytes"]
                        or member["sha256"] != expected_member["sha256"]):
                    raise ValueError("manifestの動画同一性が一致しません")
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            raise _SkipDeletion("provenance_mismatch", f"投稿した動画の出自を確認できません: {exc}") from exc

    def total_original_bytes(self) -> int:
        """リンク先へ辿らず、保護・未登録・退避中も含む実サイズを数える。"""
        total = 0
        root = self.paths.recordings
        if not root.is_dir():
            return 0
        for folder, directories, filenames in os.walk(root, followlinks=False):
            parent = Path(folder)
            directories[:] = [name for name in directories
                              if not _is_link(parent / name) and name.casefold() not in {"preroll", "exports"}]
            for name in filenames:
                path = parent / name
                if path.suffix.casefold() not in VIDEO_SUFFIXES or _is_link(path):
                    continue
                try:
                    total += path.stat().st_size
                except OSError:
                    raise _SkipDeletion("capacity_unknown", "動画の使用容量を確認できません")
        return total


def _assert_file_not_in_use(path: Path) -> None:
    status = query_file_use(path)
    if not status.known:
        raise _SkipDeletion("file_use_unknown", status.message, retryable=True)
    if status.in_use:
        raise _SkipDeletion("source_in_use", status.message, retryable=True)


def _baseline(started_at: object, created_at: object) -> datetime:
    raw = started_at if started_at not in {None, ""} else created_at
    if not isinstance(raw, str):
        raise ValueError("録画日時がありません")
    stamp = datetime.fromisoformat(raw)
    if stamp.tzinfo is None:
        raise ValueError("録画日時にタイムゾーンがありません")
    return stamp.astimezone(timezone.utc)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _assert_no_links(root: Path, path: Path) -> None:
    current = path
    while True:
        if _is_link(current):
            raise _SkipDeletion("unsafe_path", "リンク・ジャンクションを含む動画は削除しません")
        if current == root:
            return
        if current.parent == current:
            raise _SkipDeletion("unsafe_path", "動画の保存先境界を確認できません")
        current = current.parent


def safe_file_path(root: Path, relative: object, *, must_exist: bool = True, allow_staging: bool = False, allow_reserved: bool = False) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise _SkipDeletion("unsafe_path", "動画の保存先が不正です")
    part = Path(relative)
    if part.is_absolute() or part.drive or ".." in part.parts:
        raise _SkipDeletion("unsafe_path", "保存領域の外側を参照する動画は削除しません")
    if STAGING_DIRECTORY in part.parts:
        if not allow_staging or len(part.parts) != 2 or part.parts[0] != STAGING_DIRECTORY or re.fullmatch(r"[0-9a-f]{32}\.pending", part.name) is None:
            raise _SkipDeletion("unsafe_staging", "退避動画の保存先が不正です")
    if not allow_reserved and any(value.casefold() in {"exports", "preroll"} for value in part.parts):
        raise _SkipDeletion("unsafe_path", "元動画以外の保存先を参照しています")
    selected_root = root.absolute()
    path = selected_root / part
    _assert_no_links(selected_root, path)
    resolved_root = selected_root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(resolved_root) or resolved == resolved_root:
        raise _SkipDeletion("unsafe_path", "動画の保存先境界を確認できません")
    if must_exist and not path.is_file():
        raise _SkipDeletion("source_missing", "元動画を確認できません")
    if path.is_file() and path.stat().st_nlink > 1:
        raise _SkipDeletion("shared_file", "複数の参照を持つファイルは削除しません")
    return resolved


def fingerprint_file(path: Path) -> FileFingerprint:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
            raise _SkipDeletion("source_changed", "動画の内容が確認中に変化しました")
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
        raise _SkipDeletion("source_changed", "動画の内容が確認中に変化しました")
    return FileFingerprint(before.st_size, before.st_mtime_ns, digest.hexdigest(), before.st_dev, before.st_ino)
