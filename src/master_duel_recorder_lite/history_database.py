from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


CURRENT_SCHEMA_VERSION = 3
HISTORY_DATABASE_NAME = "history.sqlite3"


class HistoryDatabaseError(RuntimeError):
    """録画履歴DBを安全に初期化または移行できない場合のエラーです。"""


@dataclass(frozen=True)
class HistoryDatabaseInfo:
    path: Path
    version: int
    backup_path: Path | None


Migration = Callable[[sqlite3.Connection], None]


def _migrate_to_v1(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE recordings (
            recording_id TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (
                state IN ('starting', 'recording', 'completed', 'failed')
            ),
            source TEXT NOT NULL CHECK (length(trim(source)) > 0),
            detection_reason TEXT,
            output_path TEXT NOT NULL UNIQUE CHECK (length(trim(output_path)) > 0),
            container TEXT NOT NULL CHECK (container IN ('mkv', 'mp4')),
            created_at TEXT NOT NULL,
            started_at TEXT,
            ended_at TEXT,
            duration_seconds REAL CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
            size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
            returncode INTEGER,
            error TEXT,
            diagnostics_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX recordings_started_at_idx ON recordings(started_at DESC, recording_id DESC)"
    )
    connection.execute(
        "CREATE INDEX recordings_state_started_at_idx "
        "ON recordings(state, started_at DESC, recording_id DESC)"
    )


def _migrate_to_v2(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE recordings ADD COLUMN failure_code TEXT")
    connection.execute("ALTER TABLE recordings ADD COLUMN recovery_policy TEXT")
    connection.execute(
        "ALTER TABLE recordings ADD COLUMN recovery_state TEXT NOT NULL DEFAULT 'not_required' "
        "CHECK (recovery_state IN "
        "('not_required', 'pending', 'inspecting', 'repairable', 'repaired', 'ignored', 'unrecoverable'))"
    )
    connection.execute(
        "ALTER TABLE recordings ADD COLUMN recovery_attempts INTEGER NOT NULL DEFAULT 0 "
        "CHECK (recovery_attempts >= 0)"
    )
    connection.execute("ALTER TABLE recordings ADD COLUMN recovery_message TEXT")
    connection.execute("ALTER TABLE recordings ADD COLUMN recovery_diagnostic TEXT")
    connection.execute(
        """
        CREATE TABLE recovery_artifacts (
            artifact_id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            kind TEXT NOT NULL CHECK (kind IN ('recovered', 'partial')),
            status TEXT NOT NULL CHECK (status IN ('created', 'valid', 'failed')),
            output_path TEXT NOT NULL UNIQUE CHECK (length(trim(output_path)) > 0),
            size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
            diagnostic TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX recordings_recovery_state_idx "
        "ON recordings(recovery_state, updated_at DESC, recording_id DESC)"
    )
    connection.execute(
        "CREATE INDEX recovery_artifacts_recording_id_idx "
        "ON recovery_artifacts(recording_id, created_at DESC)"
    )
    connection.execute(
        """
        UPDATE recordings
        SET failure_code = 'legacy_failure',
            recovery_policy = 'manual_review',
            recovery_state = 'pending',
            recovery_message = '以前のバージョンで失敗した録画です。内容を確認してください。',
            recovery_diagnostic = COALESCE(error, 'legacy failed recording')
        WHERE state = 'failed'
        """
    )


def _migrate_to_v3(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE duel_records (
            recording_id TEXT PRIMARY KEY REFERENCES recordings(recording_id) ON DELETE RESTRICT,
            status TEXT NOT NULL CHECK (status IN ('draft', 'confirmed')),
            result TEXT NOT NULL CHECK (result IN ('win', 'loss', 'draw', 'unknown')),
            play_order TEXT NOT NULL CHECK (play_order IN ('first', 'second', 'unknown')),
            own_deck TEXT NOT NULL DEFAULT '',
            opponent_deck TEXT NOT NULL DEFAULT '',
            duel_type TEXT NOT NULL CHECK (duel_type IN ('ranked', 'event', 'room', 'solo', 'other')),
            notes TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL CHECK (revision >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE duel_record_tags (
            recording_id TEXT NOT NULL REFERENCES duel_records(recording_id) ON DELETE RESTRICT,
            tag TEXT NOT NULL,
            normalized_tag TEXT NOT NULL,
            PRIMARY KEY (recording_id, normalized_tag)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE duel_record_changes (
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recording_id TEXT NOT NULL REFERENCES duel_records(recording_id) ON DELETE RESTRICT,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            source TEXT NOT NULL CHECK (source IN ('user', 'system', 'detected')),
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            UNIQUE (recording_id, revision)
        )
        """
    )
    connection.execute(
        "CREATE INDEX duel_records_updated_at_idx ON duel_records(updated_at DESC, recording_id DESC)"
    )
    connection.execute(
        "CREATE INDEX duel_record_changes_recording_idx "
        "ON duel_record_changes(recording_id, revision DESC)"
    )


_MIGRATIONS: dict[int, Migration] = {1: _migrate_to_v1, 2: _migrate_to_v2, 3: _migrate_to_v3}


def initialize_history_database(
    path: Path,
    *,
    migrations: Mapping[int, Migration] | None = None,
) -> HistoryDatabaseInfo:
    database_path = path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    existed = database_path.exists() and database_path.stat().st_size > 0
    selected_migrations = dict(_MIGRATIONS if migrations is None else migrations)
    expected_versions = set(range(1, CURRENT_SCHEMA_VERSION + 1))
    if set(selected_migrations) != expected_versions:
        raise HistoryDatabaseError("マイグレーション定義が現在のスキーマ版と一致しません")

    connection: sqlite3.Connection | None = None
    backup_path: Path | None = None
    try:
        connection = sqlite3.connect(database_path, timeout=10.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        current_version = _read_schema_version(connection)
        if current_version > CURRENT_SCHEMA_VERSION:
            raise HistoryDatabaseError(
                f"履歴DBのスキーマ版{current_version}はこのアプリでは未対応です"
            )
        if current_version == CURRENT_SCHEMA_VERSION:
            _validate_current_schema(connection)
            return HistoryDatabaseInfo(database_path, current_version, None)

        if existed:
            backup_path = _backup_database(connection, database_path, current_version)

        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL CHECK (version >= 0)
            )
            """
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(singleton, version) VALUES (1, ?)",
            (current_version,),
        )
        for version in range(current_version + 1, CURRENT_SCHEMA_VERSION + 1):
            selected_migrations[version](connection)
            connection.execute(
                "UPDATE schema_version SET version = ? WHERE singleton = 1",
                (version,),
            )
            connection.execute(f"PRAGMA user_version = {version}")
        connection.commit()
        _validate_current_schema(connection)
        return HistoryDatabaseInfo(database_path, CURRENT_SCHEMA_VERSION, backup_path)
    except HistoryDatabaseError:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise
    except (OSError, sqlite3.Error, KeyError, RuntimeError, ValueError) as exc:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise HistoryDatabaseError(f"録画履歴DBを初期化できません: {database_path}: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def connect_history_database(path: Path) -> sqlite3.Connection:
    info = initialize_history_database(path)
    try:
        connection = sqlite3.connect(info.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection
    except sqlite3.Error as exc:
        raise HistoryDatabaseError(f"録画履歴DBへ接続できません: {info.path}: {exc}") from exc


def _read_schema_version(connection: sqlite3.Connection) -> int:
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if table is None:
        return 0
    columns = {row[1] for row in connection.execute("PRAGMA table_info(schema_version)")}
    if not {"singleton", "version"}.issubset(columns):
        raise HistoryDatabaseError("schema_versionテーブルの形式が不正です")
    row = connection.execute(
        "SELECT version FROM schema_version WHERE singleton = 1"
    ).fetchone()
    if row is None or isinstance(row[0], bool) or not isinstance(row[0], int) or row[0] < 0:
        raise HistoryDatabaseError("schema_versionの値が不正です")
    return row[0]


def _validate_current_schema(connection: sqlite3.Connection) -> None:
    required_columns = {
        "recording_id",
        "state",
        "source",
        "output_path",
        "container",
        "created_at",
        "diagnostics_json",
        "updated_at",
        "failure_code",
        "recovery_policy",
        "recovery_state",
        "recovery_attempts",
        "recovery_message",
        "recovery_diagnostic",
    }
    columns = {row[1] for row in connection.execute("PRAGMA table_info(recordings)")}
    if not required_columns.issubset(columns):
        raise HistoryDatabaseError("録画履歴DBに必須のrecordingsスキーマがありません")
    artifact_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(recovery_artifacts)")
    }
    if not {"artifact_id", "recording_id", "output_path", "status"}.issubset(
        artifact_columns
    ):
        raise HistoryDatabaseError("録画履歴DBに必須のrecovery_artifactsスキーマがありません")
    duel_columns = {row[1] for row in connection.execute("PRAGMA table_info(duel_records)")}
    if not {"recording_id", "status", "result", "revision", "updated_at"}.issubset(
        duel_columns
    ):
        raise HistoryDatabaseError("録画履歴DBに必須のduel_recordsスキーマがありません")
    tag_columns = {row[1] for row in connection.execute("PRAGMA table_info(duel_record_tags)")}
    if not {"recording_id", "tag", "normalized_tag"}.issubset(tag_columns):
        raise HistoryDatabaseError("録画履歴DBに必須のduel_record_tagsスキーマがありません")
    change_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(duel_record_changes)")
    }
    if not {"change_id", "recording_id", "revision", "after_json"}.issubset(change_columns):
        raise HistoryDatabaseError("録画履歴DBに必須のduel_record_changesスキーマがありません")
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or quick_check[0] != "ok":
        detail = quick_check[0] if quick_check else "結果なし"
        raise HistoryDatabaseError(f"録画履歴DBの整合性検査に失敗しました: {detail}")


def _backup_database(
    source: sqlite3.Connection,
    database_path: Path,
    current_version: int,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = database_path.with_name(
        f"{database_path.stem}.v{current_version}.{timestamp}.{uuid.uuid4().hex}.backup.sqlite3"
    )
    destination: sqlite3.Connection | None = None
    try:
        destination = sqlite3.connect(backup_path)
        source.backup(destination)
        destination.commit()
        return backup_path
    except (OSError, sqlite3.Error) as exc:
        raise HistoryDatabaseError(f"移行前バックアップを作成できません: {backup_path}: {exc}") from exc
    finally:
        if destination is not None:
            destination.close()
