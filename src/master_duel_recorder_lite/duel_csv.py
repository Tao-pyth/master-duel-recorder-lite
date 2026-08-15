from __future__ import annotations

from contextlib import closing
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import unicodedata
import uuid
from collections.abc import Iterable

from .data_protection import DataProtectionService
from .duel_catalog import DEFAULT_DECK_COLOR, DEFAULT_TAG_COLOR
from .duel_records import (
    COIN_FACES,
    DUEL_CHOICE_LABELS,
    DUEL_TYPES,
    MAX_DECK_LENGTH,
    MAX_NOTES_LENGTH,
    MAX_TAG_LENGTH,
    MAX_TAGS,
    PLAY_ORDERS,
    RESULTS,
)
from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


CSV_HEADERS = (
    "ID",
    "開始日時",
    "自分デッキ名",
    "相手デッキ名",
    "勝敗",
    "先後",
    "コイン",
    "対戦種別",
    "シーズン",
    "タグ",
    "メモ",
)
MAX_CSV_ROWS = 100_000
MAX_CSV_BYTES = 64 * 1024 * 1024
MAX_SEASON_NAME_LENGTH = 100


class DuelCsvError(RuntimeError):
    """CSVを安全に解析または適用できない場合のエラーです。"""


@dataclass(frozen=True)
class DuelCsvIssue:
    row_number: int
    column: str
    message: str


@dataclass(frozen=True)
class DuelCsvRow:
    row_number: int
    requested_id: str
    occurred_at: datetime
    own_deck: str
    opponent_deck: str
    result: str
    play_order: str
    coin_face: str
    duel_type: str
    season: str
    tags: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class DuelCsvPreview:
    source: Path
    rows: tuple[DuelCsvRow, ...]
    issues: tuple[DuelCsvIssue, ...]
    create_count: int
    update_count: int
    reassigned_id_count: int
    new_decks: tuple[str, ...]
    new_tags: tuple[str, ...]
    new_seasons: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class DuelCsvImportResult:
    source: Path
    created_ids: tuple[str, ...]
    updated_ids: tuple[str, ...]
    reassigned_ids: tuple[tuple[int, str, str], ...]
    created_decks: tuple[str, ...]
    created_tags: tuple[str, ...]
    created_seasons: tuple[str, ...]
    backup_path: Path


