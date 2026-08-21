from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Iterator
import uuid

from .history_database import HISTORY_DATABASE_NAME, HistoryDatabaseError, connect_history_database
from .runtime_paths import RuntimePaths
from .upload_metadata import UploadMetadata, UploadPrivacy


class YouTubeUploadError(RuntimeError):
    """YouTubeアップロード状態を安全に保存できない場合のエラーです。"""


class YouTubeUploadState(str, Enum):
    WAITING = "waiting"
    PREPARING = "preparing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class YouTubeUpload:
    upload_id: str
    recording_id: str
    prepare_queue_id: str | None
    state: YouTubeUploadState
    metadata: UploadMetadata
    video_id: str | None
    watch_url: str | None
    attempts: int
    error: str | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "upload_id": self.upload_id,
            "recording_id": self.recording_id,
            "prepare_queue_id": self.prepare_queue_id,
            "state": self.state.value,
            "metadata": self.metadata.to_dict(),
            "video_id": self.video_id,
            "watch_url": self.watch_url,
            "attempts": self.attempts,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class YouTubeUploadRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> YouTubeUploadRepository:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def create(
        self,
        *,
        recording_id: str,
        metadata: UploadMetadata,
        force_new: bool = False,
    ) -> YouTubeUpload:
        identifier = _required_text(recording_id, "recording_id")
        if not force_new:
            completed = self.completed_for_recording(identifier)
            if completed is not None:
                raise YouTubeUploadError(
                    f"この録画は既にYouTubeアップロード済みです: {identifier}: {completed.watch_url}"
                )
        now = datetime.now(timezone.utc)
        upload_id = uuid.uuid4().hex
        try:
            with self._connection() as connection:
                recording = connection.execute(
                    "SELECT state FROM recordings WHERE recording_id = ?",
                    (identifier,),
                ).fetchone()
                if recording is None:
                    raise YouTubeUploadError(f"録画履歴が見つかりません: {identifier}")
                if recording["state"] != "completed":
                    raise YouTubeUploadError(
                        f"正常完了した録画だけをアップロードできます: {identifier}: {recording['state']}"
                    )
                connection.execute(
                    """
                    INSERT INTO youtube_uploads (
                        upload_id, recording_id, state, privacy, title, description,
                        tags_json, attempts, created_at, updated_at
                    ) VALUES (?, ?, 'waiting', ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        upload_id,
                        identifier,
                        metadata.privacy.value,
                        metadata.title,
                        metadata.description,
                        json.dumps(list(metadata.tags), ensure_ascii=False),
                        _format_datetime(now),
                        _format_datetime(now),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise YouTubeUploadError(f"YouTubeアップロード状態を作成できません: {exc}") from exc
        upload = self.get(upload_id)
        assert upload is not None
        return upload

    def get(self, upload_id: str) -> YouTubeUpload | None:
        identifier = _required_text(upload_id, "upload_id")
        with self._connection(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM youtube_uploads WHERE upload_id = ?",
                (identifier,),
            ).fetchone()
        return _upload_from_row(row) if row is not None else None

    def list(self, *, state: YouTubeUploadState | None = None) -> tuple[YouTubeUpload, ...]:
        clauses: list[str] = []
        parameters: list[object] = []
        if state is not None:
            clauses.append("state = ?")
            parameters.append(state.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection(write=False) as connection:
            rows = connection.execute(
                "SELECT * FROM youtube_uploads"
                f"{where} ORDER BY created_at DESC, upload_id DESC",
                parameters,
            ).fetchall()
        return tuple(_upload_from_row(row) for row in rows)

    def completed_for_recording(self, recording_id: str) -> YouTubeUpload | None:
        identifier = _required_text(recording_id, "recording_id")
        with self._connection(write=False) as connection:
            row = connection.execute(
                """
                SELECT * FROM youtube_uploads
                WHERE recording_id = ? AND state = 'completed'
                ORDER BY updated_at DESC, upload_id DESC
                LIMIT 1
                """,
                (identifier,),
            ).fetchone()
        return _upload_from_row(row) if row is not None else None

    def latest_for_recording(self, recording_id: str) -> YouTubeUpload | None:
        identifier = _required_text(recording_id, "recording_id")
        with self._connection(write=False) as connection:
            row = connection.execute(
                """
                SELECT * FROM youtube_uploads
                WHERE recording_id = ?
                ORDER BY updated_at DESC, upload_id DESC
                LIMIT 1
                """,
                (identifier,),
            ).fetchone()
        return _upload_from_row(row) if row is not None else None

    def update(
        self,
        upload: YouTubeUpload,
        *,
        state: YouTubeUploadState | None = None,
        prepare_queue_id: str | None = None,
        video_id: str | None = None,
        watch_url: str | None = None,
        error: str | None = None,
        increment_attempts: bool = False,
    ) -> YouTubeUpload:
        updated = replace(
            upload,
            state=state or upload.state,
            prepare_queue_id=prepare_queue_id
            if prepare_queue_id is not None
            else upload.prepare_queue_id,
            video_id=video_id if video_id is not None else upload.video_id,
            watch_url=watch_url if watch_url is not None else upload.watch_url,
            error=error,
            attempts=upload.attempts + (1 if increment_attempts else 0),
            updated_at=datetime.now(timezone.utc),
        )
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE youtube_uploads
                SET prepare_queue_id = ?, state = ?, video_id = ?, watch_url = ?,
                    attempts = ?, error = ?, updated_at = ?
                WHERE upload_id = ?
                """,
                (
                    updated.prepare_queue_id,
                    updated.state.value,
                    updated.video_id,
                    updated.watch_url,
                    updated.attempts,
                    updated.error,
                    _format_datetime(updated.updated_at),
                    updated.upload_id,
                ),
            )
        refreshed = self.get(upload.upload_id)
        assert refreshed is not None
        return refreshed

    @contextmanager
    def _connection(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        try:
            connection = connect_history_database(self.database_path)
        except HistoryDatabaseError as exc:
            raise YouTubeUploadError(str(exc)) from exc
        try:
            if write:
                with connection:
                    yield connection
            else:
                yield connection
        except sqlite3.Error as exc:
            raise YouTubeUploadError(f"YouTubeアップロードDB操作に失敗しました: {exc}") from exc
        finally:
            connection.close()


def _upload_from_row(row: sqlite3.Row) -> YouTubeUpload:
    tags = json.loads(row["tags_json"])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise YouTubeUploadError("youtube_uploads.tags_jsonが不正です")
    metadata = UploadMetadata(
        title=row["title"],
        description=row["description"],
        tags=tuple(tags),
        privacy=UploadPrivacy(row["privacy"]),
    )
    return YouTubeUpload(
        upload_id=row["upload_id"],
        recording_id=row["recording_id"],
        prepare_queue_id=row["prepare_queue_id"],
        state=YouTubeUploadState(row["state"]),
        metadata=metadata,
        video_id=row["video_id"],
        watch_url=row["watch_url"],
        attempts=row["attempts"],
        error=row["error"],
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _required_text(value: str, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise YouTubeUploadError(f"{key} は空でない文字列である必要があります")
    return value.strip()


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise YouTubeUploadError("timestampにはタイムゾーンが必要です")
    return parsed.astimezone(timezone.utc)
