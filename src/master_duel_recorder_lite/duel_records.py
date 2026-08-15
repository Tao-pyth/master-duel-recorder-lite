from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import unicodedata
import uuid

from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


STATUSES = {"draft", "confirmed"}
RESULTS = {"win", "loss", "draw", "unknown"}
PLAY_ORDERS = {"first", "second", "unknown"}
COIN_FACES = {"heads", "tails", "unknown"}
DUEL_TYPES = {"ranked", "event", "room", "solo", "other"}
SOURCES = {"user", "system", "detected", "import"}
DUEL_CHOICE_LABELS = {
    "status": {
        "draft": "編集中",
        "confirmed": "確認済み",
    },
    "result": {
        "unknown": "未設定",
        "win": "勝ち",
        "loss": "負け",
        "draw": "引き分け",
    },
    "play_order": {
        "unknown": "未設定",
        "first": "先攻",
        "second": "後攻",
    },
    "coin_face": {
        "unknown": "未設定",
        "heads": "表",
        "tails": "裏",
    },
    "duel_type": {
        "ranked": "ランク戦",
        "event": "イベント",
        "room": "ルーム戦",
        "solo": "ソロモード",
        "other": "その他",
    },
}
MAX_DECK_LENGTH = 100
MAX_NOTES_LENGTH = 4000
MAX_TAG_LENGTH = 40
MAX_TAGS = 20


class DuelRecordError(RuntimeError):
    """対戦記録を安全に読み書きできない場合のエラーです。"""


class DuelRecordConflictError(DuelRecordError):
    """別の更新が先に確定していた場合のエラーです。"""


@dataclass(frozen=True)
class DuelRecordValues:
    status: str = "draft"
    result: str = "unknown"
    play_order: str = "unknown"
    coin_face: str = "unknown"
    own_deck: str = ""
    opponent_deck: str = ""
    duel_type: str = "other"
    tags: tuple[str, ...] = ()
    notes: str = ""
    season_id: int | None = None

    def normalized(self) -> DuelRecordValues:
        return DuelRecordValues(
            status=_choice(self.status, STATUSES, "status"),
            result=_choice(self.result, RESULTS, "result"),
            play_order=_choice(self.play_order, PLAY_ORDERS, "play_order"),
            coin_face=_choice(self.coin_face, COIN_FACES, "coin_face"),
            own_deck=_text(self.own_deck, MAX_DECK_LENGTH, "own_deck"),
            opponent_deck=_text(self.opponent_deck, MAX_DECK_LENGTH, "opponent_deck"),
            duel_type=_choice(self.duel_type, DUEL_TYPES, "duel_type"),
            tags=_tags(self.tags),
            notes=_text(self.notes, MAX_NOTES_LENGTH, "notes", multiline=True),
            season_id=_optional_id(self.season_id, "season_id"),
        )


@dataclass(frozen=True)
class DuelRecord:
    duel_id: str
    recording_id: str | None
    entry_origin: str
    occurred_at: datetime
    values: DuelRecordValues
    revision: int
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "duel_id": self.duel_id,
            "recording_id": self.recording_id,
            "entry_origin": self.entry_origin,
            "occurred_at": self.occurred_at.isoformat(),
            "status": self.values.status,
            "result": self.values.result,
            "play_order": self.values.play_order,
            "coin_face": self.values.coin_face,
            "own_deck": self.values.own_deck,
            "opponent_deck": self.values.opponent_deck,
            "duel_type": self.values.duel_type,
            "tags": list(self.values.tags),
            "notes": self.values.notes,
            "season_id": self.values.season_id,
            "revision": self.revision,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class DuelRecordChange:
    change_id: int
    duel_id: str
    revision: int
    source: str
    before: dict[str, object]
    after: dict[str, object]
    changed_at: datetime


class DuelRecordRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connection = connect_history_database(self.database_path)
        connection.close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> DuelRecordRepository:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def get(self, identifier: str) -> DuelRecord | None:
        normalized = _identifier(identifier)
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM duel_records WHERE duel_id = ? OR recording_id = ?",
                (normalized, normalized),
            ).fetchone()
            if row is None:
                return None
            tags = self._read_tags(connection, row["duel_id"])
        return _record(row, tags)

    def list(self, *, limit: int = 200, offset: int = 0) -> tuple[DuelRecord, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise ValueError("limitは1から1000の整数である必要があります")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offsetは0以上の整数である必要があります")
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT * FROM duel_records ORDER BY occurred_at DESC, duel_id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            records = tuple(
                _record(row, self._read_tags(connection, row["duel_id"]))
                for row in rows
            )
        return records

    def count_incomplete_recordings(self) -> int:
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM (
                    SELECT recording.recording_id AS identifier
                    FROM recordings AS recording
                    LEFT JOIN duel_records AS duel
                        ON duel.recording_id = recording.recording_id
                    WHERE recording.state = 'completed'
                      AND (duel.recording_id IS NULL OR duel.status <> 'confirmed')
                    UNION ALL
                    SELECT duel_id AS identifier
                    FROM duel_records
                    WHERE entry_origin IN ('manual', 'import') AND status <> 'confirmed'
                )
                """
            ).fetchone()
        assert row is not None
        return int(row["count"])

    def create_draft(self, recording_id: str, *, source: str = "system") -> DuelRecord:
        existing = self.get(recording_id)
        if existing is not None:
            return existing
        return self.save(
            recording_id,
            DuelRecordValues(),
            expected_revision=0,
            source=source,
        )

    def create_manual(
        self,
        values: DuelRecordValues,
        *,
        occurred_at: datetime,
        source: str = "user",
    ) -> DuelRecord:
        timestamp = _aware_datetime(occurred_at, "occurred_at")
        duel_id = uuid.uuid4().hex
        return self._save_record(
            duel_id,
            None,
            "manual",
            timestamp,
            values,
            expected_revision=0,
            source=source,
        )

    def save(
        self,
        recording_id: str,
        values: DuelRecordValues,
        *,
        expected_revision: int,
        source: str = "user",
    ) -> DuelRecord:
        identifier = _identifier(recording_id)
        current = self.get(identifier)
        if current is not None:
            return self._save_record(
                current.duel_id,
                current.recording_id,
                current.entry_origin,
                current.occurred_at,
                values,
                expected_revision=expected_revision,
                source=source,
            )
        with closing(connect_history_database(self.database_path)) as connection:
            recording = connection.execute(
                "SELECT COALESCE(started_at, created_at) AS occurred_at FROM recordings WHERE recording_id = ?",
                (identifier,),
            ).fetchone()
        if recording is None:
            raise DuelRecordError(f"録画履歴が見つかりません: {identifier}")
        return self._save_record(
            uuid.uuid4().hex,
            identifier,
            "recording",
            _datetime(recording["occurred_at"]),
            values,
            expected_revision=expected_revision,
            source=source,
        )

    def update(
        self,
        duel_id: str,
        values: DuelRecordValues,
        *,
        expected_revision: int,
        occurred_at: datetime | None = None,
        source: str = "user",
    ) -> DuelRecord:
        current = self.get(duel_id)
        if current is None or current.duel_id != _identifier(duel_id):
            raise DuelRecordError(f"対戦記録が見つかりません: {duel_id}")
        if occurred_at is not None and current.entry_origin not in {"manual", "import"}:
            raise DuelRecordError("録画付き対戦の対戦日時は変更できません")
        return self._save_record(
            current.duel_id,
            current.recording_id,
            current.entry_origin,
            _aware_datetime(occurred_at, "occurred_at") if occurred_at else current.occurred_at,
            values,
            expected_revision=expected_revision,
            source=source,
        )

    def delete_manual(self, duel_id: str) -> DuelRecord:
        current = self.get(duel_id)
        if current is None or current.duel_id != _identifier(duel_id):
            raise DuelRecordError(f"対戦記録が見つかりません: {duel_id}")
        if current.recording_id is not None:
            raise DuelRecordError("録画付き戦績は録画履歴の削除を使用してください")
        with closing(connect_history_database(self.database_path)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM duel_record_tag_links WHERE duel_id = ?", (current.duel_id,)
            )
            connection.execute(
                "DELETE FROM duel_record_changes WHERE duel_id = ?", (current.duel_id,)
            )
            connection.execute("DELETE FROM duel_records WHERE duel_id = ?", (current.duel_id,))
        return current

    def _save_record(
        self,
        duel_id: str,
        recording_id: str | None,
        entry_origin: str,
        occurred_at: datetime,
        values: DuelRecordValues,
        *,
        expected_revision: int,
        source: str,
    ) -> DuelRecord:
        identifier = _identifier(duel_id)
        normalized = values.normalized()
        normalized_source = _choice(source, SOURCES, "source")
        if isinstance(expected_revision, bool) or not isinstance(
            expected_revision, int
        ):
            raise ValueError("expected_revisionは整数である必要があります")
        if expected_revision < 0:
            raise ValueError("expected_revisionは0以上である必要があります")
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        try:
            with (
                closing(connect_history_database(self.database_path)) as connection,
                connection,
            ):
                connection.execute("BEGIN IMMEDIATE")
                current_row = connection.execute(
                    "SELECT * FROM duel_records WHERE duel_id = ?", (identifier,)
                ).fetchone()
                current_tags = (
                    self._read_tags(connection, identifier)
                    if current_row is not None
                    else ()
                )
                current = (
                    _record(current_row, current_tags)
                    if current_row is not None
                    else None
                )
                actual_revision = current.revision if current is not None else 0
                if actual_revision != expected_revision:
                    raise DuelRecordConflictError(
                        f"対戦記録は別の操作で更新されています: expected={expected_revision}, actual={actual_revision}"
                    )
                if normalized_source == "detected" and current is not None:
                    raise DuelRecordConflictError(
                        "自動判定は既存の対戦記録を上書きできません。候補として確認してください"
                    )
                revision = actual_revision + 1
                own_deck_id = self._deck_id(connection, normalized.own_deck, timestamp)
                opponent_deck_id = self._deck_id(
                    connection, normalized.opponent_deck, timestamp
                )
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO duel_records (
                            duel_id, recording_id, entry_origin, occurred_at,
                            status, result, play_order, coin_face, own_deck,
                            opponent_deck, duel_type, notes, revision, created_at, updated_at,
                            season_id, own_deck_id, opponent_deck_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            identifier,
                            recording_id,
                            entry_origin,
                            occurred_at.isoformat(),
                            normalized.status,
                            normalized.result,
                            normalized.play_order,
                            normalized.coin_face,
                            normalized.own_deck,
                            normalized.opponent_deck,
                            normalized.duel_type,
                            normalized.notes,
                            revision,
                            timestamp,
                            timestamp,
                            normalized.season_id,
                            own_deck_id,
                            opponent_deck_id,
                        ),
                    )
                    created_at = now
                else:
                    cursor = connection.execute(
                        """
                        UPDATE duel_records
                        SET occurred_at = ?, status = ?, result = ?, play_order = ?, coin_face = ?,
                            own_deck = ?,
                            opponent_deck = ?, duel_type = ?, notes = ?, revision = ?, updated_at = ?,
                            season_id = ?, own_deck_id = ?, opponent_deck_id = ?
                        WHERE duel_id = ? AND revision = ?
                        """,
                        (
                            occurred_at.isoformat(),
                            normalized.status,
                            normalized.result,
                            normalized.play_order,
                            normalized.coin_face,
                            normalized.own_deck,
                            normalized.opponent_deck,
                            normalized.duel_type,
                            normalized.notes,
                            revision,
                            timestamp,
                            normalized.season_id,
                            own_deck_id,
                            opponent_deck_id,
                            identifier,
                            expected_revision,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise DuelRecordConflictError(
                            "対戦記録の更新競合を検出しました"
                        )
                    created_at = current.created_at
                connection.execute(
                    "DELETE FROM duel_record_tags WHERE duel_id = ?", (identifier,)
                )
                connection.execute(
                    "DELETE FROM duel_record_tag_links WHERE duel_id = ?",
                    (identifier,),
                )
                connection.executemany(
                    "INSERT INTO duel_record_tags(duel_id, tag, normalized_tag) VALUES (?, ?, ?)",
                    ((identifier, tag, _tag_key(tag)) for tag in normalized.tags),
                )
                for tag in normalized.tags:
                    normalized_tag = _tag_key(tag)
                    connection.execute(
                        """
                        INSERT INTO duel_catalog_entries (
                            kind, name, normalized_name, description, color,
                            is_archived, created_at, updated_at
                        ) VALUES ('tag', ?, ?, '', '#4F6F8F', 0, ?, ?)
                        ON CONFLICT(kind, normalized_name) DO UPDATE SET is_archived = 0
                        """,
                        (tag, normalized_tag, timestamp, timestamp),
                    )
                    catalog_row = connection.execute(
                        "SELECT entry_id FROM duel_catalog_entries "
                        "WHERE kind = 'tag' AND normalized_name = ?",
                        (normalized_tag,),
                    ).fetchone()
                    assert catalog_row is not None
                    connection.execute(
                        "INSERT INTO duel_record_tag_links(duel_id, tag_entry_id) VALUES (?, ?)",
                        (identifier, catalog_row["entry_id"]),
                    )
                saved = DuelRecord(
                    identifier, recording_id, entry_origin, occurred_at,
                    normalized, revision, created_at, now
                )
                before = current.to_dict() if current is not None else {}
                connection.execute(
                    """
                    INSERT INTO duel_record_changes (
                        duel_id, revision, source, before_json, after_json, changed_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        revision,
                        normalized_source,
                        _audit_json(before),
                        _audit_json(saved.to_dict()),
                        timestamp,
                    ),
                )
                connection.commit()
                return saved
        except DuelRecordError:
            raise
        except sqlite3.Error as exc:
            raise DuelRecordError(f"対戦記録を保存できません: {exc}") from exc

    def confirm(self, recording_id: str, *, expected_revision: int) -> DuelRecord:
        current = self.get(recording_id)
        if current is None:
            raise DuelRecordError(f"対戦記録が見つかりません: {recording_id}")
        values = DuelRecordValues(**{**current.values.__dict__, "status": "confirmed"})
        return self.save(
            recording_id,
            values,
            expected_revision=expected_revision,
            source="user",
        )

    def changes(self, identifier: str) -> tuple[DuelRecordChange, ...]:
        record = self.get(identifier)
        if record is None:
            return ()
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT * FROM duel_record_changes WHERE duel_id = ? ORDER BY revision DESC",
                (record.duel_id,),
            ).fetchall()
        return tuple(
            DuelRecordChange(
                change_id=row["change_id"],
                duel_id=row["duel_id"],
                revision=row["revision"],
                source=row["source"],
                before=json.loads(row["before_json"]),
                after=json.loads(row["after_json"]),
                changed_at=_datetime(row["changed_at"]),
            )
            for row in rows
        )

    @staticmethod
    def _deck_id(
        connection: sqlite3.Connection, name: str, timestamp: str
    ) -> int | None:
        if not name:
            return None
        key = unicodedata.normalize("NFC", name).casefold()
        connection.execute(
            """
            INSERT INTO duel_catalog_entries (
                kind, name, normalized_name, description, color, is_archived,
                opponent_only, hidden_from_history_statistics, created_at, updated_at
            ) VALUES ('deck', ?, ?, '', '#2F6B5F', 0, 0, 0, ?, ?)
            ON CONFLICT(kind, normalized_name) DO UPDATE SET is_archived = 0
            """,
            (name, key, timestamp, timestamp),
        )
        row = connection.execute(
            "SELECT entry_id FROM duel_catalog_entries WHERE kind = 'deck' AND normalized_name = ?",
            (key,),
        ).fetchone()
        assert row is not None
        return int(row["entry_id"])

    @staticmethod
    def _read_tags(
        connection: sqlite3.Connection, duel_id: str
    ) -> tuple[str, ...]:
        rows = connection.execute(
            "SELECT tag FROM duel_record_tags WHERE duel_id = ? ORDER BY rowid",
            (duel_id,),
        ).fetchall()
        return tuple(row["tag"] for row in rows)


