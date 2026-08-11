from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import unicodedata

from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


GRANULARITIES = {"day", "week", "month"}
PLAY_ORDER_FILTERS = {"first", "second"}


@dataclass(frozen=True)
class StatisticsFilter:
    date_from: date | None = None
    date_to: date | None = None
    own_deck: str | None = None
    tag_entry_id: int | None = None
    play_order: str | None = None

    def __post_init__(self) -> None:
        if self.date_from is not None and not isinstance(self.date_from, date):
            raise ValueError("date_fromはdateである必要があります")
        if self.date_to is not None and not isinstance(self.date_to, date):
            raise ValueError("date_toはdateである必要があります")
        if self.date_from is not None and self.date_to is not None:
            if self.date_from > self.date_to:
                raise ValueError("開始日は終了日以前である必要があります")
        if self.tag_entry_id is not None:
            if isinstance(self.tag_entry_id, bool) or not isinstance(self.tag_entry_id, int):
                raise ValueError("tag_entry_idは整数である必要があります")
            if self.tag_entry_id <= 0:
                raise ValueError("tag_entry_idは1以上である必要があります")
        if self.play_order is not None and self.play_order not in PLAY_ORDER_FILTERS:
            raise ValueError(f"未対応の先後条件です: {self.play_order}")


@dataclass(frozen=True)
class StatisticsMetric:
    matches: int
    wins: int
    losses: int
    draws: int

    @property
    def win_rate(self) -> float | None:
        if self.matches == 0:
            return None
        return self.wins / self.matches


@dataclass(frozen=True)
class StatisticsBreakdown:
    key: str
    label: str
    metric: StatisticsMetric


@dataclass(frozen=True)
class StatisticsTrendPoint:
    period_start: date
    label: str
    metric: StatisticsMetric


@dataclass(frozen=True)
class StatisticsDashboard:
    overall: StatisticsMetric
    filtered: StatisticsMetric
    by_deck: tuple[StatisticsBreakdown, ...]
    by_play_order: tuple[StatisticsBreakdown, ...]
    trend: tuple[StatisticsTrendPoint, ...]
    filters: StatisticsFilter
    granularity: str


@dataclass(frozen=True)
class _StatisticsRow:
    occurred_at: datetime
    result: str
    play_order: str
    own_deck: str


class DuelStatisticsRepository:
    """確定済みの対戦記録だけを読み取り、統計へ変換します。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connection = connect_history_database(self.database_path)
        connection.close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> DuelStatisticsRepository:
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def dashboard(
        self,
        filters: StatisticsFilter | None = None,
        *,
        granularity: str = "month",
    ) -> StatisticsDashboard:
        selected = filters or StatisticsFilter()
        if granularity not in GRANULARITIES:
            raise ValueError(f"未対応の集計単位です: {granularity}")
        all_rows = self._read_rows()
        source_rows = (
            self._read_rows(tag_entry_id=selected.tag_entry_id)
            if selected.tag_entry_id is not None
            else all_rows
        )
        filtered_rows = tuple(row for row in source_rows if _matches(row, selected))
        return StatisticsDashboard(
            overall=_metric(all_rows),
            filtered=_metric(filtered_rows),
            by_deck=_breakdown_by_deck(filtered_rows),
            by_play_order=_breakdown_by_play_order(filtered_rows),
            trend=_trend(filtered_rows, selected, granularity),
            filters=selected,
            granularity=granularity,
        )

    def _read_rows(self, *, tag_entry_id: int | None = None) -> tuple[_StatisticsRow, ...]:
        tag_clause = ""
        parameters: tuple[object, ...] = ()
        if tag_entry_id is not None:
            tag_clause = """
                  AND EXISTS (
                      SELECT 1
                      FROM duel_record_tag_links AS tag_link
                      WHERE tag_link.recording_id = recording.recording_id
                        AND tag_link.tag_entry_id = ?
                  )
            """
            parameters = (tag_entry_id,)
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    COALESCE(recording.started_at, recording.created_at) AS occurred_at,
                    duel.result,
                    duel.play_order,
                    duel.own_deck
                FROM recordings AS recording
                JOIN duel_records AS duel
                  ON duel.recording_id = recording.recording_id
                WHERE recording.state = 'completed'
                  AND duel.status = 'confirmed'
                  AND duel.result IN ('win', 'loss', 'draw')
                  {tag_clause}
                ORDER BY occurred_at, recording.recording_id
                """,
                parameters,
            ).fetchall()
        return tuple(
            _StatisticsRow(
                occurred_at=_parse_datetime(str(row["occurred_at"])),
                result=str(row["result"]),
                play_order=str(row["play_order"]),
                own_deck=str(row["own_deck"]),
            )
            for row in rows
        )

