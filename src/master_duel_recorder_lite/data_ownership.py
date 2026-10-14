"""対戦・録画が所有する関連行を、呼出し元のtransaction内で削除します。"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

from .data_protection import DataProtectionService
from .runtime_paths import RuntimePaths, default_runtime_paths


class DataOwnershipError(RuntimeError):
    """未完了操作や所有関係のため、管理データを安全に変更できません。"""


def scoped_runtime_paths(
    database_path: Path, *, recordings_root: Path | None = None, exports_path: Path | None = None,
) -> RuntimePaths:
    """渡されたDB・保存先の範囲だけから排他とバックアップの置き場所を組み立てます。"""
    database = database_path.expanduser().resolve()
    if database.parent.name == "db" and database.parent.parent.name == "data":
        paths = default_runtime_paths(user_data_dir=database.parent.parent.parent)
        if recordings_root is not None and recordings_root.expanduser().resolve() != paths.recordings:
            raise DataOwnershipError("DBと録画保存先が同じ実行時データ領域にありません")
    else:
        container = database.parent
        if recordings_root is not None:
            recording_directory = recordings_root.expanduser().resolve()
            container = recording_directory.parent
            if database.parent != container and database.parent.parent != container:
                raise DataOwnershipError("DBと録画保存先の共通の管理範囲を確認できません")
        paths = default_runtime_paths(user_data_dir=container)
        paths = replace(paths, data=container, db=database.parent,
                        recordings=recordings_root.expanduser().resolve() if recordings_root else container / "recordings",
                        exports=container / "exports", queue=container / "queue")
    if exports_path is not None:
        explicit_exports = exports_path.expanduser().resolve()
        if paths.root != explicit_exports and paths.root not in explicit_exports.parents:
            raise DataOwnershipError("エクスポート保存先が実行時データ領域の外側です")
        paths = replace(paths, exports=explicit_exports)
    return paths


def scoped_data_protection(paths: RuntimePaths, database_path: Path) -> DataProtectionService:
    protection = DataProtectionService(paths)
    # 単独DB fixtureのファイル名も、呼出し元が渡した対象に限定します。
    protection.database_path = database_path.expanduser().resolve()
    return protection


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise DataOwnershipError("関連情報の削除には明示的なtransactionが必要です")


def delete_duel_related(connection: sqlite3.Connection, duel_id: str) -> int:
    _require_transaction(connection)
    if not isinstance(duel_id, str) or not duel_id.strip():
        raise DataOwnershipError("対戦IDが不正です")
    connection.execute(
        "DELETE FROM duel_card_information_changes WHERE information_id IN "
        "(SELECT information_id FROM duel_card_information WHERE duel_id=?)", (duel_id,),
    )
    for table in ("duel_card_opener_changes", "card_migration_review_items", "duel_card_openers",
                  "duel_card_information", "duel_record_tag_links", "duel_record_tags", "duel_record_changes"):
        connection.execute(f"DELETE FROM {table} WHERE duel_id=?", (duel_id,))
    return connection.execute("DELETE FROM duel_records WHERE duel_id=?", (duel_id,)).rowcount


def delete_recording_related(connection: sqlite3.Connection, recording_id: str) -> int:
    _require_transaction(connection)
    if not isinstance(recording_id, str) or not recording_id.strip():
        raise DataOwnershipError("録画IDが不正です")
    if connection.execute(
        "SELECT 1 FROM video_deletion_journal WHERE recording_id=? AND state IN ('intent','staged','unlinked')",
        (recording_id,),
    ).fetchone() is not None:
        raise DataOwnershipError("未完了の動画削除を通常起動で回復してから再操作してください")
    duels = connection.execute("SELECT duel_id FROM duel_records WHERE recording_id=?", (recording_id,)).fetchall()
    for row in duels:
        delete_duel_related(connection, str(row[0]))
    connection.execute("DELETE FROM card_migration_review_items WHERE recording_id=?", (recording_id,))
    connection.execute(
        "DELETE FROM video_asset_uses WHERE asset_id IN "
        "(SELECT asset_id FROM recording_video_assets WHERE recording_id=?)", (recording_id,),
    )
    for table in ("upload_cleanup_requests", "video_deletion_journal", "video_deletion_history", "youtube_uploads",
                  "card_observations", "duel_events"):
        connection.execute(f"DELETE FROM {table} WHERE recording_id=?", (recording_id,))
    connection.execute(
        "DELETE FROM card_analysis_job_sources WHERE job_id IN "
        "(SELECT job_id FROM card_analysis_jobs WHERE recording_id=?)", (recording_id,),
    )
    for table in ("card_analysis_jobs", "recording_video_protection", "recording_video_assets"):
        connection.execute(f"DELETE FROM {table} WHERE recording_id=?", (recording_id,))
    return connection.execute("DELETE FROM recordings WHERE recording_id=?", (recording_id,)).rowcount


def clear_history_owned(connection: sqlite3.Connection) -> None:
    _require_transaction(connection)
    for row in connection.execute("SELECT recording_id FROM recordings ORDER BY recording_id").fetchall():
        delete_recording_related(connection, str(row[0]))
    for row in connection.execute("SELECT duel_id FROM duel_records ORDER BY duel_id").fetchall():
        delete_duel_related(connection, str(row[0]))


def clear_portable_base(connection: sqlite3.Connection) -> None:
    """全置換で共有カード・解析設定を保持し、ほかの持出し対象を消します。"""
    clear_history_owned(connection)
    for table in ("deck_tag_links", "saved_duel_filters", "duel_editor_preferences", "seasons", "duel_catalog_entries"):
        connection.execute(f"DELETE FROM {table}")
