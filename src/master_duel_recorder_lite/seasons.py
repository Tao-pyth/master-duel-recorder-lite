from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import unicodedata

from .duel_records import DUEL_TYPES
from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


SEASON_TYPES = {"ranked", "event", "custom"}
MAX_SEASON_NAME_LENGTH = 100
MAX_SEASON_DESCRIPTION_LENGTH = 1000
MAX_REPORT_NOTES_LENGTH = 10000


class SeasonError(RuntimeError):
    """シーズンを安全に読み書きできない場合のエラーです。"""


@dataclass(frozen=True)
class Season:
    season_id: int
    name: str
    season_type: str
    duel_type: str
    start_date: date
    end_date: date
    description: str
    report_notes: str
    is_archived: bool
    created_at: datetime
    updated_at: datetime

    def contains(self, value: date) -> bool:
        return self.start_date <= value <= self.end_date


class SeasonRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connect_history_database(self.database_path).close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> SeasonRepository:
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def list(self, *, include_archived: bool = False) -> tuple[Season, ...]:
        where = "" if include_archived else " WHERE is_archived = 0"
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT * FROM seasons"
                + where
                + " ORDER BY start_date DESC, season_id DESC"
            ).fetchall()
        return tuple(_season(row) for row in rows)

    def get(self, season_id: int) -> Season:
        identifier = _identifier(season_id)
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM seasons WHERE season_id = ?", (identifier,)
            ).fetchone()
        if row is None:
            raise SeasonError(f"シーズンが見つかりません: {identifier}")
        return _season(row)

    def add(
        self,
        *,
        name: str,
        season_type: str,
        duel_type: str,
        start_date: date,
        end_date: date,
        description: str = "",
        report_notes: str = "",
    ) -> Season:
        values = _values(
            name,
            season_type,
            duel_type,
            start_date,
            end_date,
            description,
            report_notes,
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with (
                closing(connect_history_database(self.database_path)) as connection,
                connection,
            ):
                cursor = connection.execute(
                    """
                    INSERT INTO seasons (
                        name, normalized_name, season_type, duel_type, start_date, end_date,
                        description, report_notes, is_archived, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (*values, timestamp, timestamp),
                )
                identifier = int(cursor.lastrowid)
            return self.get(identifier)
        except sqlite3.IntegrityError as exc:
            raise SeasonError("同じシーズン名が既にあります") from exc

    def update(
        self,
        season_id: int,
        *,
        name: str,
        season_type: str,
        duel_type: str,
        start_date: date,
        end_date: date,
        description: str = "",
        report_notes: str = "",
    ) -> Season:
        identifier = _identifier(season_id)
        values = _values(
            name,
            season_type,
            duel_type,
            start_date,
            end_date,
            description,
            report_notes,
        )
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with (
                closing(connect_history_database(self.database_path)) as connection,
                connection,
            ):
                cursor = connection.execute(
                    """
                    UPDATE seasons SET name = ?, normalized_name = ?, season_type = ?, duel_type = ?,
                        start_date = ?, end_date = ?, description = ?, report_notes = ?,
                        is_archived = 0, updated_at = ? WHERE season_id = ?
                    """,
                    (*values, timestamp, identifier),
                )
                if cursor.rowcount != 1:
                    raise SeasonError(f"シーズンが見つかりません: {identifier}")
            return self.get(identifier)
        except sqlite3.IntegrityError as exc:
            raise SeasonError("同じシーズン名が既にあります") from exc

    def delete(self, season_id: int) -> Season:
        current = self.get(season_id)
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM duel_records WHERE season_id = ?",
                    (current.season_id,),
                ).fetchone()[0]
            )
            if count:
                connection.execute(
                    "UPDATE seasons SET is_archived = 1, updated_at = ? WHERE season_id = ?",
                    (datetime.now(timezone.utc).isoformat(), current.season_id),
                )
                row = connection.execute(
                    "SELECT * FROM seasons WHERE season_id = ?", (current.season_id,)
                ).fetchone()
                assert row is not None
                return _season(row)
            connection.execute(
                "DELETE FROM seasons WHERE season_id = ?", (current.season_id,)
            )
        return current


def _values(
    name: str,
    season_type: str,
    duel_type: str,
    start: date,
    end: date,
    description: str,
    notes: str,
) -> tuple[str, str, str, str, str, str, str, str]:
    display = unicodedata.normalize("NFC", name.strip())
    if not display or len(display) > MAX_SEASON_NAME_LENGTH:
        raise SeasonError(
            f"シーズン名は1から{MAX_SEASON_NAME_LENGTH}文字で入力してください"
        )
    if season_type not in SEASON_TYPES:
        raise SeasonError(f"未対応のシーズン種別です: {season_type}")
    if duel_type not in DUEL_TYPES:
        raise SeasonError(f"未対応の対戦種別です: {duel_type}")
    if not isinstance(start, date) or not isinstance(end, date) or start > end:
        raise SeasonError("開始日は終了日以前にしてください")
    description = unicodedata.normalize("NFC", description.strip())
    notes = unicodedata.normalize("NFC", notes.strip())
    if (
        len(description) > MAX_SEASON_DESCRIPTION_LENGTH
        or len(notes) > MAX_REPORT_NOTES_LENGTH
    ):
        raise SeasonError("説明またはレポートメモが長すぎます")
    return (
        display,
        display.casefold(),
        season_type,
        duel_type,
        start.isoformat(),
        end.isoformat(),
        description,
        notes,
    )


def _identifier(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SeasonError("season_idは1以上の整数で指定してください")
    return value


def _season(row: sqlite3.Row) -> Season:
    return Season(
        season_id=int(row["season_id"]),
        name=str(row["name"]),
        season_type=str(row["season_type"]),
        duel_type=str(row["duel_type"]),
        start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]),
        description=str(row["description"]),
        report_notes=str(row["report_notes"]),
        is_archived=bool(row["is_archived"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
