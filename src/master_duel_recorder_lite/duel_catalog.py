from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
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
MAX_DESCRIPTION_LENGTH = 500
DEFAULT_TAG_COLOR = "#4F6F8F"
DEFAULT_DECK_COLOR = "#2F6B5F"
TAG_COLOR_PATTERN = re.compile(r"#[0-9A-F]{6}")


class DuelCatalogError(RuntimeError):
    """デッキ名・タグ辞書を安全に操作できない場合のエラーです。"""


@dataclass(frozen=True)
class DuelCatalogEntry:
    entry_id: int
    kind: str
    name: str
    description: str
    color: str | None
    is_archived: bool
    opponent_only: bool
    hidden_from_history_statistics: bool
    deck_only: bool
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
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def list(
        self,
        *,
        kind: str | None = None,
        include_archived: bool = False,
    ) -> tuple[DuelCatalogEntry, ...]:
        normalized_kind = _kind(kind) if kind is not None else None
        sql = "SELECT * FROM duel_catalog_entries"
        clauses: list[str] = []
        parameters: list[object] = []
        if normalized_kind is not None:
            clauses.append("kind = ?")
            parameters.append(normalized_kind)
        if not include_archived:
            clauses.append("is_archived = 0")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY kind, normalized_name, entry_id"
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return tuple(_entry(row) for row in rows)

    def list_decks(
        self, *, include_archived: bool = False, include_hidden: bool = True
    ) -> tuple[DuelCatalogEntry, ...]:
        items = self.list(kind="deck", include_archived=include_archived)
        return (
            items
            if include_hidden
            else tuple(
                item for item in items if not item.hidden_from_history_statistics
            )
        )

    def list_tags(
        self, *, include_archived: bool = False, include_deck_only: bool = True
    ) -> tuple[DuelCatalogEntry, ...]:
        items = self.list(kind="tag", include_archived=include_archived)
        return (
            items
            if include_deck_only
            else tuple(item for item in items if not item.deck_only)
        )

    def add(
        self,
        kind: str,
        name: str,
        *,
        description: str = "",
        color: str | None = None,
        deck_only: bool = False,
    ) -> DuelCatalogEntry:
        normalized_kind = _kind(kind)
        display_name, normalized_name = _name(normalized_kind, name)
        normalized_description = _description(description)
        normalized_color = _color(normalized_kind, color)
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with (
                closing(connect_history_database(self.database_path)) as connection,
                connection,
            ):
                cursor = connection.execute(
                    """
                    INSERT INTO duel_catalog_entries (
                        kind, name, normalized_name, description, color,
                        is_archived, deck_only, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        normalized_kind,
                        display_name,
                        normalized_name,
                        normalized_description,
                        normalized_color,
                        int(bool(deck_only)) if normalized_kind == "tag" else 0,
                        timestamp,
                        timestamp,
                    ),
                )
                entry_id = cursor.lastrowid
                row = connection.execute(
                    "SELECT * FROM duel_catalog_entries WHERE entry_id = ?", (entry_id,)
                ).fetchone()
            assert row is not None
            return _entry(row)
        except sqlite3.IntegrityError as exc:
            raise DuelCatalogError(
                f"同じ{_kind_label(normalized_kind)}が既にあります: {display_name}"
            ) from exc

    def add_deck(
        self,
        name: str,
        *,
        description: str = "",
        color: str = DEFAULT_DECK_COLOR,
    ) -> DuelCatalogEntry:
        return self.add("deck", name, description=description, color=color)

    def add_tag(
        self,
        name: str,
        *,
        description: str = "",
        color: str = DEFAULT_TAG_COLOR,
        deck_only: bool = False,
    ) -> DuelCatalogEntry:
        return self.add(
            "tag",
            name,
            description=description,
            color=color,
            deck_only=deck_only,
        )

    def remember(self, kind: str, name: str) -> DuelCatalogEntry | None:
        if not name.strip():
            return None
        normalized_kind = _kind(kind)
        display_name, normalized_name = _name(normalized_kind, name)
        timestamp = datetime.now(timezone.utc).isoformat()
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
            connection.execute(
                """
                INSERT INTO duel_catalog_entries (
                    kind, name, normalized_name, description, color,
                    is_archived, created_at, updated_at
                ) VALUES (?, ?, ?, '', ?, 0, ?, ?)
                ON CONFLICT(kind, normalized_name) DO UPDATE SET is_archived = 0
                """,
                (
                    normalized_kind,
                    display_name,
                    normalized_name,
                    DEFAULT_TAG_COLOR
                    if normalized_kind == "tag"
                    else DEFAULT_DECK_COLOR,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM duel_catalog_entries WHERE kind = ? AND normalized_name = ?",
                (normalized_kind, normalized_name),
            ).fetchone()
        assert row is not None
        return _entry(row)

    def rename(self, entry_id: int, name: str) -> DuelCatalogEntry:
        current = self.get(entry_id)
        return self.update(
            entry_id,
            name=name,
            description=current.description,
            color=current.color,
        )

    def get(self, entry_id: int) -> DuelCatalogEntry:
        identifier = _entry_id(entry_id)
        with closing(connect_history_database(self.database_path)) as connection:
            current = connection.execute(
                "SELECT * FROM duel_catalog_entries WHERE entry_id = ?", (identifier,)
            ).fetchone()
        if current is None:
            raise DuelCatalogError(f"辞書項目が見つかりません: {identifier}")
        return _entry(current)

    def update(
        self,
        entry_id: int,
        *,
        name: str,
        description: str = "",
        color: str | None = None,
    ) -> DuelCatalogEntry:
        identifier = _entry_id(entry_id)
        normalized_description = _description(description)
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with (
                closing(connect_history_database(self.database_path)) as connection,
                connection,
            ):
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT * FROM duel_catalog_entries WHERE entry_id = ?",
                    (identifier,),
                ).fetchone()
                if current is None:
                    raise DuelCatalogError(f"辞書項目が見つかりません: {identifier}")
                display_name, normalized_name = _name(current["kind"], name)
                normalized_color = _color(current["kind"], color)
                cursor = connection.execute(
                    """
                    UPDATE duel_catalog_entries
                    SET name = ?, normalized_name = ?, description = ?, color = ?,
                        is_archived = 0, updated_at = ?
                    WHERE entry_id = ?
                    """,
                    (
                        display_name,
                        normalized_name,
                        normalized_description,
                        normalized_color,
                        timestamp,
                        identifier,
                    ),
                )
                if cursor.rowcount != 1:
                    raise DuelCatalogError(f"辞書項目が見つかりません: {identifier}")
                if current["name"] != display_name:
                    self._rename_references(
                        connection,
                        kind=current["kind"],
                        old_name=current["name"],
                        new_name=display_name,
                        new_normalized_name=normalized_name,
                        entry_id=identifier,
                        timestamp=timestamp,
                    )
                row = connection.execute(
                    "SELECT * FROM duel_catalog_entries WHERE entry_id = ?",
                    (identifier,),
                ).fetchone()
            assert row is not None
            return _entry(row)
        except sqlite3.IntegrityError as exc:
            raise DuelCatalogError(
                f"同じ{_kind_label(current['kind'])}が既にあります: {display_name}"
            ) from exc

    def update_deck(
        self,
        entry_id: int,
        *,
        name: str,
        description: str = "",
        color: str = DEFAULT_DECK_COLOR,
        opponent_only: bool = False,
        hidden_from_history_statistics: bool = False,
    ) -> DuelCatalogEntry:
        entry = self.update(entry_id, name=name, description=description, color=color)
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
            connection.execute(
                "UPDATE duel_catalog_entries SET opponent_only = ?, hidden_from_history_statistics = ? WHERE entry_id = ? AND kind = 'deck'",
                (
                    int(opponent_only),
                    int(hidden_from_history_statistics),
                    entry.entry_id,
                ),
            )
        return self.get(entry.entry_id)

    def update_tag(
        self,
        entry_id: int,
        *,
        name: str,
        description: str = "",
        color: str = DEFAULT_TAG_COLOR,
        deck_only: bool = False,
    ) -> DuelCatalogEntry:
        current = self.get(entry_id)
        if current.kind != "tag":
            raise DuelCatalogError("タグ以外の項目をタグとして更新できません")
        entry = self.update(entry_id, name=name, description=description, color=color)
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
            connection.execute(
                "UPDATE duel_catalog_entries SET deck_only = ? WHERE entry_id = ? AND kind = 'tag'",
                (int(deck_only), entry.entry_id),
            )
        return self.get(entry.entry_id)

    def list_deck_tags(self, deck_entry_id: int) -> tuple[DuelCatalogEntry, ...]:
        deck = self.get(deck_entry_id)
        if deck.kind != "deck":
            raise DuelCatalogError("デッキ名以外の項目にはデッキタグを設定できません")
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT tag.*
                FROM deck_tag_links AS link
                JOIN duel_catalog_entries AS tag ON tag.entry_id = link.tag_entry_id
                WHERE link.deck_entry_id = ?
                ORDER BY tag.normalized_name, tag.entry_id
                """,
                (deck.entry_id,),
            ).fetchall()
        return tuple(_entry(row) for row in rows)

    def list_deck_tag_ids(self, deck_entry_id: int) -> tuple[int, ...]:
        return tuple(entry.entry_id for entry in self.list_deck_tags(deck_entry_id))

    def set_deck_tags(
        self, deck_entry_id: int, tag_entry_ids: tuple[int, ...]
    ) -> tuple[DuelCatalogEntry, ...]:
        deck = self.get(deck_entry_id)
        if deck.kind != "deck":
            raise DuelCatalogError("デッキ名以外の項目にはデッキタグを設定できません")
        normalized_tag_ids = tuple(
            dict.fromkeys(_entry_id(item) for item in tag_entry_ids)
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
            connection.execute("BEGIN IMMEDIATE")
            if normalized_tag_ids:
                placeholders = ", ".join("?" for _ in normalized_tag_ids)
                rows = connection.execute(
                    "SELECT entry_id, kind FROM duel_catalog_entries "
                    f"WHERE entry_id IN ({placeholders})",
                    normalized_tag_ids,
                ).fetchall()
                found = {int(row["entry_id"]) for row in rows if row["kind"] == "tag"}
                missing = set(normalized_tag_ids) - found
                if missing:
                    raise DuelCatalogError("デッキタグに指定できない項目が含まれています")
            connection.execute(
                "DELETE FROM deck_tag_links WHERE deck_entry_id = ?",
                (deck.entry_id,),
            )
            connection.executemany(
                """
                INSERT INTO deck_tag_links(deck_entry_id, tag_entry_id, created_at)
                VALUES (?, ?, ?)
                """,
                tuple(
                    (deck.entry_id, tag_id, timestamp)
                    for tag_id in normalized_tag_ids
                ),
            )
        return self.list_deck_tags(deck.entry_id)

    def delete(self, entry_id: int) -> DuelCatalogEntry:
        identifier = _entry_id(entry_id)
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM duel_catalog_entries WHERE entry_id = ?", (identifier,)
            ).fetchone()
            if row is None:
                raise DuelCatalogError(f"辞書項目が見つかりません: {identifier}")
            if self._reference_count(connection, row) > 0:
                timestamp = datetime.now(timezone.utc).isoformat()
                connection.execute(
                    "UPDATE duel_catalog_entries SET is_archived = 1, updated_at = ? "
                    "WHERE entry_id = ?",
                    (timestamp, identifier),
                )
                updated = connection.execute(
                    "SELECT * FROM duel_catalog_entries WHERE entry_id = ?",
                    (identifier,),
                ).fetchone()
                assert updated is not None
                return _entry(updated)
            connection.execute(
                "DELETE FROM duel_catalog_entries WHERE entry_id = ?", (identifier,)
            )
            return _entry(row)

    def reference_count(self, entry_id: int) -> int:
        entry = self.get(entry_id)
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM duel_catalog_entries WHERE entry_id = ?",
                (entry.entry_id,),
            ).fetchone()
            assert row is not None
            return self._reference_count(connection, row)

    def recordings_for_tag(self, entry_id: int) -> tuple[str, ...]:
        entry = self.get(entry_id)
        if entry.kind != "tag":
            raise DuelCatalogError("タグ以外の項目では対戦記録を検索できません")
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT COALESCE(duel.recording_id, duel.duel_id) AS record_id "
                "FROM duel_record_tag_links AS link "
                "JOIN duel_records AS duel ON duel.duel_id = link.duel_id "
                "WHERE link.tag_entry_id = ? ORDER BY record_id",
                (entry.entry_id,),
            ).fetchall()
        return tuple(row["record_id"] for row in rows)

    @staticmethod
    def _reference_count(connection: sqlite3.Connection, row: sqlite3.Row) -> int:
        if row["kind"] == "tag":
            result = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM duel_record_tag_links WHERE tag_entry_id = ?)
                    + (SELECT COUNT(*) FROM deck_tag_links WHERE tag_entry_id = ?)
                """,
                (row["entry_id"], row["entry_id"]),
            ).fetchone()
            return int(result[0])
        result = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM duel_records WHERE own_deck = ? OR opponent_deck = ?)
                + (SELECT COUNT(*) FROM deck_tag_links WHERE deck_entry_id = ?)
            """,
            (row["name"], row["name"], row["entry_id"]),
        ).fetchone()
        return int(result[0])

    def deck_tags_by_deck(self) -> dict[int, tuple[DuelCatalogEntry, ...]]:
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT link.deck_entry_id, tag.*
                FROM deck_tag_links AS link
                JOIN duel_catalog_entries AS tag ON tag.entry_id = link.tag_entry_id
                ORDER BY link.deck_entry_id, tag.normalized_name, tag.entry_id
                """
            ).fetchall()
        grouped: dict[int, list[DuelCatalogEntry]] = {}
        for row in rows:
            grouped.setdefault(int(row["deck_entry_id"]), []).append(_entry(row))
        return {deck_id: tuple(tags) for deck_id, tags in grouped.items()}

    @staticmethod
    def _rename_references(
        connection: sqlite3.Connection,
        *,
        kind: str,
        old_name: str,
        new_name: str,
        new_normalized_name: str,
        entry_id: int,
        timestamp: str,
    ) -> None:
        if kind == "deck":
            connection.execute(
                "UPDATE duel_records SET own_deck = ?, updated_at = ? WHERE own_deck = ?",
                (new_name, timestamp, old_name),
            )
            connection.execute(
                "UPDATE duel_records SET opponent_deck = ?, updated_at = ? WHERE opponent_deck = ?",
                (new_name, timestamp, old_name),
            )
            connection.execute(
                "UPDATE duel_editor_preferences SET own_deck = ? WHERE own_deck = ?",
                (new_name, old_name),
            )
            connection.execute(
                "UPDATE duel_editor_preferences SET opponent_deck = ? WHERE opponent_deck = ?",
                (new_name, old_name),
            )
            return
        duel_rows = connection.execute(
            "SELECT duel_id FROM duel_record_tag_links WHERE tag_entry_id = ?",
            (entry_id,),
        ).fetchall()
        for duel_row in duel_rows:
            connection.execute(
                "UPDATE duel_record_tags SET tag = ?, normalized_tag = ? "
                "WHERE duel_id = ? AND normalized_tag = ?",
                (
                    new_name,
                    new_normalized_name,
                    duel_row["duel_id"],
                    unicodedata.normalize("NFC", old_name).casefold(),
                ),
            )
        preference = connection.execute(
            "SELECT tags_json FROM duel_editor_preferences WHERE singleton = 1"
        ).fetchone()
        if preference is not None:
            tags = json.loads(preference["tags_json"])
            updated = [new_name if item == old_name else item for item in tags]
            connection.execute(
                "UPDATE duel_editor_preferences SET tags_json = ?, updated_at = ? WHERE singleton = 1",
                (json.dumps(updated, ensure_ascii=False), timestamp),
            )

    def preferences(self) -> DuelEditorPreferences:
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM duel_editor_preferences WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return DuelEditorPreferences()
        try:
            tags = json.loads(row["tags_json"])
            if not isinstance(tags, list) or not all(
                isinstance(item, str) for item in tags
            ):
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
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
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
        description=row["description"],
        color=row["color"],
        is_archived=bool(row["is_archived"]),
        opponent_only=bool(row["opponent_only"]),
        hidden_from_history_statistics=bool(row["hidden_from_history_statistics"]),
        deck_only=bool(row["deck_only"]),
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
        raise DuelCatalogError(
            f"{_kind_label(kind)}は{maximum}文字以内で入力してください"
        )
    if any(
        unicodedata.category(character).startswith("C") for character in display_name
    ):
        raise DuelCatalogError(f"{_kind_label(kind)}に制御文字は使用できません")
    return display_name, display_name.casefold()


def _description(value: str) -> str:
    if not isinstance(value, str):
        raise DuelCatalogError("説明は文字列で入力してください")
    normalized = unicodedata.normalize("NFC", value.strip())
    if len(normalized) > MAX_DESCRIPTION_LENGTH:
        raise DuelCatalogError(
            f"説明は{MAX_DESCRIPTION_LENGTH}文字以内で入力してください"
        )
    if any(character in "\x00\r" for character in normalized):
        raise DuelCatalogError("説明に使用できない文字が含まれています")
    return normalized


def _color(kind: str, value: str | None) -> str | None:
    normalized = (
        (value or (DEFAULT_DECK_COLOR if kind == "deck" else DEFAULT_TAG_COLOR))
        .strip()
        .upper()
    )
    if TAG_COLOR_PATTERN.fullmatch(normalized) is None:
        raise DuelCatalogError("カラーは#RRGGBB形式で入力してください")
    return normalized


def _entry_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DuelCatalogError("entry_idは1以上の整数である必要があります")
    return value


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise DuelCatalogError("辞書項目の日時にタイムゾーンがありません")
    return parsed.astimezone(timezone.utc)