class DuelCsvService:
    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        self.database_path = (paths.db / HISTORY_DATABASE_NAME).resolve()
        connect_history_database(self.database_path).close()

    def export(self, destination: Path) -> Path:
        target = destination.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_history_database(self.database_path)) as connection:
            records = connection.execute(
                "SELECT duel.*, season.name AS season_name "
                "FROM duel_records AS duel "
                "LEFT JOIN seasons AS season ON season.season_id = duel.season_id "
                "ORDER BY duel.occurred_at, duel.duel_id"
            ).fetchall()
            rows = [self._export_row(connection, row) for row in records]
        self._atomic_write(target, rows)
        return target

    def export_sample(self, destination: Path) -> Path:
        target = destination.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            (
                "",
                "2026-01-01T12:00:00+09:00",
                "自分のデッキ",
                "相手のデッキ",
                "勝ち",
                "先攻",
                "表",
                "ランク戦",
                "シーズン名",
                "大会,振り返り",
                "改行やカンマを含むメモも入力できます。",
            )
        ]
        self._atomic_write(target, rows)
        return target

    def preview(self, source: Path) -> DuelCsvPreview:
        path = source.expanduser().resolve()
        rows, issues = self._parse(path)
        if issues:
            return DuelCsvPreview(path, rows, issues, 0, 0, 0, (), (), ())
        with closing(connect_history_database(self.database_path)) as connection:
            existing_ids = {
                str(row[0])
                for row in connection.execute("SELECT duel_id FROM duel_records")
            }
            deck_names = self._master_names(connection, "deck")
            tag_names = self._master_names(connection, "tag")
            season_names = {
                _key(str(row[0]))
                for row in connection.execute("SELECT normalized_name FROM seasons")
            }
        requested = [row.requested_id for row in rows if row.requested_id]
        duplicates = {value for value in requested if requested.count(value) > 1}
        if duplicates:
            duplicate_issues = tuple(
                DuelCsvIssue(row.row_number, "ID", "同一CSV内でIDが重複しています")
                for row in rows
                if row.requested_id in duplicates
            )
            return DuelCsvPreview(path, rows, duplicate_issues, 0, 0, 0, (), (), ())
        update_count = sum(row.requested_id in existing_ids for row in rows)
        reassigned = sum(
            bool(row.requested_id and row.requested_id not in existing_ids) for row in rows
        )
        new_decks = _missing_names(
            (name for row in rows for name in (row.own_deck, row.opponent_deck)),
            deck_names,
        )
        new_tags = _missing_names(
            (tag for row in rows for tag in row.tags), tag_names
        )
        new_seasons = _missing_names(
            (row.season for row in rows), season_names
        )
        return DuelCsvPreview(
            path,
            rows,
            (),
            len(rows) - update_count,
            update_count,
            reassigned,
            new_decks,
            new_tags,
            new_seasons,
        )

    def apply(self, preview: DuelCsvPreview) -> DuelCsvImportResult:
        if not preview.valid:
            raise DuelCsvError("エラーのあるCSVは取り込めません")
        if not preview.rows:
            raise DuelCsvError("取り込む戦績がありません")
        backup = DataProtectionService(self.paths).create_backup(
            "pre-csv-import", protected=True
        )
        created: list[str] = []
        updated: list[str] = []
        reassigned: list[tuple[int, str, str]] = []
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with closing(connect_history_database(self.database_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                deck_ids = self._resolve_catalog(
                    connection,
                    "deck",
                    tuple(
                        name
                        for row in preview.rows
                        for name in (row.own_deck, row.opponent_deck)
                        if name
                    ),
                    timestamp,
                )
                tag_ids = self._resolve_catalog(
                    connection,
                    "tag",
                    tuple(tag for row in preview.rows for tag in row.tags),
                    timestamp,
                )
                season_ids = self._resolve_seasons(connection, preview.rows, timestamp)
                for row in preview.rows:
                    existing = (
                        connection.execute(
                            "SELECT * FROM duel_records WHERE duel_id = ?",
                            (row.requested_id,),
                        ).fetchone()
                        if row.requested_id
                        else None
                    )
                    if existing is None:
                        duel_id = uuid.uuid4().hex
                        if row.requested_id:
                            reassigned.append((row.row_number, row.requested_id, duel_id))
                        self._insert_record(
                            connection,
                            duel_id,
                            row,
                            deck_ids,
                            season_ids,
                            timestamp,
                        )
                        created.append(duel_id)
                        before: dict[str, object] = {}
                        revision = 1
                    else:
                        duel_id = str(existing["duel_id"])
                        before = _audit_record(existing, self._read_tags(connection, duel_id))
                        revision = int(existing["revision"]) + 1
                        self._update_record(
                            connection,
                            existing,
                            row,
                            revision,
                            deck_ids,
                            season_ids,
                            timestamp,
                        )
                        updated.append(duel_id)
                    self._replace_tags(connection, duel_id, row.tags, tag_ids)
                    after_row = connection.execute(
                        "SELECT * FROM duel_records WHERE duel_id = ?", (duel_id,)
                    ).fetchone()
                    assert after_row is not None
                    after = _audit_record(
                        after_row, self._read_tags(connection, duel_id)
                    )
                    connection.execute(
                        """
                        INSERT INTO duel_record_changes (
                            duel_id, revision, source, before_json, after_json, changed_at
                        ) VALUES (?, ?, 'import', ?, ?, ?)
                        """,
                        (
                            duel_id,
                            revision,
                            _audit_json(before),
                            _audit_json(after),
                            timestamp,
                        ),
                    )
                connection.commit()
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise DuelCsvError(
                f"CSV取込をロールバックしました。データは変更されていません: {exc}"
            ) from exc
        return DuelCsvImportResult(
            preview.source,
            tuple(created),
            tuple(updated),
            tuple(reassigned),
            preview.new_decks,
            preview.new_tags,
            preview.new_seasons,
            backup.path,
        )

    def _parse(
        self, source: Path
    ) -> tuple[tuple[DuelCsvRow, ...], tuple[DuelCsvIssue, ...]]:
        if not source.is_file():
            raise DuelCsvError(f"CSVファイルが見つかりません: {source}")
        if source.stat().st_size > MAX_CSV_BYTES:
            raise DuelCsvError("CSVファイルが64MiBを超えています")
        try:
            raw = source.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise DuelCsvError(f"CSVをUTF-8として読み込めません: {exc}") from exc
        try:
            reader = csv.reader(io.StringIO(raw, newline=""), strict=True)
            header = next(reader, None)
            if tuple(header or ()) != CSV_HEADERS:
                return (), (
                    DuelCsvIssue(1, "ヘッダー", "11列の名称と順序が仕様と一致しません"),
                )
            rows: list[DuelCsvRow] = []
            issues: list[DuelCsvIssue] = []
            for row_number, values in enumerate(reader, start=2):
                if not any(value.strip() for value in values):
                    continue
                if len(rows) >= MAX_CSV_ROWS:
                    issues.append(
                        DuelCsvIssue(row_number, "行", "取込上限100000件を超えています")
                    )
                    break
                if len(values) != len(CSV_HEADERS):
                    issues.append(
                        DuelCsvIssue(row_number, "行", "列数が11列ではありません")
                    )
                    continue
                parsed, row_issues = _parse_row(row_number, values)
                issues.extend(row_issues)
                if parsed is not None:
                    rows.append(parsed)
        except csv.Error as exc:
            return (), (DuelCsvIssue(0, "CSV", f"CSV構文が不正です: {exc}"),)
        requested = [row.requested_id for row in rows if row.requested_id]
        duplicate_ids = {item for item in requested if requested.count(item) > 1}
        issues.extend(
            DuelCsvIssue(row.row_number, "ID", "同一CSV内でIDが重複しています")
            for row in rows
            if row.requested_id in duplicate_ids
        )
        return tuple(rows), tuple(issues)

    @staticmethod
    def _export_row(connection: sqlite3.Connection, row: sqlite3.Row) -> tuple[str, ...]:
        tags = tuple(
            str(item[0])
            for item in connection.execute(
                "SELECT tag FROM duel_record_tags WHERE duel_id = ? ORDER BY normalized_tag",
                (row["duel_id"],),
            )
        )
        return (
            str(row["duel_id"]),
            str(row["occurred_at"]),
            _spreadsheet_safe(str(row["own_deck"])),
            _spreadsheet_safe(str(row["opponent_deck"])),
            DUEL_CHOICE_LABELS["result"][str(row["result"])],
            DUEL_CHOICE_LABELS["play_order"][str(row["play_order"])],
            DUEL_CHOICE_LABELS["coin_face"][str(row["coin_face"])],
            DUEL_CHOICE_LABELS["duel_type"][str(row["duel_type"])],
            _spreadsheet_safe(str(row["season_name"] or "")),
            ",".join(_spreadsheet_safe(tag) for tag in tags),
            _spreadsheet_safe(str(row["notes"])),
        )

    @staticmethod
    def _atomic_write(target: Path, rows: list[tuple[str, ...]]) -> None:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\r\n")
                writer.writerow(CSV_HEADERS)
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise DuelCsvError(f"CSVを保存できません: {target}: {exc}") from exc

    @staticmethod
    def _master_names(connection: sqlite3.Connection, kind: str) -> set[str]:
        return {
            _key(str(row[0]))
            for row in connection.execute(
                "SELECT normalized_name FROM duel_catalog_entries WHERE kind = ?",
                (kind,),
            )
        }

    @staticmethod
    def _resolve_catalog(
        connection: sqlite3.Connection,
        kind: str,
        names: tuple[str, ...],
        timestamp: str,
    ) -> dict[str, int]:
        color = DEFAULT_DECK_COLOR if kind == "deck" else DEFAULT_TAG_COLOR
        for name in _unique_names(names):
            connection.execute(
                """
                INSERT INTO duel_catalog_entries (
                    kind, name, normalized_name, description, color, is_archived,
                    opponent_only, hidden_from_history_statistics, created_at, updated_at
                ) VALUES (?, ?, ?, '', ?, 0, 0, 0, ?, ?)
                ON CONFLICT(kind, normalized_name) DO NOTHING
                """,
                (kind, name, _key(name), color, timestamp, timestamp),
            )
        return {
            _key(str(row["name"])): int(row["entry_id"])
            for row in connection.execute(
                "SELECT entry_id, name FROM duel_catalog_entries WHERE kind = ?",
                (kind,),
            )
        }

    @staticmethod
    def _resolve_seasons(
        connection: sqlite3.Connection,
        rows: tuple[DuelCsvRow, ...],
        timestamp: str,
    ) -> dict[str, int]:
        by_name: dict[str, list[DuelCsvRow]] = {}
        for row in rows:
            if row.season:
                by_name.setdefault(_key(row.season), []).append(row)
        existing = {
            _key(str(row["name"])): int(row["season_id"])
            for row in connection.execute("SELECT season_id, name FROM seasons")
        }
        for key, grouped in by_name.items():
            if key in existing:
                continue
            display = grouped[0].season
            dates = [row.occurred_at.astimezone().date() for row in grouped]
            cursor = connection.execute(
                """
                INSERT INTO seasons (
                    name, normalized_name, season_type, duel_type, start_date, end_date,
                    description, report_notes, is_archived, created_at, updated_at
                ) VALUES (?, ?, 'custom', 'other', ?, ?, '', '', 0, ?, ?)
                """,
                (display, key, min(dates).isoformat(), max(dates).isoformat(), timestamp, timestamp),
            )
            existing[key] = int(cursor.lastrowid)
        return existing

    @staticmethod
    def _insert_record(
        connection: sqlite3.Connection,
        duel_id: str,
        row: DuelCsvRow,
        deck_ids: dict[str, int],
        season_ids: dict[str, int],
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO duel_records (
                duel_id, recording_id, entry_origin, occurred_at, status, result,
                play_order, coin_face, own_deck, opponent_deck, duel_type, notes,
                revision, created_at, updated_at, season_id, own_deck_id, opponent_deck_id
            ) VALUES (?, NULL, 'import', ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                duel_id,
                row.occurred_at.isoformat(),
                row.result,
                row.play_order,
                row.coin_face,
                row.own_deck,
                row.opponent_deck,
                row.duel_type,
                row.notes,
                timestamp,
                timestamp,
                season_ids.get(_key(row.season)) if row.season else None,
                deck_ids.get(_key(row.own_deck)) if row.own_deck else None,
                deck_ids.get(_key(row.opponent_deck)) if row.opponent_deck else None,
            ),
        )

    @staticmethod
    def _update_record(
        connection: sqlite3.Connection,
        existing: sqlite3.Row,
        row: DuelCsvRow,
        revision: int,
        deck_ids: dict[str, int],
        season_ids: dict[str, int],
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            UPDATE duel_records SET
                occurred_at = ?, result = ?, play_order = ?, coin_face = ?, own_deck = ?,
                opponent_deck = ?, duel_type = ?, notes = ?, revision = ?, updated_at = ?,
                season_id = ?, own_deck_id = ?, opponent_deck_id = ?
            WHERE duel_id = ? AND revision = ?
            """,
            (
                row.occurred_at.isoformat(),
                row.result,
                row.play_order,
                row.coin_face,
                row.own_deck,
                row.opponent_deck,
                row.duel_type,
                row.notes,
                revision,
                timestamp,
                season_ids.get(_key(row.season)) if row.season else None,
                deck_ids.get(_key(row.own_deck)) if row.own_deck else None,
                deck_ids.get(_key(row.opponent_deck)) if row.opponent_deck else None,
                existing["duel_id"],
                existing["revision"],
            ),
        )

    @staticmethod
    def _replace_tags(
        connection: sqlite3.Connection,
        duel_id: str,
        tags: tuple[str, ...],
        tag_ids: dict[str, int],
    ) -> None:
        connection.execute("DELETE FROM duel_record_tag_links WHERE duel_id = ?", (duel_id,))
        connection.execute("DELETE FROM duel_record_tags WHERE duel_id = ?", (duel_id,))
        connection.executemany(
            "INSERT INTO duel_record_tags(duel_id, tag, normalized_tag) VALUES (?, ?, ?)",
            ((duel_id, tag, _key(tag)) for tag in tags),
        )
        connection.executemany(
            "INSERT INTO duel_record_tag_links(duel_id, tag_entry_id) VALUES (?, ?)",
            ((duel_id, tag_ids[_key(tag)]) for tag in tags),
        )

    @staticmethod
    def _read_tags(connection: sqlite3.Connection, duel_id: str) -> tuple[str, ...]:
        return tuple(
            str(row[0])
            for row in connection.execute(
                "SELECT tag FROM duel_record_tags WHERE duel_id = ? ORDER BY normalized_tag",
                (duel_id,),
            )
        )


def _parse_row(
    row_number: int, values: list[str]
) -> tuple[DuelCsvRow | None, tuple[DuelCsvIssue, ...]]:
    issues: list[DuelCsvIssue] = []
    requested_id = values[0].strip()
    if requested_id and (len(requested_id) > 128 or any(char.isspace() for char in requested_id)):
        issues.append(DuelCsvIssue(row_number, "ID", "IDの形式が不正です"))
    try:
        occurred_at = datetime.fromisoformat(values[1].strip())
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.astimezone()
        occurred_at = occurred_at.astimezone(timezone.utc)
    except ValueError:
        issues.append(DuelCsvIssue(row_number, "開始日時", "ISO形式の日時を入力してください"))
        occurred_at = datetime.now(timezone.utc)
    own_deck = _text(values[2], MAX_DECK_LENGTH, row_number, "自分デッキ名", issues)
    opponent_deck = _text(values[3], MAX_DECK_LENGTH, row_number, "相手デッキ名", issues)
    result = _label_value(values[4], "result", RESULTS, row_number, "勝敗", issues)
    play_order = _label_value(values[5], "play_order", PLAY_ORDERS, row_number, "先後", issues)
    coin_face = _label_value(values[6], "coin_face", COIN_FACES, row_number, "コイン", issues)
    duel_type = _label_value(values[7], "duel_type", DUEL_TYPES, row_number, "対戦種別", issues)
    season = _text(values[8], MAX_SEASON_NAME_LENGTH, row_number, "シーズン", issues)
    raw_tags = [_spreadsheet_restore(item.strip()) for item in values[9].split(",")]
    tags = tuple(item for item in raw_tags if item)
    if len(tags) > MAX_TAGS:
        issues.append(DuelCsvIssue(row_number, "タグ", f"タグは{MAX_TAGS}件以内です"))
    normalized_tags: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        normalized = unicodedata.normalize("NFC", tag.strip())
        if len(normalized) > MAX_TAG_LENGTH:
            issues.append(
                DuelCsvIssue(row_number, "タグ", f"タグは{MAX_TAG_LENGTH}文字以内です")
            )
            continue
        key = _key(normalized)
        if key not in seen:
            seen.add(key)
            normalized_tags.append(normalized)
    notes = _text(values[10], MAX_NOTES_LENGTH, row_number, "メモ", issues, multiline=True)
    if issues:
        return None, tuple(issues)
    return (
        DuelCsvRow(
            row_number,
            requested_id,
            occurred_at,
            own_deck,
            opponent_deck,
            result,
            play_order,
            coin_face,
            duel_type,
            season,
            tuple(normalized_tags),
            notes,
        ),
        (),
    )


def _label_value(
    raw: str,
    field: str,
    allowed: set[str],
    row_number: int,
    column: str,
    issues: list[DuelCsvIssue],
) -> str:
    value = raw.strip()
    if not value:
        return "other" if field == "duel_type" else "unknown"
    reverse = {label: key for key, label in DUEL_CHOICE_LABELS[field].items()}
    normalized = reverse.get(value, value.casefold())
    if normalized not in allowed:
        issues.append(DuelCsvIssue(row_number, column, f"未対応の値です: {value}"))
        return "other" if field == "duel_type" else "unknown"
    return normalized


def _text(
    raw: str,
    maximum: int,
    row_number: int,
    column: str,
    issues: list[DuelCsvIssue],
    *,
    multiline: bool = False,
) -> str:
    value = unicodedata.normalize("NFC", _spreadsheet_restore(raw).strip())
    if len(value) > maximum:
        issues.append(DuelCsvIssue(row_number, column, f"{maximum}文字以内で入力してください"))
    if any(ord(char) < 32 and (not multiline or char not in "\n\t") for char in value):
        issues.append(DuelCsvIssue(row_number, column, "制御文字は使用できません"))
    return value


def _spreadsheet_safe(value: str) -> str:
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def _spreadsheet_restore(value: str) -> str:
    return value[1:] if len(value) > 1 and value[0] == "'" and value[1] in "=+-@" else value


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def _unique_names(names: tuple[str, ...]) -> tuple[str, ...]:
    result: dict[str, str] = {}
    for name in names:
        if name:
            result.setdefault(_key(name), unicodedata.normalize("NFC", name.strip()))
    return tuple(result.values())


def _missing_names(names: Iterable[str], existing: set[str]) -> tuple[str, ...]:
    found: dict[str, str] = {}
    for raw in names:
        name = str(raw).strip()
        if name and _key(name) not in existing:
            found.setdefault(_key(name), name)
    return tuple(found.values())


def _audit_record(row: sqlite3.Row, tags: tuple[str, ...]) -> dict[str, object]:
    return {
        "duel_id": str(row["duel_id"]),
        "recording_id": row["recording_id"],
        "entry_origin": str(row["entry_origin"]),
        "occurred_at": str(row["occurred_at"]),
        "status": str(row["status"]),
        "result": str(row["result"]),
        "play_order": str(row["play_order"]),
        "coin_face": str(row["coin_face"]),
        "own_deck": str(row["own_deck"]),
        "opponent_deck": str(row["opponent_deck"]),
        "duel_type": str(row["duel_type"]),
        "tags": list(tags),
        "notes": str(row["notes"]),
        "season_id": row["season_id"],
        "revision": int(row["revision"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _audit_json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