def _matches(row: _StatisticsRow, filters: StatisticsFilter) -> bool:
    local_date = row.occurred_at.astimezone().date()
    if filters.date_from is not None and local_date < filters.date_from:
        return False
    if filters.date_to is not None and local_date > filters.date_to:
        return False
    if filters.own_deck and _normalized(row.own_deck) != _normalized(filters.own_deck):
        return False
    if filters.play_order is not None and row.play_order != filters.play_order:
        return False
    return True


def _metric(rows: tuple[_StatisticsRow, ...] | list[_StatisticsRow]) -> StatisticsMetric:
    wins = sum(row.result == "win" for row in rows)
    losses = sum(row.result == "loss" for row in rows)
    draws = sum(row.result == "draw" for row in rows)
    return StatisticsMetric(len(rows), wins, losses, draws)


def _breakdown_by_deck(rows: tuple[_StatisticsRow, ...]) -> tuple[StatisticsBreakdown, ...]:
    grouped: dict[str, list[_StatisticsRow]] = defaultdict(list)
    labels: dict[str, str] = {}
    for row in rows:
        key = _normalized(row.own_deck) if row.own_deck.strip() else ""
        grouped[key].append(row)
        labels.setdefault(key, row.own_deck.strip() or "未設定")
    items = (
        StatisticsBreakdown(key, labels[key], _metric(grouped[key])) for key in grouped
    )
    return tuple(sorted(items, key=lambda item: (-item.metric.matches, item.label.casefold())))


def _breakdown_by_play_order(
    rows: tuple[_StatisticsRow, ...],
) -> tuple[StatisticsBreakdown, ...]:
    labels = {"first": "先攻", "second": "後攻", "unknown": "未設定"}
    grouped: dict[str, list[_StatisticsRow]] = defaultdict(list)
    for row in rows:
        grouped[row.play_order].append(row)
    return tuple(
        StatisticsBreakdown(key, labels.get(key, key), _metric(grouped[key]))
        for key in ("first", "second", "unknown")
        if key in grouped
    )


def _trend(
    rows: tuple[_StatisticsRow, ...],
    filters: StatisticsFilter,
    granularity: str,
) -> tuple[StatisticsTrendPoint, ...]:
    if not rows and filters.date_from is None and filters.date_to is None:
        return ()
    row_dates = tuple(row.occurred_at.astimezone().date() for row in rows)
    start = filters.date_from or (min(row_dates) if row_dates else filters.date_to)
    end = filters.date_to or (max(row_dates) if row_dates else filters.date_from)
    if start is None or end is None:
        return ()
    first = _period_start(start, granularity)
    last = _period_start(end, granularity)
    grouped: dict[date, list[_StatisticsRow]] = defaultdict(list)
    for row in rows:
        grouped[_period_start(row.occurred_at.astimezone().date(), granularity)].append(row)
    points: list[StatisticsTrendPoint] = []
    current = first
    while current <= last:
        points.append(
            StatisticsTrendPoint(current, _period_label(current, granularity), _metric(grouped[current]))
        )
        current = _next_period(current, granularity)
    return tuple(points)


def _period_start(value: date, granularity: str) -> date:
    if granularity == "day":
        return value
    if granularity == "week":
        return value - timedelta(days=value.weekday())
    return value.replace(day=1)


def _next_period(value: date, granularity: str) -> date:
    if granularity == "day":
        return value + timedelta(days=1)
    if granularity == "week":
        return value + timedelta(days=7)
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _period_label(value: date, granularity: str) -> str:
    if granularity == "day":
        return value.strftime("%m/%d")
    if granularity == "week":
        return f"{value.strftime('%m/%d')}週"
    return value.strftime("%Y/%m")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()