def duel_choice_labels(field: str) -> tuple[str, ...]:
    try:
        return tuple(DUEL_CHOICE_LABELS[field].values())
    except KeyError as exc:
        raise ValueError(f"未対応の対戦記録項目です: {field}") from exc


def duel_choice_label(field: str, value: str) -> str:
    try:
        return DUEL_CHOICE_LABELS[field][value]
    except KeyError as exc:
        raise ValueError(f"未対応の対戦記録値です: {field}={value}") from exc


def duel_choice_value(field: str, label: str) -> str:
    try:
        labels = DUEL_CHOICE_LABELS[field]
    except KeyError as exc:
        raise ValueError(f"未対応の対戦記録項目です: {field}") from exc
    for value, candidate in labels.items():
        if candidate == label:
            return value
    raise ValueError(f"未対応の対戦記録表示名です: {field}={label}")


def _record(row: sqlite3.Row, tags: tuple[str, ...]) -> DuelRecord:
    return DuelRecord(
        duel_id=row["duel_id"],
        recording_id=row["recording_id"],
        entry_origin=row["entry_origin"],
        occurred_at=_datetime(row["occurred_at"]),
        values=DuelRecordValues(
            status=row["status"],
            result=row["result"],
            play_order=row["play_order"],
            coin_face=row["coin_face"],
            own_deck=row["own_deck"],
            opponent_deck=row["opponent_deck"],
            duel_type=row["duel_type"],
            tags=tags,
            notes=row["notes"],
            season_id=row["season_id"],
        ),
        revision=row["revision"],
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
    )


