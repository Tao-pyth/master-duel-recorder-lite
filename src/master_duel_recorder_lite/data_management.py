from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
import json
from pathlib import Path
import sqlite3
import uuid

from .data_ownership import (
    DataOwnershipError, clear_history_owned, clear_portable_base, scoped_data_protection, scoped_runtime_paths,
)
from .data_protection import BackupInfo, DataProtectionError, DataProtectionService
from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .portable_data import (
    PORTABLE_SCHEMA, PORTABLE_TABLES, PortableDataError, get_portable_export,
    remap_portable_card_ids, validate_portable_document,
)
from .runtime_paths import RuntimePaths


EXPORT_SCHEMA = PORTABLE_SCHEMA
MANAGED_TABLES = PORTABLE_TABLES
DELETE_ORDER = tuple(reversed(PORTABLE_TABLES))
RESET_SCOPES = {"history", "decks", "tags", "seasons", "all"}


class ManagedDataError(RuntimeError):
    """管理データを安全に入出力または初期化できない場合のエラーです。"""


@dataclass(frozen=True)
class ManagedDataResult:
    action: str
    path: Path | None
    backup_path: Path | None
    row_count: int
    id_maps: dict[str, dict[str, str]] = field(default_factory=dict)


class ManagedDataService:
    def __init__(
        self, database_path: Path, exports_path: Path, protection: DataProtectionService | None = None,
    ) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.exports_path = exports_path.expanduser().resolve()
        self.paths = protection.paths if protection is not None else scoped_runtime_paths(
            self.database_path, exports_path=self.exports_path,
        )
        self.protection = protection or scoped_data_protection(self.paths, self.database_path)
        connect_history_database(self.database_path).close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> ManagedDataService:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME, paths.exports, DataProtectionService(paths))

    def export_to(self, path: Path) -> ManagedDataResult:
        destination = path.expanduser().resolve()
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            payload = get_portable_export(self.database_path, self.paths.recordings)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(destination)
        except (OSError, PortableDataError) as exc:
            temporary.unlink(missing_ok=True)
            raise ManagedDataError(f"管理データを書き出せません: {destination}: {exc}") from exc
        return ManagedDataResult("export", destination, None,
                                 sum(len(rows) for rows in payload["tables"].values()))

    def import_from(self, path: Path) -> ManagedDataResult:
        source = path.expanduser().resolve()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            incoming = validate_portable_document(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, PortableDataError) as exc:
            raise ManagedDataError(f"管理データを読み込めません: {source}: {exc}") from exc

        with self._mutation("import") as backup:
            try:
                with closing(connect_history_database(self.database_path)) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    with connection:
                        existing_cards = [dict(row) for row in connection.execute("SELECT * FROM card_identities")]
                        existing_targets = [dict(row) for row in connection.execute("SELECT * FROM card_targets")]
                        plan = remap_portable_card_ids(incoming, existing_cards, existing_targets)
                        _validate_shared_target_links(connection, plan.tables)
                        clear_portable_base(connection)
                        _insert_managed_plan(connection, plan.tables, self._insert_rows)
                        if connection.execute("PRAGMA foreign_key_check").fetchall():
                            raise ManagedDataError("インポートデータの参照関係が不正です")
                        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                            raise ManagedDataError("インポート後のDB整合性検査に失敗しました")
            except (sqlite3.Error, PortableDataError, DataOwnershipError) as exc:
                raise ManagedDataError(f"管理データを取り込めません。DBは変更前へ戻しました: {exc}") from exc
        return ManagedDataResult("import", source, backup.path, plan.row_count, plan.id_maps)

    def reset(self, scope: str) -> ManagedDataResult:
        if scope not in RESET_SCOPES:
            raise ManagedDataError(f"未対応の初期化対象です: {scope}")
        with self._mutation(f"reset-{scope}") as backup:
            try:
                with closing(connect_history_database(self.database_path)) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    with connection:
                        if scope in {"history", "all"}:
                            clear_history_owned(connection)
                        if scope in {"decks", "all"}:
                            connection.execute("DELETE FROM deck_tag_links")
                            connection.execute("UPDATE duel_records SET own_deck_id=NULL,opponent_deck_id=NULL,own_deck='',opponent_deck=''")
                            connection.execute("UPDATE duel_editor_preferences SET own_deck='',opponent_deck=''")
                            connection.execute("DELETE FROM duel_catalog_entries WHERE kind='deck'")
                        if scope in {"tags", "all"}:
                            connection.execute("DELETE FROM deck_tag_links")
                            connection.execute("DELETE FROM duel_record_tag_links")
                            connection.execute("DELETE FROM duel_record_tags")
                            connection.execute("UPDATE duel_editor_preferences SET tags_json='[]'")
                            connection.execute("DELETE FROM duel_catalog_entries WHERE kind='tag'")
                        if scope in {"seasons", "all"}:
                            connection.execute("UPDATE duel_records SET season_id=NULL")
                            connection.execute("DELETE FROM seasons")
                        if scope == "all":
                            connection.execute("DELETE FROM saved_duel_filters")
                        if connection.execute("PRAGMA foreign_key_check").fetchall():
                            raise ManagedDataError("初期化後のDB参照関係が不正です")
            except (sqlite3.Error, DataOwnershipError) as exc:
                raise ManagedDataError(f"管理データを初期化できません。DBは変更前へ戻しました: {exc}") from exc
        return ManagedDataResult("reset", None, backup.path, 0)

    @contextmanager
    def _mutation(self, reason: str) -> Iterator[BackupInfo]:
        from .video_management import VideoManagementError, video_management_gate

        try:
            with video_management_gate(self.paths, database_path=self.database_path):
                with self.protection.retained_backup(reason) as backup:
                    yield backup
        except (VideoManagementError, DataProtectionError, DataOwnershipError) as exc:
            raise ManagedDataError(f"管理操作を開始できません: {exc}") from exc

    def _validated_tables(self, payload: object) -> dict[str, list[dict[str, object]]]:
        try:
            return validate_portable_document(payload).tables
        except PortableDataError as exc:
            raise ManagedDataError(str(exc)) from exc

    @staticmethod
    def _insert_rows(connection: sqlite3.Connection, table: str, rows: list[dict[str, object]]) -> None:
        if rows:
            columns = tuple(rows[0])
            placeholders = ",".join("?" for _ in columns)
            connection.executemany(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                                   [tuple(row[column] for column in columns) for row in rows])


def _validate_shared_target_links(connection: sqlite3.Connection, tables: dict[str, list[dict[str, object]]]) -> None:
    current = {row["target_id"]: row["card_id"] for row in connection.execute("SELECT * FROM card_target_identity_links")}
    for row in tables["card_target_identity_links"]:
        if row["target_id"] in current and row["card_id"] != current[row["target_id"]]:
            raise ManagedDataError("既存の解析対象と持込カードの同一性が異なります。共有設定を変更せず取込を中止しました")


def _insert_managed_plan(connection: sqlite3.Connection, tables: dict[str, list[dict[str, object]]], insert) -> None:
    shared_keys = {"card_identities": "card_id", "card_targets": "target_id", "card_target_identity_links": "target_id"}
    for table in PORTABLE_TABLES:
        rows = tables[table]
        if table in shared_keys:
            key = shared_keys[table]
            existing = {row[0] for row in connection.execute(f"SELECT {key} FROM {table}")}
            rows = [row for row in rows if row[key] not in existing]
        insert(connection, table, rows)
