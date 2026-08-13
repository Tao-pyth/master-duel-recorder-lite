from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


EXPORT_SCHEMA = "mdrl-managed-data-v1"
MANAGED_TABLES = (
    "recordings",
    "duel_catalog_entries",
    "seasons",
    "duel_records",
    "duel_record_tags",
    "duel_record_changes",
    "duel_events",
    "duel_record_tag_links",
    "duel_editor_preferences",
)
DELETE_ORDER = (
    "duel_record_tag_links",
    "duel_record_tags",
    "duel_record_changes",
    "duel_events",
    "duel_records",
    "seasons",
    "duel_catalog_entries",
    "duel_editor_preferences",
    "recordings",
)
RESET_SCOPES = {"history", "decks", "tags", "seasons", "all"}
LEGACY_DEFAULTS = {
    "duel_records": {
        "coin_face": "unknown",
        "coin_toss_outcome": "unknown",
    }
}


class ManagedDataError(RuntimeError):
    """管理データを安全に入出力または初期化できない場合のエラーです。"""


@dataclass(frozen=True)
class ManagedDataResult:
    action: str
    path: Path | None
    backup_path: Path | None
    row_count: int


class ManagedDataService:
    def __init__(self, database_path: Path, exports_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.exports_path = exports_path.expanduser().resolve()
        connect_history_database(self.database_path).close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> ManagedDataService:
        return cls(paths.db / HISTORY_DATABASE_NAME, paths.exports)

    def export_to(self, path: Path) -> ManagedDataResult:
        destination = path.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_history_database(self.database_path)) as connection:
            tables = {
                table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
                for table in MANAGED_TABLES
            }
        payload = {
            "schema": EXPORT_SCHEMA,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables,
        }
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(destination)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ManagedDataError(f"管理データを書き出せません: {destination}: {exc}") from exc
        return ManagedDataResult(
            action="export",
            path=destination,
            backup_path=None,
            row_count=sum(len(rows) for rows in tables.values()),
        )

    def import_from(self, path: Path) -> ManagedDataResult:
        source = path.expanduser().resolve()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManagedDataError(f"管理データを読み込めません: {source}: {exc}") from exc
        tables = self._validated_tables(payload)
        backup = self._backup("import")
        try:
            with closing(connect_history_database(self.database_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                for table in DELETE_ORDER:
                    connection.execute(f"DELETE FROM {table}")
                for table in MANAGED_TABLES:
                    self._insert_rows(connection, table, tables[table])
                check = connection.execute("PRAGMA foreign_key_check").fetchall()
                if check:
                    raise ManagedDataError("インポートデータの参照関係が不正です")
                connection.commit()
        except (sqlite3.Error, ManagedDataError) as exc:
            self._restore_backup(backup)
            if isinstance(exc, ManagedDataError):
                raise
            raise ManagedDataError(f"管理データを取り込めません: {exc}") from exc
        return ManagedDataResult(
            action="import",
            path=source,
            backup_path=backup,
            row_count=sum(len(rows) for rows in tables.values()),
        )

    def reset(self, scope: str) -> ManagedDataResult:
        if scope not in RESET_SCOPES:
            raise ManagedDataError(f"未対応の初期化対象です: {scope}")
        backup = self._backup(f"reset-{scope}")
        try:
            with closing(connect_history_database(self.database_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if scope in {"history", "all"}:
                    for table in (
                        "duel_record_tag_links",
                        "duel_record_tags",
                        "duel_record_changes",
                        "duel_events",
                        "duel_records",
                        "recordings",
                    ):
                        connection.execute(f"DELETE FROM {table}")
                if scope in {"decks", "all"}:
                    if scope != "all":
                        connection.execute(
                            "UPDATE duel_records SET own_deck_id = NULL, opponent_deck_id = NULL, "
                            "own_deck = '', opponent_deck = ''"
                        )
                    connection.execute(
                        "UPDATE duel_editor_preferences SET own_deck = '', opponent_deck = ''"
                    )
                    connection.execute("DELETE FROM duel_catalog_entries WHERE kind = 'deck'")
                if scope in {"tags", "all"}:
                    if scope != "all":
                        connection.execute("DELETE FROM duel_record_tag_links")
                        connection.execute("DELETE FROM duel_record_tags")
                    connection.execute("UPDATE duel_editor_preferences SET tags_json = '[]'")
                    connection.execute("DELETE FROM duel_catalog_entries WHERE kind = 'tag'")
                if scope in {"seasons", "all"}:
                    if scope != "all":
                        connection.execute("UPDATE duel_records SET season_id = NULL")
                    connection.execute("DELETE FROM seasons")
                connection.commit()
        except sqlite3.Error as exc:
            self._restore_backup(backup)
            raise ManagedDataError(f"管理データを初期化できません: {exc}") from exc
        return ManagedDataResult("reset", None, backup, 0)

    def _validated_tables(self, payload: object) -> dict[str, list[dict[str, object]]]:
        if not isinstance(payload, dict) or payload.get("schema") != EXPORT_SCHEMA:
            raise ManagedDataError("このアプリで作成した管理データJSONではありません")
        raw_tables = payload.get("tables")
        if not isinstance(raw_tables, dict) or set(raw_tables) != set(MANAGED_TABLES):
            raise ManagedDataError("管理データJSONのテーブル構成が不正です")
        with closing(connect_history_database(self.database_path)) as connection:
            columns = {
                table: {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
                for table in MANAGED_TABLES
            }
        validated: dict[str, list[dict[str, object]]] = {}
        recording_dates = {
            str(row.get("recording_id")): row.get("started_at") or row.get("created_at")
            for row in raw_tables["recordings"]
            if isinstance(row, dict) and row.get("recording_id")
        }
        for table in MANAGED_TABLES:
            rows = raw_tables[table]
            if not isinstance(rows, list):
                raise ManagedDataError(f"{table}のデータ形式が不正です")
            normalized: list[dict[str, object]] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ManagedDataError(f"{table}の列構成が現在のDBと一致しません")
                if any(not isinstance(key, str) for key in row):
                    raise ManagedDataError(f"{table}の列名が不正です")
                item = dict(row)
                if table == "duel_records" and "duel_id" not in item:
                    recording_id = str(item.get("recording_id") or "")
                    item["duel_id"] = recording_id
                    item["entry_origin"] = "recording"
                    item["occurred_at"] = recording_dates.get(recording_id)
                if (
                    table
                    in {
                        "duel_record_tags",
                        "duel_record_changes",
                        "duel_record_tag_links",
                    }
                    and "duel_id" not in item
                    and "recording_id" in item
                ):
                    item["duel_id"] = item.pop("recording_id")
                defaults = LEGACY_DEFAULTS.get(table, {})
                if set(item).issubset(columns[table]):
                    for column, default in defaults.items():
                        item.setdefault(column, default)
                if set(item) != columns[table]:
                    raise ManagedDataError(f"{table}の列構成が現在のDBと一致しません")
                normalized.append(item)
            validated[table] = normalized
        return validated

    @staticmethod
    def _insert_rows(
        connection: sqlite3.Connection, table: str, rows: list[dict[str, object]]
    ) -> None:
        if not rows:
            return
        columns = tuple(rows[0])
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        connection.executemany(sql, [tuple(row[column] for column in columns) for row in rows])

    def _backup(self, reason: str) -> Path:
        self.exports_path.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.exports_path / f"history.{reason}.{timestamp}.{uuid.uuid4().hex}.sqlite3"
        source: sqlite3.Connection | None = None
        target: sqlite3.Connection | None = None
        try:
            source = connect_history_database(self.database_path)
            target = sqlite3.connect(destination)
            source.backup(target)
            target.commit()
            return destination
        except (OSError, sqlite3.Error) as exc:
            destination.unlink(missing_ok=True)
            raise ManagedDataError(f"操作前バックアップを作成できません: {exc}") from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()

    def _restore_backup(self, backup: Path) -> None:
        source: sqlite3.Connection | None = None
        target: sqlite3.Connection | None = None
        try:
            source = sqlite3.connect(backup)
            target = sqlite3.connect(self.database_path)
            source.backup(target)
            target.commit()
        except sqlite3.Error as exc:
            raise ManagedDataError(
                f"処理に失敗し、バックアップの復元にも失敗しました: {backup}: {exc}"
            ) from exc
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
