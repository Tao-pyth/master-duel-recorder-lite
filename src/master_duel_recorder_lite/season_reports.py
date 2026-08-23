from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .duel_catalog import DuelCatalogRepository
from .duel_statistics import (
    DuelStatisticsRepository,
    StatisticsDashboard,
    StatisticsFilter,
    StatisticsMetric,
    StatisticsRow,
    StatisticsTrendPoint,
    statistics_local_date,
)
from .runtime_paths import RuntimePaths
from .seasons import Season, SeasonError, SeasonRepository


SMALL_SAMPLE_THRESHOLD = 10


@dataclass(frozen=True)
class SeasonComparison:
    current: StatisticsMetric
    comparison: StatisticsMetric | None
    match_delta: int | None
    win_rate_delta: float | None


@dataclass(frozen=True)
class DeckOrderBreakdown:
    deck_id: int | None
    deck_name: str
    color: str | None
    play_order: str
    label: str
    metric: StatisticsMetric
    small_sample: bool


@dataclass(frozen=True)
class ReportAxisBreakdown:
    axis: str
    key: str
    label: str
    metric: StatisticsMetric
    small_sample: bool


@dataclass(frozen=True)
class DeckUsageShare:
    deck_id: int | None
    deck_name: str
    color: str | None
    matches: int
    ratio: float


@dataclass(frozen=True)
class DeckUsageTrendPoint:
    period_start: date
    label: str
    total_matches: int
    decks: tuple[DeckUsageShare, ...]


@dataclass(frozen=True)
class SeasonReport:
    season: Season
    comparison_season: Season | None
    comparison: SeasonComparison
    summary: StatisticsDashboard
    daily_trend: tuple[StatisticsTrendPoint, ...]
    weekly_trend: tuple[StatisticsTrendPoint, ...]
    deck_orders: tuple[DeckOrderBreakdown, ...]
    axes: tuple[ReportAxisBreakdown, ...]
    daily_deck_usage: tuple[DeckUsageTrendPoint, ...]
    weekly_deck_usage: tuple[DeckUsageTrendPoint, ...]
    generated_at: datetime
    sample_threshold: int = SMALL_SAMPLE_THRESHOLD

    @property
    def small_sample(self) -> bool:
        return _is_small(self.comparison.current)


class SeasonReportService:
    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        self.seasons = SeasonRepository.from_runtime_paths(paths)
        self.statistics = DuelStatisticsRepository.from_runtime_paths(paths)
        self.catalog = DuelCatalogRepository.from_runtime_paths(paths)

    def default_comparison(self, season_id: int) -> Season | None:
        current = self.seasons.get(season_id)
        candidates = tuple(
            season
            for season in self.seasons.list(include_archived=True)
            if season.season_id != current.season_id
            and season.season_type == current.season_type
            and season.start_date < current.start_date
        )
        return max(
            candidates,
            key=lambda season: (season.start_date, season.end_date, season.season_id),
            default=None,
        )

    def build(
        self,
        season_id: int,
        *,
        comparison_season_id: int | None = None,
        use_default_comparison: bool = True,
    ) -> SeasonReport:
        season = self.seasons.get(season_id)
        comparison_season = (
            self.seasons.get(comparison_season_id)
            if comparison_season_id is not None
            else self.default_comparison(season_id)
            if use_default_comparison
            else None
        )
        if comparison_season is not None and comparison_season.season_id == season_id:
            raise SeasonError("同じシーズン同士は比較できません")
        filters = _season_filters(season)
        summary = self.statistics.dashboard(filters, granularity="day")
        weekly = self.statistics.dashboard(filters, granularity="week")
        rows = self.statistics.rows(filters)
        comparison_metric = None
        if comparison_season is not None:
            comparison_metric = self.statistics.dashboard(
                _season_filters(comparison_season), granularity="day"
            ).filtered
        comparison = _comparison(summary.filtered, comparison_metric)
        deck_metadata = {
            item.entry_id: (item.name, item.color)
            for item in self.catalog.list_decks(include_archived=True)
        }
        return SeasonReport(
            season=season,
            comparison_season=comparison_season,
            comparison=comparison,
            summary=summary,
            daily_trend=summary.trend,
            weekly_trend=weekly.trend,
            deck_orders=_deck_orders(rows, deck_metadata),
            axes=_axes(rows),
            daily_deck_usage=_deck_usage(
                rows, season.start_date, season.end_date, "day", deck_metadata
            ),
            weekly_deck_usage=_deck_usage(
                rows, season.start_date, season.end_date, "week", deck_metadata
            ),
            generated_at=datetime.now(timezone.utc),
        )


def _season_filters(season: Season) -> StatisticsFilter:
    return StatisticsFilter(
        date_from=season.start_date,
        date_to=season.end_date,
        season_id=season.season_id,
    )


