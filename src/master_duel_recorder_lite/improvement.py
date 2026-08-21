from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
import zipfile
import hashlib

from .duel_records import DuelRecord
from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .recording_history import RecordingHistoryEntry
from .runtime_paths import RuntimePaths


class ImprovementError(RuntimeError):
    """V1.3.0の改善支援データを安全に扱えない場合のエラーです。"""


@dataclass(frozen=True)
class TagTemplate:
    template_id: str
    name: str
    tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PracticeGoal:
    goal_id: str
    title: str
    metric: str
    target_value: float
    current_value: float
    own_deck: str | None
    opponent_deck: str | None
    season_name: str | None
    status: str
    notes: str
    created_at: datetime
    updated_at: datetime

    @property
    def progress_ratio(self) -> float:
        if self.target_value <= 0:
            return 0.0
        return min(1.0, self.current_value / self.target_value)


class ImprovementRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connection = connect_history_database(self.database_path)
        connection.close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> ImprovementRepository:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def create_tag_template(self, *, name: str, tags: Iterable[str]) -> TagTemplate:
        now = datetime.now(timezone.utc)
        template = TagTemplate(
            f"tmpl-{uuid.uuid4().hex}",
            _text(name, 80, "name"),
            _tags(tags),
            now,
            now,
        )
        with closing(connect_history_database(self.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO tag_templates(template_id, name, tags_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    template.template_id,
                    template.name,
                    json.dumps(list(template.tags), ensure_ascii=False),
                    template.created_at.isoformat(),
                    template.updated_at.isoformat(),
                ),
            )
        return template

    def list_tag_templates(self) -> tuple[TagTemplate, ...]:
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT * FROM tag_templates ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return tuple(_tag_template(row) for row in rows)

    def create_goal(
        self,
        *,
        title: str,
        metric: str,
        target_value: float,
        own_deck: str | None = None,
        opponent_deck: str | None = None,
        season_name: str | None = None,
        notes: str = "",
    ) -> PracticeGoal:
        now = datetime.now(timezone.utc)
        goal = PracticeGoal(
            f"goal-{uuid.uuid4().hex}",
            _text(title, 120, "title"),
            _text(metric, 80, "metric"),
            _positive_number(target_value, "target_value"),
            0.0,
            _optional_text(own_deck, 100),
            _optional_text(opponent_deck, 100),
            _optional_text(season_name, 120),
            "active",
            _optional_text(notes, 1000) or "",
            now,
            now,
        )
        with closing(connect_history_database(self.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO practice_goals(
                    goal_id, title, metric, target_value, current_value,
                    own_deck, opponent_deck, season_name, status, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    goal.goal_id,
                    goal.title,
                    goal.metric,
                    goal.target_value,
                    goal.current_value,
                    goal.own_deck,
                    goal.opponent_deck,
                    goal.season_name,
                    goal.status,
                    goal.notes,
                    goal.created_at.isoformat(),
                    goal.updated_at.isoformat(),
                ),
            )
        return goal

    def list_goals(self, *, status: str | None = None) -> tuple[PracticeGoal, ...]:
        with closing(connect_history_database(self.database_path)) as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM practice_goals ORDER BY updated_at DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM practice_goals WHERE status = ? ORDER BY updated_at DESC",
                    (status,),
                ).fetchall()
        return tuple(_goal(row) for row in rows)


@dataclass(frozen=True)
class InputSuggestion:
    field: str
    value: str
    reason: str
    score: float


def suggest_duel_inputs(records: Iterable[DuelRecord]) -> tuple[InputSuggestion, ...]:
    recent = list(records)[:50]
    counters: dict[str, Counter[str]] = {
        "own_deck": Counter(),
        "opponent_deck": Counter(),
        "tags": Counter(),
    }
    for record in recent:
        if record.values.own_deck:
            counters["own_deck"][record.values.own_deck] += 1
        if record.values.opponent_deck:
            counters["opponent_deck"][record.values.opponent_deck] += 1
        counters["tags"].update(record.values.tags)
    suggestions: list[InputSuggestion] = []
    for field, counter in counters.items():
        for value, count in counter.most_common(5):
            suggestions.append(
                InputSuggestion(field, value, f"最近{count}回使用", float(count))
            )
    priority = {"own_deck": 0, "opponent_deck": 1, "tags": 2}
    return tuple(
        sorted(
            suggestions,
            key=lambda item: (-item.score, priority[item.field], item.value),
        )
    )


@dataclass(frozen=True)
class DeckImprovementRow:
    own_deck: str
    opponent_deck: str
    total: int
    wins: int
    losses: int
    first_total: int
    second_total: int
    coin_heads: int
    coin_tails: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.total if self.total else 0.0


def deck_improvement_rows(records: Iterable[DuelRecord]) -> tuple[DeckImprovementRow, ...]:
    buckets: dict[tuple[str, str], list[DuelRecord]] = defaultdict(list)
    for record in records:
        own = record.values.own_deck or "未設定"
        opponent = record.values.opponent_deck or "未設定"
        buckets[(own, opponent)].append(record)
    rows = []
    for (own, opponent), items in buckets.items():
        rows.append(
            DeckImprovementRow(
                own,
                opponent,
                len(items),
                sum(1 for item in items if item.values.result == "win"),
                sum(1 for item in items if item.values.result == "loss"),
                sum(1 for item in items if item.values.play_order == "first"),
                sum(1 for item in items if item.values.play_order == "second"),
                sum(1 for item in items if item.values.coin_face == "heads"),
                sum(1 for item in items if item.values.coin_face == "tails"),
            )
        )
    return tuple(sorted(rows, key=lambda item: (item.win_rate, -item.total, item.own_deck)))


@dataclass(frozen=True)
class StorageCandidate:
    recording_id: str
    category: str
    size_bytes: int
    reason: str


def storage_candidates(
    histories: Iterable[RecordingHistoryEntry],
    duel_records: Iterable[DuelRecord],
) -> tuple[StorageCandidate, ...]:
    recorded_duels = {record.recording_id for record in duel_records if record.recording_id}
    candidates: list[StorageCandidate] = []
    for item in histories:
        if item.size_bytes <= 0:
            continue
        if item.state == "failed":
            candidates.append(
                StorageCandidate(item.recording_id, "failed", item.size_bytes, "失敗録画")
            )
        elif item.recording_id not in recorded_duels:
            candidates.append(
                StorageCandidate(item.recording_id, "incomplete", item.size_bytes, "戦績未入力")
            )
    return tuple(sorted(candidates, key=lambda item: (-item.size_bytes, item.recording_id)))


@dataclass(frozen=True)
class ReviewTarget:
    recording_id: str
    has_recording: bool
    marker_count: int
    can_edit_duel: bool
    can_open_clip: bool


def review_target(
    *,
    recording_id: str,
    has_recording: bool,
    marker_count: int,
    has_duel: bool,
    has_clip: bool,
) -> ReviewTarget:
    return ReviewTarget(recording_id, has_recording, marker_count, has_duel, has_clip)


def export_migration_pack(paths: RuntimePaths, destination: Path) -> Path:
    target = destination.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    files: list[tuple[str, Path]] = []
    config_path = paths.config / "app.toml"
    db_path = paths.db / HISTORY_DATABASE_NAME
    if config_path.is_file():
        files.append(("config/app.toml", config_path))
    if db_path.is_file():
        files.append((f"db/{HISTORY_DATABASE_NAME}", db_path))
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contains_recordings": False,
        "contains_oauth_credentials": False,
        "files": [
            {"path": name, "sha256": _sha256(path), "size": path.stat().st_size}
            for name, path in files
        ],
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, path in files:
            archive.write(path, name)
    return target


def _tag_template(row: sqlite3.Row) -> TagTemplate:
    tags = tuple(json.loads(row["tags_json"]))
    return TagTemplate(
        row["template_id"],
        row["name"],
        tags,
        datetime.fromisoformat(row["created_at"]),
        datetime.fromisoformat(row["updated_at"]),
    )


def _goal(row: sqlite3.Row) -> PracticeGoal:
    return PracticeGoal(
        row["goal_id"],
        row["title"],
        row["metric"],
        float(row["target_value"]),
        float(row["current_value"]),
        row["own_deck"],
        row["opponent_deck"],
        row["season_name"],
        row["status"],
        row["notes"],
        datetime.fromisoformat(row["created_at"]),
        datetime.fromisoformat(row["updated_at"]),
    )


def _text(value: str, limit: int, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}は空にできません")
    normalized = value.strip()
    if len(normalized) > limit:
        raise ValueError(f"{name}は{limit}文字以内で指定してください")
    return normalized


def _optional_text(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > limit:
        raise ValueError(f"値は{limit}文字以内で指定してください")
    return normalized


def _tags(values: Iterable[str]) -> tuple[str, ...]:
    tags = tuple(dict.fromkeys(_text(value, 40, "tag") for value in values))
    if len(tags) > 20:
        raise ValueError("tagは20件以内で指定してください")
    return tags


def _positive_number(value: float, name: str) -> float:
    number = float(value)
    if number <= 0:
        raise ValueError(f"{name}は0より大きい値で指定してください")
    return number


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
