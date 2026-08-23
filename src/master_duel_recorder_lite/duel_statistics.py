from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
import unicodedata

from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


GRANULARITIES = {"day", "week", "month"}
PLAY_ORDER_FILTERS = {"first", "second"}
COIN_FACE_FILTERS = {"heads", "tails", "unknown"}


@dataclass(frozen=True)
class StatisticsFilter:
    date_from: date | None = None
    date_to: date | None = None
    own_deck: str | None = None
    tag_entry_id: int | None = None
    play_order: str | None = None
    coin_face: str | None = None
    season_id: int | None = None
    season_unassigned: bool = False

    def __post_init__(self) -> None:
        if self.date_from is not None and not isinstance(self.date_from, date):
            raise ValueError("date_fromはdateである必要があります")
        if self.date_to is not None and not isinstance(self.date_to, date):
            raise ValueError("date_toはdateである必要があります")
        if self.date_from is not None and self.date_to is not None:
            if self.date_from > self.date_to:
                raise ValueError("開始日は終了日以前である必要があります")
        if self.tag_entry_id is not None:
            if isinstance(self.tag_entry_id, bool) or not isinstance(
                self.tag_entry_id, int
            ):
                raise ValueError("tag_entry_idは整数である必要があります")
            if self.tag_entry_id <= 0:
                raise ValueError("tag_entry_idは1以上である必要があります")
        if self.play_order is not None and self.play_order not in PLAY_ORDER_FILTERS:
            raise ValueError(f"未対応の先後条件です: {self.play_order}")
        if self.coin_face is not None and self.coin_face not in COIN_FACE_FILTERS:
            raise ValueError(f"未対応のコインの面です: {self.coin_face}")
        if self.season_id is not None and (
            isinstance(self.season_id, bool)
            or not isinstance(self.season_id, int)
            or self.season_id < 1
        ):
            raise ValueError("season_idは1以上の整数である必要があります")
        if self.season_id is not None and self.season_unassigned:
            raise ValueError("シーズン指定と未設定指定は同時に使用できません")


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
    cumulative_metric: StatisticsMetric | None = None

    @property
    def cumulative_win_rate(self) -> float | None:
        metric = self.cumulative_metric or self.metric
        return metric.win_rate


@dataclass(frozen=True)
class StatisticsDashboard:
    overall: StatisticsMetric
    filtered: StatisticsMetric
    by_deck: tuple[StatisticsBreakdown, ...]
    by_play_order: tuple[StatisticsBreakdown, ...]
    by_deck_play_order: tuple[StatisticsBreakdown, ...]
    by_coin_face: tuple[StatisticsBreakdown, ...]
    by_season: tuple[StatisticsBreakdown, ...]
    trend: tuple[StatisticsTrendPoint, ...]
    filters: StatisticsFilter
    granularity: str


@dataclass(frozen=True)
class _StatisticsRow:
    occurred_at: datetime
    result: str
    play_order: str
    coin_face: str
    own_deck: str
    own_deck_id: int | None
    season_id: int | None


StatisticsRow = _StatisticsRow


