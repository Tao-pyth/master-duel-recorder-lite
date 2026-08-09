from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import unicodedata

from .duel_records import (
    MAX_DECK_LENGTH,
    MAX_TAG_LENGTH,
    DuelRecordValues,
)
from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


CATALOG_KINDS = {"deck", "tag"}


class DuelCatalogError(RuntimeError):
    """デッキ名・タグ辞書を安全に操作できない場合のエラーです。"""


@dataclass(frozen=True)
class DuelCatalogEntry:
    entry_id: int
    kind: str
    name: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DuelEditorPreferences:
    duel_type: str = "other"
    own_deck: str = ""
    opponent_deck: str = ""
    tags: tuple[str, ...] = ()

    def to_record_values(self) -> DuelRecordValues:
        return DuelRecordValues(
            duel_type=self.duel_type,
            own_deck=self.own_deck,
            opponent_deck=self.opponent_deck,
            tags=self.tags,
        )


class DuelCatalogRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connection = connect_history_database(self.database_path)
        connection.close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> DuelCatalogRepository:
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def list(self, *, kind: str | None = None) -> tuple[DuelCatalogEntry, ...]:
        normalized_kind = _kind(kind) if kind is not None else None
        sql = "SELECT * FROM duel_catalog_entries"
        parameters: tuple[object, ...] = ()
        if normalized_kind is not None:
            sql += " WHERE kind = ?"
            parameters = (normalized_kind,)
        sql += " ORDER BY kind, normalized_name, entry_id"
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return tuple(_entry(row) for row in rows)

    def add(self, kind: str, name: str) -> DuelCatalogEntry:
        normalized_kind = _kind(kind)
        display_name, normalized_name = _name(normalized_kind, name)
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with closing(connect_history_database(self.database_path)) as connection, connection:
                cursor = connection.execute(
                    """
                    INSERT INTO duel_catalog_entries (
                        kind, name, normalized_name, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (normalized_kind, display_name, normalized_name, timestamp, timestamp),
                )
                entry_id = cursor.lastrowid
                row = connection.execute(
                    "SELECT * FROM duel_catalog_entries WHERE entry_id = ?", (entry_id,)
                ).fetchone()
            assert row is not None
            return _entry(row)
        except sqlite3.IntegrityError as exc:
            raise DuelCatalogError(f"同じ{_kind_label(normalized_kind)}が既にあります: {display_name}") from exc

    def remember(self, kind: str, name: str) -> DuelCatalogEntry | None:
        if not name.strip():
            return None
        normalized_kind = _kind(kind)
        display_name, normalized_name = _name(normalized_kind, name)
        timestamp = datetime.now(timezone.utc).isoformat()
        with closing(connect_history_database(self.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO duel_catalog_entries (
                    kind, name, normalized_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(kind, normalized_name) DO NOTHING
                """,
                (normalized_kind, display_name, normalized_name, timestamp, timestamp),
            )
            row = connection.execute(
                "SELECT * FROM duel_catalog_entries WHERE kind = ? AND normalized_name = ?",
                (normalized_kind, normalized_name),
            ).fetchone()
        assert row is not None
        return _entry(row)

    def rename(self, entry_id: int, name: str) -> DuelCatalogEntry:
        identifier = _entry_id(entry_id)
        with closing(connect_history_database(self.database_path)) as connection:
            current = connection.execute(
                "SELECT * FROM duel_catalog_entries WHERE entry_id = ?", (identifier,)
            ).fetchone()
        if current is None:
            raise DuelCatalogError(f"辞書項目が見つかりません: {identifier}")
        display_name, normalized_name = _name(current["kind"], name)
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with closing(connect_history_database(self.database_path)) as connection, connection:
                cursor = connection.execute(
                    """
                    UPDATE duel_catalog_entries
                    SET name = ?, normalized_name = ?, updated_at = ?
                    WHERE entry_id = ?
                    """,
                    (display_name, normalized_name, timestamp, identifier),
                )
                if cursor.rowcount != 1:
                    raise DuelCatalogError(f"辞書項目が見つかりません: {identifier}")
                row = connection.execute(
                    "SELECT * FROM duel_catalog_entries WHERE entry_id = ?", (identifier,)
                ).fetchone()
            assert row is not None
            return _entry(row)
        except sqlite3.IntegrityError as exc:
            raise DuelCatalogError(f"同じ{_kind_label(current['kind'])}が既にあります: {display_name}") from exc

    def delete(self, entry_id: int) -> DuelCatalogEntry:
        identifier = _entry_id(entry_id)
        with closing(connect_history_database(self.database_path)) as connection, connection:
            row = connection.execute(
                "SELECT * FROM duel_catalog_entries WHERE entry_id = ?", (identifier,)
            ).fetchone()
            if row is None:
                raise DuelCatalogError(f"辞書項目が見つかりません: {identifier}")
            connection.execute("DELETE FROM duel_catalog_entries WHERE entry_id = ?", (identifier,))
        return _entry(row)

    def preferences(self) -> DuelEditorPreferences:
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM duel_editor_preferences WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return DuelEditorPreferences()
        try:
            tags = json.loads(row["tags_json"])
            if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
                raise ValueError("tags_json must be a string array")
            values = DuelRecordValues(
                duel_type=row["duel_type"],
                own_deck=row["own_deck"],
                opponent_deck=row["opponent_deck"],
                tags=tuple(tags),
            ).normalized()
            return DuelEditorPreferences(
                values.duel_type,
                values.own_deck,
                values.opponent_deck,
                values.tags,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DuelCatalogError(f"前回入力の形式が不正です: {exc}") from exc

    def remember_record_values(self, values: DuelRecordValues) -> DuelEditorPreferences:
        normalized = values.normalized()
        self.remember("deck", normalized.own_deck)
        self.remember("deck", normalized.opponent_deck)
        for tag in normalized.tags:
            self.remember("tag", tag)
        timestamp = datetime.now(timezone.utc).isoformat()
        with closing(connect_history_database(self.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO duel_editor_preferences (
                    singleton, duel_type, own_deck, opponent_deck, tags_json, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    duel_type = excluded.duel_type,
                    own_deck = excluded.own_deck,
                    opponent_deck = excluded.opponent_deck,
                    tags_json = excluded.tags_json,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized.duel_type,
                    normalized.own_deck,
                    normalized.opponent_deck,
                    json.dumps(list(normalized.tags), ensure_ascii=False),
                    timestamp,
                ),
            )
        return DuelEditorPreferences(
            normalized.duel_type,
            normalized.own_deck,
            normalized.opponent_deck,
            normalized.tags,
        )


def _entry(row: sqlite3.Row) -> DuelCatalogEntry:
    return DuelCatalogEntry(
        entry_id=row["entry_id"],
        kind=row["kind"],
        name=row["name"],
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
    )


def _kind(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized not in CATALOG_KINDS:
        raise DuelCatalogError(f"未対応の辞書種別です: {value}")
    return normalized


def _kind_label(kind: str) -> str:
    return "デッキ名" if kind == "deck" else "タグ"


def _name(kind: str, value: str) -> tuple[str, str]:
    display_name = unicodedata.normalize("NFC", value.strip())
    maximum = MAX_DECK_LENGTH if kind == "deck" else MAX_TAG_LENGTH
    if not display_name:
        raise DuelCatalogError(f"{_kind_label(kind)}を入力してください")
    if len(display_name) > maximum:
        raise DuelCatalogError(f"{_kind_label(kind)}は{maximum}文字以内で入力してください")
    if any(unicodedata.category(character).startswith("C") for character in display_name):
        raise DuelCatalogError(f"{_kind_label(kind)}に制御文字は使用できません")
    return display_name, display_name.casefold()


def _entry_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DuelCatalogError("entry_idは1以上の整数である必要があります")
    return value


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise DuelCatalogError("辞書項目の日時にタイムゾーンがありません")
    return parsed.astimezone(timezone.utc)