def _choice(value: str, allowed: set[str], key: str) -> str:
    normalized = unicodedata.normalize("NFC", value.strip()).casefold()
    if normalized not in allowed:
        raise ValueError(
            f"{key}は{', '.join(sorted(allowed))}のいずれかである必要があります"
        )
    return normalized


def _text(value: str, maximum: int, key: str, *, multiline: bool = False) -> str:
    normalized = unicodedata.normalize("NFC", value.strip())
    if len(normalized) > maximum:
        raise ValueError(f"{key}は{maximum}文字以内である必要があります")
    for character in normalized:
        if unicodedata.category(character).startswith("C") and not (
            multiline and character in {"\n", "\t"}
        ):
            raise ValueError(f"{key}に制御文字は使用できません")
    return normalized


def _tags(values: tuple[str, ...]) -> tuple[str, ...]:
    if len(values) > MAX_TAGS:
        raise ValueError(f"タグは{MAX_TAGS}件以内である必要があります")
    result: list[str] = []
    keys: set[str] = set()
    for value in values:
        tag = _text(value, MAX_TAG_LENGTH, "tag")
        if not tag:
            raise ValueError("空のタグは使用できません")
        key = _tag_key(tag)
        if key in keys:
            raise ValueError(f"重複したタグです: {tag}")
        keys.add(key)
        result.append(tag)
    return tuple(result)


def _tag_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _optional_id(value: int | None, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key}は1以上の整数である必要があります")
    return value


def _identifier(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 200:
        raise ValueError("recording_idが不正です")
    return normalized


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise DuelRecordError("対戦記録の日時にタイムゾーンがありません")
    return parsed.astimezone(timezone.utc)


def _aware_datetime(value: datetime, key: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{key}はタイムゾーン付き日時である必要があります")
    return value.astimezone(timezone.utc)


def _audit_json(value: dict[str, object]) -> str:
    allowed = {
        "recording_id",
        "duel_id",
        "entry_origin",
        "occurred_at",
        "status",
        "result",
        "play_order",
        "coin_face",
        "own_deck",
        "opponent_deck",
        "duel_type",
        "tags",
        "notes",
        "revision",
        "created_at",
        "updated_at",
        "season_id",
    }
    filtered = {key: item for key, item in value.items() if key in allowed}
    return json.dumps(
        filtered, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