class DuelStatisticsRepository:
    """確定済みの対戦記録だけを読み取り、統計へ変換します。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connection = connect_history_database(self.database_path)
        connection.close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> DuelStatisticsRepository:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
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
        filtered_rows = self.rows(selected)
        season_labels = self._season_labels()
        return StatisticsDashboard(
            overall=_metric(all_rows),
            filtered=_metric(filtered_rows),
            by_deck=_breakdown_by_deck(filtered_rows),
            by_play_order=_breakdown_by_play_order(filtered_rows),
            by_deck_play_order=_breakdown_by_deck_play_order(filtered_rows),
            by_coin_face=_breakdown_by_coin_face(filtered_rows),
            by_season=_breakdown_by_season(filtered_rows, season_labels),
            trend=_trend(filtered_rows, selected, granularity),
            filters=selected,
            granularity=granularity,
        )

    def _season_labels(self) -> dict[int, str]:
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute("SELECT season_id, name FROM seasons").fetchall()
        return {int(row["season_id"]): str(row["name"]) for row in rows}

    def rows(
        self, filters: StatisticsFilter | None = None
    ) -> tuple[StatisticsRow, ...]:
        selected = filters or StatisticsFilter()
        source_rows = self._read_rows(tag_entry_id=selected.tag_entry_id)
        return tuple(row for row in source_rows if _matches(row, selected))

    def _read_rows(
        self, *, tag_entry_id: int | None = None
    ) -> tuple[_StatisticsRow, ...]:
        tag_clause = ""
        parameters: tuple[object, ...] = ()
        if tag_entry_id is not None:
            tag_clause = """
                  AND EXISTS (
                      SELECT 1
                      FROM duel_record_tag_links AS tag_link
                      WHERE tag_link.duel_id = duel.duel_id
                        AND tag_link.tag_entry_id = ?
                  )
            """
            parameters = (tag_entry_id,)
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    duel.occurred_at,
                    duel.result,
                    duel.play_order,
                    duel.coin_face,
                    duel.own_deck,
                    duel.own_deck_id,
                    duel.season_id
                FROM duel_records AS duel
                LEFT JOIN recordings AS recording
                    ON recording.recording_id = duel.recording_id
                WHERE duel.status = 'confirmed'
                  AND duel.result IN ('win', 'loss', 'draw')
                  AND (
                      duel.entry_origin IN ('manual', 'import')
                      OR recording.state = 'completed'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM duel_catalog_entries AS hidden_deck
                      WHERE hidden_deck.entry_id = duel.own_deck_id
                        AND hidden_deck.hidden_from_history_statistics = 1
                  )
                  {tag_clause}
                ORDER BY duel.occurred_at, duel.duel_id
                """,
                parameters,
            ).fetchall()
        return tuple(
            _StatisticsRow(
                occurred_at=_parse_datetime(str(row["occurred_at"])),
                result=str(row["result"]),
                play_order=str(row["play_order"]),
                coin_face=str(row["coin_face"]),
                own_deck=str(row["own_deck"]),
                own_deck_id=row["own_deck_id"],
                season_id=row["season_id"],
            )
            for row in rows
        )


def _matches(row: _StatisticsRow, filters: StatisticsFilter) -> bool:
    local_date = statistics_local_date(row.occurred_at)
    if filters.date_from is not None and local_date < filters.date_from:
        return False
    if filters.date_to is not None and local_date > filters.date_to:
        return False
    if filters.own_deck and _normalized(row.own_deck) != _normalized(filters.own_deck):
        return False
    if filters.play_order is not None and row.play_order != filters.play_order:
        return False
    if filters.coin_face is not None and row.coin_face != filters.coin_face:
        return False
    if filters.season_id is not None and row.season_id != filters.season_id:
        return False
    if filters.season_unassigned and row.season_id is not None:
        return False
    return True


def _metric(
    rows: tuple[_StatisticsRow, ...] | list[_StatisticsRow],
) -> StatisticsMetric:
    wins = sum(row.result == "win" for row in rows)
    losses = sum(row.result == "loss" for row in rows)
    draws = sum(row.result == "draw" for row in rows)
    return StatisticsMetric(len(rows), wins, losses, draws)


def _breakdown_by_deck(
    rows: tuple[_StatisticsRow, ...],
) -> tuple[StatisticsBreakdown, ...]:
    grouped: dict[str, list[_StatisticsRow]] = defaultdict(list)
    labels: dict[str, str] = {}
    for row in rows:
        key = _normalized(row.own_deck) if row.own_deck.strip() else ""
        grouped[key].append(row)
        labels.setdefault(key, row.own_deck.strip() or "未設定")
    items = (
        StatisticsBreakdown(key, labels[key], _metric(grouped[key])) for key in grouped
    )
    return tuple(
        sorted(items, key=lambda item: (-item.metric.matches, item.label.casefold()))
    )


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