def _comparison(
    current: StatisticsMetric, comparison: StatisticsMetric | None
) -> SeasonComparison:
    if comparison is None:
        return SeasonComparison(current, None, None, None)
    rate_delta = (
        None
        if current.win_rate is None or comparison.win_rate is None
        else current.win_rate - comparison.win_rate
    )
    return SeasonComparison(
        current,
        comparison,
        current.matches - comparison.matches,
        rate_delta,
    )


def _deck_orders(
    rows: tuple[StatisticsRow, ...],
    metadata: dict[int, tuple[str, str | None]],
) -> tuple[DeckOrderBreakdown, ...]:
    grouped: dict[tuple[int | None, str], list[StatisticsRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.own_deck_id, row.own_deck.strip())].append(row)
    result: list[DeckOrderBreakdown] = []
    labels = {
        "overall": "全体",
        "first": "先攻時",
        "second": "後攻時",
        "unknown": "未設定",
    }
    ordered_decks = sorted(
        grouped,
        key=lambda key: (-len(grouped[key]), (key[1] or "未設定").casefold()),
    )
    for deck_id, stored_name in ordered_decks:
        name, color = metadata.get(
            deck_id,
            (stored_name or "未設定", None),
        )
        deck_rows = grouped[(deck_id, stored_name)]
        for play_order in ("overall", "first", "second", "unknown"):
            selected = (
                deck_rows
                if play_order == "overall"
                else [row for row in deck_rows if row.play_order == play_order]
            )
            metric = _metric(selected)
            if play_order == "unknown" and metric.matches == 0:
                continue
            result.append(
                DeckOrderBreakdown(
                    deck_id,
                    name,
                    color,
                    play_order,
                    labels[play_order],
                    metric,
                    _is_small(metric),
                )
            )
    return tuple(result)


def _axes(rows: tuple[StatisticsRow, ...]) -> tuple[ReportAxisBreakdown, ...]:
    overall = _metric(rows)
    definitions = (
        ("coin_face", "コイン", (("heads", "表"), ("tails", "裏"), ("unknown", "未設定"))),
        ("play_order", "先後", (("first", "先攻"), ("second", "後攻"), ("unknown", "未設定"))),
    )
    result: list[ReportAxisBreakdown] = [
        ReportAxisBreakdown(
            "overall",
            "all",
            "全体",
            overall,
            _is_small(overall),
        )
    ]
    for field, axis_label, choices in definitions:
        for key, label in choices:
            metric = _metric([row for row in rows if getattr(row, field) == key])
            if key == "unknown" and metric.matches == 0:
                continue
            result.append(
                ReportAxisBreakdown(
                    field,
                    key,
                    f"{axis_label}: {label}",
                    metric,
                    _is_small(metric),
                )
            )
    return tuple(result)


def _deck_usage(
    rows: tuple[StatisticsRow, ...],
    start: date,
    end: date,
    granularity: str,
    metadata: dict[int, tuple[str, str | None]],
) -> tuple[DeckUsageTrendPoint, ...]:
    grouped: dict[date, list[StatisticsRow]] = defaultdict(list)
    for row in rows:
        grouped[_period_start(statistics_local_date(row.occurred_at), granularity)].append(row)
    current = _period_start(start, granularity)
    last = _period_start(end, granularity)
    points: list[DeckUsageTrendPoint] = []
    while current <= last:
        period_rows = grouped[current]
        deck_groups: dict[tuple[int | None, str], list[StatisticsRow]] = defaultdict(list)
        for row in period_rows:
            deck_groups[(row.own_deck_id, row.own_deck.strip())].append(row)
        shares: list[DeckUsageShare] = []
        total = len(period_rows)
        for (deck_id, stored_name), selected in deck_groups.items():
            name, color = metadata.get(deck_id, (stored_name or "未設定", None))
            shares.append(
                DeckUsageShare(
                    deck_id,
                    name,
                    color,
                    len(selected),
                    len(selected) / total if total else 0.0,
                )
            )
        points.append(
            DeckUsageTrendPoint(
                current,
                current.strftime("%m/%d")
                if granularity == "day"
                else f"{current:%m/%d}週",
                total,
                tuple(sorted(shares, key=lambda item: (-item.matches, item.deck_name.casefold()))),
            )
        )
        current += timedelta(days=1 if granularity == "day" else 7)
    return tuple(points)


def _period_start(value: date, granularity: str) -> date:
    return value if granularity == "day" else value - timedelta(days=value.weekday())


def _metric(rows: list[StatisticsRow] | tuple[StatisticsRow, ...]) -> StatisticsMetric:
    return StatisticsMetric(
        len(rows),
        sum(row.result == "win" for row in rows),
        sum(row.result == "loss" for row in rows),
        sum(row.result == "draw" for row in rows),
    )


def _is_small(metric: StatisticsMetric) -> bool:
    return 0 < metric.matches < SMALL_SAMPLE_THRESHOLD
