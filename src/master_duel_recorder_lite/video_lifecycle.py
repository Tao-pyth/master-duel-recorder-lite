"""元動画の同一性、保護、削除台帳を管理します。ファイルの削除は行いません。"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
from typing import Callable, Iterator
import uuid

from .history_database import (
    HISTORY_DATABASE_NAME,
    HistoryDatabaseError,
    connect_history_database,
    initialize_history_database,
)
from .runtime_paths import RuntimePaths
from .recording_lock import RecordingBusyError, RecordingLock


ACTIVE_INTENT_STATES = frozenset({"intent", "staged", "unlinked"})
DELETION_REASONS = frozenset({"capacity", "retention", "youtube_uploaded", "manual"})
DELETION_REASON_LABELS = {
    "capacity": "保存容量超過",
    "retention": "保存期限超過",
    "youtube_uploaded": "YouTubeアップロード完了後",
    "manual": "手動削除",
    "restored_deleted": "復元時削除済み",
}
_MAX_INTEGER = (1 << 63) - 1


class VideoLifecycleError(RuntimeError):
    """動画の管理状態を安全に確認・変更できない場合のエラーです。"""


@dataclass(frozen=True)
class VideoAsset:
    asset_id: str
    recording_id: str
    generation: int
    path: str
    size_bytes: int | None
    mtime_ns: int | None
    sha256: str
    time_basis: str
    state: str
    is_current: bool


@dataclass(frozen=True)
class VideoProtection:
    recording_id: str
    protected: bool
    revision: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class DeletionIntent:
    intent_id: str
    recording_id: str
    asset_id: str
    generation: int
    reason: str
    state: str
    original_path: str
    staged_path: str
    size_bytes: int
    mtime_ns: int
    sha256: str
    backup_path: str
    upload_id: str | None
    prepare_queue_id: str | None
    requested_at: datetime
    updated_at: datetime
    error: str
    metadata: dict[str, object]


@dataclass(frozen=True)
class VideoDeletionRecord:
    deletion_id: str
    recording_id: str
    asset_id: str
    generation: int
    reason: str
    deleted_at: datetime
    original_path: str
    size_bytes: int | None
    sha256: str
    cause_known: bool
    upload_id: str | None
    prepare_queue_id: str | None
    metadata: dict[str, object]


@dataclass(frozen=True)
class UploadCleanupRequest:
    request_id: str
    recording_id: str
    asset_id: str
    generation: int
    upload_id: str
    prepare_queue_id: str | None
    state: str
    provenance: dict[str, object]
    last_skip_reason: str
    attempts: int
    last_attempt_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class VideoAssetUse:
    use_id: str
    asset_id: str
    owner_id: str
    purpose: str
    process_id: int
    acquired_at: datetime


@dataclass(frozen=True)
class RecordingVideoState:
    recording_id: str
    asset: VideoAsset | None
    protection: VideoProtection
    deletion: VideoDeletionRecord | None
    state: str
    reason: str | None
    cause_known: bool
    message: str
    pending_intent: bool
    cleanup_pending: bool

    @property
    def protected(self) -> bool:
        return self.protection.protected


class VideoLifecycleRepository:
    def __init__(
        self, *, database_path: Path, recordings_root: Path,
        migration_backup_factory: Callable[[int], Path] | None = None,
        operation_lock_path: Path | None = None,
    ) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.recordings_root = recordings_root.expanduser().resolve()
        self.operation_lock_path = (operation_lock_path or self.recordings_root.parent / "video-operations.lock").expanduser().resolve()
        try:
            initialize_history_database(
                self.database_path, recordings_root=self.recordings_root,
                migration_backup_factory=migration_backup_factory,
            )
        except (OSError, HistoryDatabaseError) as exc:
            raise VideoLifecycleError(str(exc)) from exc

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> VideoLifecycleRepository:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(database_path=paths.db / HISTORY_DATABASE_NAME, recordings_root=paths.recordings,
                   operation_lock_path=paths.data / "video-operations.lock")

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        try:
            with closing(connect_history_database(self.database_path)) as connection:
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                    with connection:
                        yield connection
                else:
                    yield connection
        except (sqlite3.Error, HistoryDatabaseError, OverflowError) as exc:
            raise VideoLifecycleError(f"動画管理DBの操作に失敗しました: {exc}") from exc

    def get_current_asset(self, recording_id: str) -> VideoAsset | None:
        identifier = _text(recording_id, "recording_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM recording_video_assets WHERE recording_id=? AND is_current=1",
                (identifier,),
            ).fetchone()
        return _asset(row) if row is not None else None

    def get_asset(self, asset_id: str) -> VideoAsset | None:
        identifier = _text(asset_id, "asset_id")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM recording_video_assets WHERE asset_id=?", (identifier,)).fetchone()
        return _asset(row) if row is not None else None

    def ensure_current_asset(self, recording_id: str) -> VideoAsset:
        identifier = _text(recording_id, "recording_id")
        with self._connection(write=True) as connection:
            recording = _recording(connection, identifier)
            row = connection.execute(
                "SELECT * FROM recording_video_assets WHERE recording_id=? AND is_current=1", (identifier,),
            ).fetchone()
            if row is not None:
                result = _asset(row)
                if result.path != _relative_path(recording["output_path"]):
                    raise VideoLifecycleError("録画履歴と動画実体の参照が異なります。再関連付けが必要です")
                return result
            if connection.execute("SELECT 1 FROM recording_video_assets WHERE recording_id=?", (identifier,)).fetchone():
                raise VideoLifecycleError("以前の動画実体があります。新しい動画の関連付けを確認してください")
            now = _now()
            asset_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO recording_video_assets "
                "(asset_id,recording_id,generation,is_current,path,size_bytes,time_basis,state,created_at,updated_at) "
                "VALUES (?,?,1,1,?,?,'recording_start','unverified',?,?)",
                (asset_id, identifier, _relative_path(recording["output_path"]), recording["size_bytes"], now, now),
            )
            return _asset(_require_asset(connection, asset_id))

    def set_verified_asset(self, asset_id: str, *, size_bytes: int, mtime_ns: int, sha256: str) -> VideoAsset:
        identifier = _text(asset_id, "asset_id")
        size, modified, digest = _integer(size_bytes, "size_bytes"), _integer(mtime_ns, "mtime_ns"), _sha(sha256)
        with self._connection(write=True) as connection:
            current = _require_asset(connection, identifier)
            _require_current(current)
            _no_intent(connection, identifier)
            if current["sha256"] and (
                current["size_bytes"], current["sha256"]
            ) != (size, digest):
                raise VideoLifecycleError("検証済み動画の内容が変わりました。別世代の動画として扱ってください")
            connection.execute(
                "UPDATE recording_video_assets SET size_bytes=?,mtime_ns=?,sha256=?,state='present',updated_at=? "
                "WHERE asset_id=?", (size, modified, digest, _now(), identifier),
            )
            return _asset(_require_asset(connection, identifier))

    def get_protection(self, recording_id: str) -> VideoProtection:
        identifier = _text(recording_id, "recording_id")
        with self._connection() as connection:
            _recording(connection, identifier)
            return _protection(connection, identifier)

    def set_protection(
        self, recording_id: str, protected: bool, *, expected_revision: int | None = None,
    ) -> VideoProtection:
        identifier = _text(recording_id, "recording_id")
        if not isinstance(protected, bool):
            raise VideoLifecycleError("protectedは真偽値で指定してください")
        if expected_revision is not None:
            _integer(expected_revision, "expected_revision")
        with self._connection(write=True) as connection:
            _recording(connection, identifier)
            current = _protection(connection, identifier)
            if expected_revision is not None and current.revision != expected_revision:
                raise VideoLifecycleError("動画の保護設定が更新されています。再読込してください")
            if current.revision and current.protected == protected:
                return current
            now = _now()
            connection.execute(
                "INSERT INTO recording_video_protection (recording_id,protected,revision,created_at,updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(recording_id) DO UPDATE SET protected=excluded.protected,"
                "revision=excluded.revision,updated_at=excluded.updated_at",
                (identifier, int(protected), current.revision + 1,
                 current.created_at.isoformat() if current.created_at else now, now),
            )
            return _protection(connection, identifier)

    def deletion_for_asset(self, asset_id: str) -> VideoDeletionRecord | None:
        identifier = _text(asset_id, "asset_id")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM video_deletion_history WHERE asset_id=?", (identifier,)).fetchone()
        return _deletion(row) if row is not None else None

    def get_intent(self, intent_id: str) -> DeletionIntent | None:
        identifier = _text(intent_id, "intent_id")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM video_deletion_journal WHERE intent_id=?", (identifier,)).fetchone()
        return _intent(row) if row is not None else None

    def pending_intents(self) -> tuple[DeletionIntent, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM video_deletion_journal WHERE state IN ('intent','staged','unlinked') "
                "ORDER BY requested_at,intent_id",
            ).fetchall()
        return tuple(_intent(row) for row in rows)

    def create_intent(
        self, *, recording_id: str, asset_id: str, generation: int, reason: str,
        original_path: str, staged_path: str, size_bytes: int, mtime_ns: int,
        sha256: str, backup_path: str, upload_id: str | None = None,
        prepare_queue_id: str | None = None, metadata: dict[str, object] | None = None,
        allow_protected: bool = False,
    ) -> DeletionIntent:
        recording_id, asset_id = _text(recording_id, "recording_id"), _text(asset_id, "asset_id")
        generation = _integer(generation, "generation", minimum=1)
        reason = _choice(reason, DELETION_REASONS, "reason")
        original_path, staged_path = _relative_path(original_path), _relative_path(staged_path)
        if original_path == staged_path:
            raise VideoLifecycleError("退避先と元動画のパスは別にしてください")
        size_bytes, mtime_ns = _integer(size_bytes, "size_bytes"), _integer(mtime_ns, "mtime_ns")
        sha256, backup_path = _sha(sha256), _text(backup_path, "backup_path")
        upload_id, prepare_queue_id = _optional_text(upload_id, "upload_id"), _optional_text(prepare_queue_id, "prepare_queue_id")
        if not isinstance(allow_protected, bool) or (allow_protected and reason != "manual"):
            raise VideoLifecycleError("保護対象の削除許可は確認済みの手動操作だけで使用してください")
        payload = _json_object({} if metadata is None else metadata, "metadata")
        with self._connection(write=True) as connection:
            recording = _recording(connection, recording_id)
            current = _require_asset(connection, asset_id)
            _require_current(current)
            if (current["recording_id"], current["generation"], current["path"]) != (recording_id, generation, original_path):
                raise VideoLifecycleError("削除対象の動画実体・世代・パスが一致しません")
            if _relative_path(recording["output_path"]) != original_path:
                raise VideoLifecycleError("録画履歴の動画参照が変わっています")
            if current["state"] != "present" or (
                current["size_bytes"], current["mtime_ns"], current["sha256"]
            ) != (size_bytes, mtime_ns, sha256):
                raise VideoLifecycleError("削除対象の検証値が動画実体と一致しません")
            if _protection(connection, recording_id).protected and not allow_protected:
                raise VideoLifecycleError("削除対象外に設定されている動画です")
            if recording["state"] not in {"completed", "failed"}:
                raise VideoLifecycleError("録画が完了していません")
            if recording["state"] == "failed":
                if not recording["ended_at"]:
                    raise VideoLifecycleError("失敗した録画の終了を確認できません")
                _timestamp(recording["ended_at"])
            if connection.execute(
                "SELECT 1 FROM video_deletion_journal WHERE recording_id=? AND asset_id<>? "
                "AND state IN ('intent','staged','unlinked')", (recording_id, asset_id),
            ).fetchone():
                raise VideoLifecycleError("同じ録画の別世代の削除台帳が未完了です")
            if connection.execute(
                "SELECT 1 FROM card_analysis_jobs WHERE recording_id=? AND state='running'", (recording_id,),
            ).fetchone():
                raise VideoLifecycleError("カード解析が実行中です")
            uses = connection.execute("SELECT * FROM video_asset_uses WHERE asset_id=?", (asset_id,)).fetchall()
            for use in uses:
                if _process_alive(use["process_id"]) is False:
                    connection.execute("DELETE FROM video_asset_uses WHERE use_id=?", (use["use_id"],))
                else:
                    raise VideoLifecycleError("動画が使用中、または利用元の終了を確認できません")
            if reason == "youtube_uploaded":
                _completed_upload(connection, recording_id, upload_id, prepare_queue_id)
            existing = connection.execute(
                "SELECT * FROM video_deletion_journal WHERE asset_id=? AND state IN ('intent','staged','unlinked')",
                (asset_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing[key] for key in (
                    "generation", "reason", "original_path", "staged_path", "size_bytes", "mtime_ns", "sha256",
                    "backup_path", "upload_id", "prepare_queue_id", "metadata_json",
                )) != (generation, reason, original_path, staged_path, size_bytes, mtime_ns, sha256,
                       backup_path, upload_id, prepare_queue_id, payload):
                    raise VideoLifecycleError("同じ動画の削除処理が既に開始されています")
                return _intent(existing)
            if connection.execute("SELECT 1 FROM video_deletion_history WHERE asset_id=?", (asset_id,)).fetchone():
                raise VideoLifecycleError("削除済みの動画です")
            now, identifier = _now(), uuid.uuid4().hex
            connection.execute(
                "INSERT INTO video_deletion_journal (intent_id,recording_id,asset_id,generation,reason,state,"
                "original_path,staged_path,size_bytes,mtime_ns,sha256,backup_path,upload_id,prepare_queue_id,"
                "requested_at,updated_at,error,metadata_json) VALUES (?,?,?,?,?,'intent',?,?,?,?,?,?,?,?,?,?,'',?)",
                (identifier, recording_id, asset_id, generation, reason, original_path, staged_path,
                 size_bytes, mtime_ns, sha256, backup_path, upload_id, prepare_queue_id, now, now, payload),
            )
            return _intent(_require_intent(connection, identifier))

    def update_intent(self, intent_id: str, state: str, error: str = "") -> DeletionIntent:
        identifier = _text(intent_id, "intent_id")
        state = _choice(state, {"intent", "staged", "unlinked", "cancelled", "failed"}, "state")
        error = _string(error, "error")
        with self._connection(write=True) as connection:
            row = _require_intent(connection, identifier)
            allowed = {
                "intent": {"intent", "staged", "cancelled", "failed"},
                "staged": {"staged", "unlinked", "cancelled", "failed"},
                "unlinked": {"unlinked"}, "cancelled": {"cancelled"}, "failed": {"failed"},
                "completed": set(),
            }
            if state not in allowed[row["state"]]:
                raise VideoLifecycleError("削除台帳の状態を戻せません。削除成功後は完了処理で回復してください")
            connection.execute(
                "UPDATE video_deletion_journal SET state=?,error=?,updated_at=? WHERE intent_id=?",
                (state, error, _now(), identifier),
            )
            return _intent(_require_intent(connection, identifier))

    def complete_deletion(self, intent_id: str) -> VideoDeletionRecord:
        identifier = _text(intent_id, "intent_id")
        with self._connection(write=True) as connection:
            intent = _intent(_require_intent(connection, identifier))
            existing = connection.execute(
                "SELECT * FROM video_deletion_history WHERE asset_id=? AND generation=?", (intent.asset_id, intent.generation),
            ).fetchone()
            if intent.state == "completed":
                if existing is None:
                    raise VideoLifecycleError("削除完了の記録が欠けています")
                return _deletion(existing)
            if intent.state != "unlinked":
                raise VideoLifecycleError("物理削除の成功を記録してから動画依存情報を削除してください")
            current = _require_asset(connection, intent.asset_id)
            if not current["is_current"] or (current["recording_id"], current["generation"]) != (intent.recording_id, intent.generation):
                raise VideoLifecycleError("削除台帳と現在の動画実体が一致しません")
            now = _now()
            if existing is None:
                connection.execute(
                    "INSERT INTO video_deletion_history (deletion_id,recording_id,asset_id,generation,reason,deleted_at,"
                    "original_path,size_bytes,sha256,cause_known,upload_id,prepare_queue_id,metadata_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)",
                    (identifier, intent.recording_id, intent.asset_id, intent.generation, intent.reason, now,
                     intent.original_path, intent.size_bytes, intent.sha256, intent.upload_id,
                     intent.prepare_queue_id, _json_object(intent.metadata, "metadata")),
                )
            # 正本には触れず、同じ動画に所属する観測と旧来のマーカーだけを消します。
            self._purge_video_evidence(connection, intent, now)
            connection.execute(
                "UPDATE recording_video_assets SET state='deleted',updated_at=? WHERE asset_id=?", (now, intent.asset_id),
            )
            connection.execute(
                "UPDATE video_deletion_journal SET state='completed',error='',updated_at=? WHERE intent_id=?", (now, identifier),
            )
            row = connection.execute("SELECT * FROM video_deletion_history WHERE asset_id=?", (intent.asset_id,)).fetchone()
            return _deletion(row)

    @staticmethod
    def _purge_video_evidence(connection: sqlite3.Connection, intent: DeletionIntent, now: str) -> None:
        connection.execute(
            "DELETE FROM card_observations WHERE event_id IN (SELECT event_id FROM card_observation_links "
            "WHERE video_asset_id=? AND video_generation=?) OR (recording_id=? AND NOT EXISTS "
            "(SELECT 1 FROM card_observation_links WHERE card_observation_links.event_id=card_observations.event_id))",
            (intent.asset_id, intent.generation, intent.recording_id),
        )
        connection.execute(
            "DELETE FROM duel_events WHERE event_id IN (SELECT event_id FROM duel_event_sources "
            "WHERE video_asset_id=? AND video_generation=?) OR (recording_id=? AND NOT EXISTS "
            "(SELECT 1 FROM duel_event_sources WHERE duel_event_sources.event_id=duel_events.event_id))",
            (intent.asset_id, intent.generation, intent.recording_id),
        )
        jobs = connection.execute(
            "SELECT jobs.* FROM card_analysis_jobs jobs LEFT JOIN card_analysis_job_sources sources USING(job_id) "
            "WHERE (sources.video_asset_id=? AND sources.video_generation=?) "
            "OR (jobs.recording_id=? AND sources.job_id IS NULL)",
            (intent.asset_id, intent.generation, intent.recording_id),
        ).fetchall()
        for job in jobs:
            metadata = _object(job["result_metadata_json"], "解析結果metadata")
            metadata.pop("checkpoint", None)
            metadata["video_deleted"] = True
            active = job["state"] in {"queued", "waiting", "running"}
            connection.execute(
                "UPDATE card_analysis_jobs SET state=?,error=?,result_metadata_json=?,updated_at=? WHERE job_id=?",
                ("cancelled" if active else job["state"], "元動画削除のため解析を中止しました" if active else job["error"],
                 _json_object(metadata, "解析結果metadata"), now, job["job_id"]),
            )
            connection.execute("UPDATE card_analysis_job_sources SET verified=0 WHERE job_id=?", (job["job_id"],))

    def request_cleanup(
        self, *, recording_id: str, asset_id: str, generation: int, upload_id: str,
        provenance: dict[str, object], skip_reason: str, prepare_queue_id: str | None = None,
    ) -> UploadCleanupRequest:
        recording_id, asset_id, upload_id = (
            _text(recording_id, "recording_id"), _text(asset_id, "asset_id"), _text(upload_id, "upload_id"),
        )
        generation = _integer(generation, "generation", minimum=1)
        prepare_queue_id = _optional_text(prepare_queue_id, "prepare_queue_id")
        payload, skip_reason = _json_object(provenance, "provenance"), _text(skip_reason, "skip_reason")
        with self._connection(write=True) as connection:
            _completed_upload(connection, recording_id, upload_id, prepare_queue_id)
            row = connection.execute(
                "SELECT * FROM upload_cleanup_requests WHERE upload_id=? AND asset_id=? AND generation=?",
                (upload_id, asset_id, generation),
            ).fetchone()
            if row is not None:
                if (row["recording_id"], row["prepare_queue_id"], row["provenance_json"]) != (recording_id, prepare_queue_id, payload):
                    raise VideoLifecycleError("既存の再試行要求と出自が一致しません")
                if row["state"] != "pending":
                    # 遅れた成功通知でも、終了した要求を新しい実行要求に戻しません。
                    return _request(row)
            current = _require_asset(connection, asset_id)
            _require_current(current)
            if (current["recording_id"], current["generation"]) != (recording_id, generation):
                raise VideoLifecycleError("再試行要求と動画実体が一致しません")
            _no_intent(connection, asset_id)
            if _protection(connection, recording_id).protected:
                raise VideoLifecycleError("保護対象の動画から再試行要求を作成できません")
            if connection.execute("SELECT 1 FROM video_deletion_history WHERE asset_id=?", (asset_id,)).fetchone():
                raise VideoLifecycleError("削除済み動画から再試行要求を作成できません")
            if row is not None:
                connection.execute(
                    "UPDATE upload_cleanup_requests SET last_skip_reason=?,updated_at=? WHERE request_id=?",
                    (skip_reason, _now(), row["request_id"]),
                )
                return _request(_require_request(connection, row["request_id"]))
            now, identifier = _now(), uuid.uuid4().hex
            connection.execute(
                "INSERT INTO upload_cleanup_requests (request_id,recording_id,asset_id,generation,upload_id,"
                "prepare_queue_id,state,provenance_json,last_skip_reason,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,'pending',?,?,?,?)",
                (identifier, recording_id, asset_id, generation, upload_id, prepare_queue_id, payload, skip_reason, now, now),
            )
            return _request(_require_request(connection, identifier))

    def pending_cleanup_requests(self) -> tuple[UploadCleanupRequest, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM upload_cleanup_requests WHERE state='pending' ORDER BY created_at,request_id",
            ).fetchall()
        return tuple(_request(row) for row in rows)

    def get_cleanup_request(self, request_id: str) -> UploadCleanupRequest | None:
        identifier = _text(request_id, "request_id")
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM upload_cleanup_requests WHERE request_id=?", (identifier,)).fetchone()
        return _request(row) if row is not None else None

    def update_cleanup_request(
        self, request_id: str, *, state: str | None = None, skip_reason: str = "", increment_attempts: bool = False,
    ) -> UploadCleanupRequest:
        identifier, skip_reason = _text(request_id, "request_id"), _string(skip_reason, "skip_reason")
        if state is not None:
            state = _choice(state, {"pending", "completed", "cancelled"}, "state")
        if not isinstance(increment_attempts, bool):
            raise VideoLifecycleError("increment_attemptsは真偽値で指定してください")
        with self._connection(write=True) as connection:
            current = _require_request(connection, identifier)
            target = state or current["state"]
            if current["state"] != "pending" and target != current["state"]:
                raise VideoLifecycleError("完了・中止済みの再試行要求を再実行できません")
            if current["state"] != "pending":
                return _request(current)
            now = _now()
            connection.execute(
                "UPDATE upload_cleanup_requests SET state=?,last_skip_reason=?,attempts=attempts+?,"
                "last_attempt_at=?,updated_at=? WHERE request_id=?",
                (target, skip_reason, int(increment_attempts), now if increment_attempts else current["last_attempt_at"], now, identifier),
            )
            return _request(_require_request(connection, identifier))

    def register_use(
        self, *, asset_id: str, owner_id: str, purpose: str, process_id: int | None = None,
    ) -> VideoAssetUse:
        asset_id, owner_id, purpose = _text(asset_id, "asset_id"), _text(owner_id, "owner_id"), _text(purpose, "purpose")
        pid = _integer(os.getpid() if process_id is None else process_id, "process_id")
        try:
            # 削除サービスと同じOSロックで、新しい利用の登録が削除判定を追い越すのを防ぎます。
            with RecordingLock.acquire(self.operation_lock_path, recording_id=f"video-use:{owner_id}"):
                with self._connection(write=True) as connection:
                    _require_current(_require_asset(connection, asset_id))
                    _no_intent(connection, asset_id)
                    identifier, now = uuid.uuid4().hex, _now()
                    connection.execute(
                        "INSERT INTO video_asset_uses (use_id,asset_id,owner_id,purpose,process_id,acquired_at) VALUES (?,?,?,?,?,?)",
                        (identifier, asset_id, owner_id, purpose, pid, now),
                    )
                    return VideoAssetUse(identifier, asset_id, owner_id, purpose, pid, _timestamp(now))
        except (RecordingBusyError, OSError) as exc:
            raise VideoLifecycleError("動画の整理・管理処理が実行中のため、新しい利用を開始できません") from exc

    def release_use(self, use_id: str, *, owner_id: str | None = None, process_id: int | None = None) -> bool:
        identifier = _text(use_id, "use_id")
        if owner_id is not None:
            owner_id = _text(owner_id, "owner_id")
        if process_id is not None:
            process_id = _integer(process_id, "process_id")
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM video_asset_uses WHERE use_id=?", (identifier,)).fetchone()
            if row is None:
                return False
            if ((owner_id is not None and row["owner_id"] != owner_id)
                    or (process_id is not None and row["process_id"] != process_id)):
                raise VideoLifecycleError("他の処理の動画利用を解除できません")
            connection.execute("DELETE FROM video_asset_uses WHERE use_id=?", (identifier,))
            return True

    def list_uses(self, asset_id: str, *, discard_dead: bool = False) -> tuple[VideoAssetUse, ...]:
        identifier = _text(asset_id, "asset_id")
        if not isinstance(discard_dead, bool):
            raise VideoLifecycleError("discard_deadは真偽値で指定してください")
        with self._connection(write=discard_dead) as connection:
            _require_asset(connection, identifier)
            rows = connection.execute(
                "SELECT * FROM video_asset_uses WHERE asset_id=? ORDER BY acquired_at,use_id", (identifier,),
            ).fetchall()
            retained = []
            for row in rows:
                # 経過時間では失効させません。PIDが生存・確認不可なら保守的に残します。
                if discard_dead and _process_alive(row["process_id"]) is False:
                    connection.execute("DELETE FROM video_asset_uses WHERE use_id=?", (row["use_id"],))
                else:
                    retained.append(_use(row))
            return tuple(retained)

    def get_recording_video_state(self, recording_id: str) -> RecordingVideoState:
        identifier = _text(recording_id, "recording_id")
        with self._connection() as connection:
            _recording(connection, identifier)
            protection = _protection(connection, identifier)
            row = connection.execute(
                "SELECT * FROM recording_video_assets WHERE recording_id=? AND is_current=1", (identifier,),
            ).fetchone()
            asset = _asset(row) if row is not None else None
            deletion_row = connection.execute(
                "SELECT * FROM video_deletion_history WHERE asset_id=?", (asset.asset_id,),
            ).fetchone() if asset else None
            deletion = _deletion(deletion_row) if deletion_row is not None else None
            pending = bool(connection.execute(
                "SELECT 1 FROM video_deletion_journal WHERE recording_id=? AND state IN ('intent','staged','unlinked')",
                (identifier,),
            ).fetchone())
            retry = bool(connection.execute(
                "SELECT 1 FROM upload_cleanup_requests WHERE recording_id=? AND state='pending'", (identifier,),
            ).fetchone())
        state = asset.state if asset else "unregistered"
        reason, cause_known = (deletion.reason, deletion.cause_known) if deletion else (None, False)
        message = DELETION_REASON_LABELS[reason] if reason else "削除原因の記録なし" if state == "deleted" else "未検証"
        if deletion or state == "deleted":
            state = "deleted"
        elif asset:
            try:
                # 保存先が確認できない場合は、ファイル不在を削除済みと推定しません。
                self.recordings_root.stat()
                actual = (self.recordings_root / asset.path).stat()
            except FileNotFoundError:
                try:
                    self.recordings_root.stat()
                except OSError:
                    state, message = "unavailable", "確認不可"
                else:
                    state, message = "missing", "動画が見つかりません。削除原因は不明です"
            except OSError:
                state, message = "unavailable", "確認不可"
            else:
                if state == "present" and (
                    (asset.size_bytes is not None and actual.st_size != asset.size_bytes)
                    or (asset.mtime_ns is not None and actual.st_mtime_ns != asset.mtime_ns)
                ):
                    state, message = "unverified", "動画が変更されています。同一性の確認が必要です"
                else:
                    message = "動画あり" if state == "present" else "動画の同一性は未検証"
        return RecordingVideoState(identifier, asset, protection, deletion, state, reason, cause_known, message, pending, retry)


def _recording(connection: sqlite3.Connection, recording_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM recordings WHERE recording_id=?", (recording_id,)).fetchone()
    if row is None:
        raise VideoLifecycleError("録画履歴が見つかりません")
    return row


def _require_asset(connection: sqlite3.Connection, asset_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM recording_video_assets WHERE asset_id=?", (asset_id,)).fetchone()
    if row is None:
        raise VideoLifecycleError("動画実体が見つかりません")
    return row


def _require_intent(connection: sqlite3.Connection, intent_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM video_deletion_journal WHERE intent_id=?", (intent_id,)).fetchone()
    if row is None:
        raise VideoLifecycleError("削除台帳が見つかりません")
    return row


def _require_request(connection: sqlite3.Connection, request_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM upload_cleanup_requests WHERE request_id=?", (request_id,)).fetchone()
    if row is None:
        raise VideoLifecycleError("投稿後削除の再試行要求が見つかりません")
    return row


def _require_current(row: sqlite3.Row) -> None:
    if not row["is_current"] or row["state"] == "deleted":
        raise VideoLifecycleError("現在の未削除動画ではありません")


def _no_intent(connection: sqlite3.Connection, asset_id: str) -> None:
    if connection.execute(
        "SELECT 1 FROM video_deletion_journal WHERE asset_id=? AND state IN ('intent','staged','unlinked')", (asset_id,),
    ).fetchone():
        raise VideoLifecycleError("動画の削除処理が未完了です")


def _completed_upload(
    connection: sqlite3.Connection, recording_id: str, upload_id: str | None, prepare_queue_id: str | None,
) -> None:
    row = connection.execute("SELECT * FROM youtube_uploads WHERE upload_id=?", (upload_id,)).fetchone()
    if (row is None or row["state"] != "completed" or not row["video_id"] or not row["watch_url"]
            or row["recording_id"] != recording_id or row["prepare_queue_id"] != prepare_queue_id):
        raise VideoLifecycleError("投稿成功の保存・URL・動画・準備参照を確認できません")
    _text(row["video_id"], "video_id")
    _text(row["watch_url"], "watch_url")


def _protection(connection: sqlite3.Connection, recording_id: str) -> VideoProtection:
    row = connection.execute("SELECT * FROM recording_video_protection WHERE recording_id=?", (recording_id,)).fetchone()
    if row is None:
        return VideoProtection(recording_id, False, 0, None, None)
    return VideoProtection(recording_id, _boolean(row["protected"], "protected"),
                           _integer(row["revision"], "revision", minimum=1),
                           _timestamp(row["created_at"]), _timestamp(row["updated_at"]))


def _asset(row: sqlite3.Row) -> VideoAsset:
    _timestamp(row["created_at"])
    _timestamp(row["updated_at"])
    return VideoAsset(
        _text(row["asset_id"], "asset_id"), _text(row["recording_id"], "recording_id"),
        _integer(row["generation"], "generation", minimum=1), _relative_path(row["path"]),
        _optional_integer(row["size_bytes"], "size_bytes"), _optional_integer(row["mtime_ns"], "mtime_ns"),
        _sha(row["sha256"], allow_empty=True), _text(row["time_basis"], "time_basis"),
        _choice(row["state"], {"unverified", "present", "deleted", "unavailable"}, "state"),
        _boolean(row["is_current"], "is_current"),
    )


def _intent(row: sqlite3.Row) -> DeletionIntent:
    return DeletionIntent(
        _text(row["intent_id"], "intent_id"), _text(row["recording_id"], "recording_id"),
        _text(row["asset_id"], "asset_id"), _integer(row["generation"], "generation", minimum=1),
        _choice(row["reason"], DELETION_REASONS, "reason"),
        _choice(row["state"], ACTIVE_INTENT_STATES | {"completed", "cancelled", "failed"}, "state"),
        _relative_path(row["original_path"]), _relative_path(row["staged_path"]),
        _integer(row["size_bytes"], "size_bytes"), _integer(row["mtime_ns"], "mtime_ns"),
        _sha(row["sha256"]), _text(row["backup_path"], "backup_path"),
        _optional_text(row["upload_id"], "upload_id"), _optional_text(row["prepare_queue_id"], "prepare_queue_id"),
        _timestamp(row["requested_at"]), _timestamp(row["updated_at"]), _string(row["error"], "error"),
        _object(row["metadata_json"], "metadata"),
    )


def _deletion(row: sqlite3.Row) -> VideoDeletionRecord:
    reason = _choice(row["reason"], DELETION_REASONS | {"restored_deleted"}, "reason")
    cause_known = _boolean(row["cause_known"], "cause_known")
    if reason == "restored_deleted" and cause_known:
        raise VideoLifecycleError("復元時削除済みの元の原因を推定できません")
    return VideoDeletionRecord(
        _text(row["deletion_id"], "deletion_id"), _text(row["recording_id"], "recording_id"),
        _text(row["asset_id"], "asset_id"), _integer(row["generation"], "generation", minimum=1), reason,
        _timestamp(row["deleted_at"]), _relative_path(row["original_path"]),
        _optional_integer(row["size_bytes"], "size_bytes"), _sha(row["sha256"], allow_empty=True), cause_known,
        _optional_text(row["upload_id"], "upload_id"), _optional_text(row["prepare_queue_id"], "prepare_queue_id"),
        _object(row["metadata_json"], "metadata"),
    )


def _request(row: sqlite3.Row) -> UploadCleanupRequest:
    return UploadCleanupRequest(
        _text(row["request_id"], "request_id"), _text(row["recording_id"], "recording_id"),
        _text(row["asset_id"], "asset_id"), _integer(row["generation"], "generation", minimum=1),
        _text(row["upload_id"], "upload_id"), _optional_text(row["prepare_queue_id"], "prepare_queue_id"),
        _choice(row["state"], {"pending", "completed", "cancelled"}, "state"),
        _object(row["provenance_json"], "provenance"), _string(row["last_skip_reason"], "last_skip_reason"),
        _integer(row["attempts"], "attempts"), _timestamp(row["last_attempt_at"]) if row["last_attempt_at"] else None,
        _timestamp(row["created_at"]), _timestamp(row["updated_at"]),
    )


def _use(row: sqlite3.Row) -> VideoAssetUse:
    return VideoAssetUse(_text(row["use_id"], "use_id"), _text(row["asset_id"], "asset_id"),
                         _text(row["owner_id"], "owner_id"), _text(row["purpose"], "purpose"),
                         _integer(row["process_id"], "process_id"), _timestamp(row["acquired_at"]))


def _text(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise VideoLifecycleError(f"{key}は空でない文字列で指定してください")
    return value.strip()


def _string(value: object, key: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise VideoLifecycleError(f"{key}は文字列で指定してください")
    return value


def _optional_text(value: object, key: str) -> str | None:
    return None if value is None else _text(value, key)


def _integer(value: object, key: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= _MAX_INTEGER:
        raise VideoLifecycleError(f"{key}は{minimum}以上の整数で指定してください")
    return value


def _optional_integer(value: object, key: str) -> int | None:
    return None if value is None else _integer(value, key)


def _boolean(value: object, key: str) -> bool:
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise VideoLifecycleError(f"{key}の真偽値が不正です")


def _choice(value: object, allowed: set[str] | frozenset[str], key: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise VideoLifecycleError(f"{key}が不正です")
    return value


def _sha(value: object, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise VideoLifecycleError("SHA-256は64桁の小文字16進数で指定してください")
    return value


def _relative_path(value: object) -> str:
    text = _text(value, "動画パス")
    posix, windows = PurePosixPath(text.replace("\\", "/")), PureWindowsPath(text)
    if (posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root
            or ".." in posix.parts or not posix.parts or any(":" in part for part in posix.parts)):
        raise VideoLifecycleError("動画の参照は保存先内の相対パスで指定してください")
    return posix.as_posix()


def _timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value, "日時"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise VideoLifecycleError("日時に有効なタイムゾーンが必要です") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json_object(value: object, key: str) -> str:
    if not isinstance(value, dict):
        raise VideoLifecycleError(f"{key}はJSON objectで指定してください")
    try:
        _validate_json(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise VideoLifecycleError(f"{key}のJSONが不正です") from exc


def _validate_json(value: object) -> None:
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON keys must be strings")
        for child in value.values():
            _validate_json(child)
    elif isinstance(value, list):
        for child in value:
            _validate_json(child)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError("JSON values must be portable")


def _object(value: object, key: str) -> dict[str, object]:
    try:
        result = json.loads(_string(value, key), parse_constant=_invalid_json_constant, object_pairs_hook=_unique_object)
    except (ValueError, TypeError, RecursionError) as exc:
        raise VideoLifecycleError(f"{key}のJSONが不正です") from exc
    _json_object(result, key)
    return result


def _invalid_json_constant(value: str) -> object:
    raise ValueError(f"JSON数値が不正です: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSONのキーが重複しています: {key}")
        result[key] = value
    return result


def _process_alive(process_id: int) -> bool | None:
    if process_id <= 0 or process_id > 0xFFFFFFFF:
        return None
    if process_id == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, process_id)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            exit_code = wintypes.DWORD()
            return exit_code.value == 259 if kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)) else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError, OverflowError):
        return None
    return True