def _breakdown_by_deck_play_order(
    rows: tuple[_StatisticsRow, ...],
) -> tuple[StatisticsBreakdown, ...]:
    grouped: dict[tuple[str, str], list[_StatisticsRow]] = defaultdict(list)
    labels: dict[str, str] = {}
    order_labels = {"first": "先攻時", "second": "後攻時", "unknown": "未設定"}
    for row in rows:
        deck_key = _normalized(row.own_deck) if row.own_deck.strip() else ""
        labels.setdefault(deck_key, row.own_deck.strip() or "未設定")
        grouped[(deck_key, row.play_order)].append(row)
    result: list[StatisticsBreakdown] = []
    for deck_key in sorted(labels, key=lambda key: labels[key].casefold()):
        for play_order in ("first", "second", "unknown"):
            values = grouped.get((deck_key, play_order))
            if values:
                result.append(
                    StatisticsBreakdown(
                        f"{deck_key}:{play_order}",
                        f"{labels[deck_key]} {order_labels[play_order]}",
                        _metric(values),
                    )
                )
    return tuple(result)


def _breakdown_by_coin_face(
    rows: tuple[_StatisticsRow, ...],
) -> tuple[StatisticsBreakdown, ...]:
    labels = {"heads": "表", "tails": "裏", "unknown": "未設定"}
    return _breakdown_by_choice(rows, "coin_face", labels)


def _breakdown_by_season(
    rows: tuple[_StatisticsRow, ...], labels: dict[int, str]
) -> tuple[StatisticsBreakdown, ...]:
    grouped: dict[int | None, list[_StatisticsRow]] = defaultdict(list)
    for row in rows:
        grouped[row.season_id].append(row)
    ordered = sorted(
        grouped,
        key=lambda key: (key is None, labels.get(key, "シーズン未設定").casefold()),
    )
    return tuple(
        StatisticsBreakdown(
            "unassigned" if key is None else str(key),
            "シーズン未設定" if key is None else labels.get(key, f"削除済みシーズン {key}"),
            _metric(grouped[key]),
        )
        for key in ordered
    )


def _breakdown_by_choice(
    rows: tuple[_StatisticsRow, ...],
    field: str,
    labels: dict[str, str],
) -> tuple[StatisticsBreakdown, ...]:
    grouped: dict[str, list[_StatisticsRow]] = defaultdict(list)
    for row in rows:
        grouped[str(getattr(row, field))].append(row)
    return tuple(
        StatisticsBreakdown(key, label, _metric(grouped[key]))
        for key, label in labels.items()
        if key in grouped
    )


def _trend(
    rows: tuple[_StatisticsRow, ...],
    filters: StatisticsFilter,
    granularity: str,
) -> tuple[StatisticsTrendPoint, ...]:
    if not rows and filters.date_from is None and filters.date_to is None:
        return ()
    row_dates = tuple(statistics_local_date(row.occurred_at) for row in rows)
    start = filters.date_from or (min(row_dates) if row_dates else filters.date_to)
    end = filters.date_to or (max(row_dates) if row_dates else filters.date_from)
    if start is None or end is None:
        return ()
    first = _period_start(start, granularity)
    last = _period_start(end, granularity)
    grouped: dict[date, list[_StatisticsRow]] = defaultdict(list)
    for row in rows:
        grouped[_period_start(statistics_local_date(row.occurred_at), granularity)].append(
            row
        )
    points: list[StatisticsTrendPoint] = []
    cumulative_rows: list[_StatisticsRow] = []
    current = first
    while current <= last:
        period_rows = grouped[current]
        cumulative_rows.extend(period_rows)
        points.append(
            StatisticsTrendPoint(
                current,
                _period_label(current, granularity),
                _metric(period_rows),
                _metric(cumulative_rows),
            )
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


def statistics_local_date(value: datetime, zone: tzinfo | None = None) -> date:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("統計日時にはタイムゾーンが必要です")
    return value.astimezone(zone).date()


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()
