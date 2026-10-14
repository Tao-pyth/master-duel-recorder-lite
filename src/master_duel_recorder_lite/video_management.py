"""DB置換・関連情報削除を、動画の利用と未完了削除から隔離します。"""
from __future__ import annotations

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from .history_database import HISTORY_DATABASE_NAME
from .recording_lock import RecordingBusyError, RecordingLock
from .runtime_paths import RuntimePaths
from .upload_queue import UploadQueueState, UploadQueueStore
from .video_lifecycle import _process_alive


class VideoManagementError(RuntimeError):
    """動画の利用・回復が終わるまで管理データの変更を待つ必要があります。"""


@contextmanager
def video_management_gate(paths: RuntimePaths, *, database_path: Path | None = None) -> Iterator[None]:
    """バックアップから管理変更の完了まで、最外周で一度だけ保持します。"""
    try:
        video_lock = RecordingLock.acquire(paths.data / "video-operations.lock", recording_id="video-management")
    except (RecordingBusyError, OSError) as exc:
        raise VideoManagementError("録画・動画操作が使用中のため管理操作を実行できません") from exc
    with video_lock:
        try:
            recording_lock = RecordingLock.acquire(paths.data / "recording.lock", recording_id="video-management")
        except (RecordingBusyError, OSError) as exc:
            raise VideoManagementError("録画・動画操作が使用中のため管理操作を実行できません") from exc
        with recording_lock:
            assert_video_management_allowed(paths, database_path=database_path)
            yield


def assert_video_management_allowed(paths: RuntimePaths, *, database_path: Path | None = None) -> None:
    """呼出し側が両OS排他を保持すること。確実に終了した利用leaseだけ除去します。"""
    database = (database_path or paths.db / HISTORY_DATABASE_NAME).resolve()
    dead_uses: list[tuple[str, int]] = []
    try:
        _assert_youtube_upload_idle(paths)
        if database.exists():
            with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True, timeout=10)) as connection:
                tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                checks = (
                    ("video_deletion_journal", "state IN ('intent','staged','unlinked')", "動画削除の回復が未完了です。通常起動で回復してから管理操作を実行してください"),
                    ("card_analysis_jobs", "state='running'", "カード解析が実行中のため管理操作を実行できません"),
                    ("youtube_uploads", "state IN ('preparing','uploading')", "YouTube投稿が実行中のため管理操作を実行できません"),
                )
                for table, clause, message in checks:
                    # schema17/18以前には動画管理や解析tableが存在しません。
                    if table in tables and connection.execute(f"SELECT 1 FROM {table} WHERE {clause} LIMIT 1").fetchone():
                        raise VideoManagementError(message)
                if "video_asset_uses" in tables:
                    for identifier, process_id in connection.execute("SELECT use_id,process_id FROM video_asset_uses"):
                        if _process_alive(process_id) is not False:
                            raise VideoManagementError("元動画が利用中、または利用processの終了を確認できないため管理操作を実行できません")
                        dead_uses.append((identifier, process_id))
        if any(item.state is UploadQueueState.PROCESSING for item in UploadQueueStore(paths).list()):
            raise VideoManagementError("投稿用動画の準備が実行中のため管理操作を実行できません")
        if dead_uses:
            with closing(sqlite3.connect(database, timeout=10)) as connection, connection:
                for identifier, process_id in dead_uses:
                    if _process_alive(process_id) is not False:
                        raise VideoManagementError("動画利用processの終了を再確認できません")
                    connection.execute("DELETE FROM video_asset_uses WHERE use_id=? AND process_id=?", (identifier, process_id))
    except VideoManagementError:
        raise
    except Exception as exc:
        raise VideoManagementError(f"管理操作前の動画・処理状態を確認できません: {exc}") from exc


def _assert_youtube_upload_idle(paths: RuntimePaths) -> None:
    """送信成功保存後も、削除後処理の終端まではDB置換を許可しません。"""
    owner_path = paths.data / "youtube-upload.lock"
    try:
        owner_path.stat()
    except FileNotFoundError:
        # callerのvideo-operations.lockが、新しい投稿ownerの作成を止めています。
        return
    try:
        with RecordingLock.acquire(owner_path, recording_id="youtube-management-check"):
            pass
    except (RecordingBusyError, OSError) as exc:
        raise VideoManagementError("YouTube投稿または動画の後処理が実行中です。終端まで待ってから管理操作を実行してください") from exc
