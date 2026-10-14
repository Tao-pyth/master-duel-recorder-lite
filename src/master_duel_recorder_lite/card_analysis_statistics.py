from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .duel_card_repository import DuelCardRepository
from .duel_statistics import (
    DuelStatisticsRepository, StatisticsFilter, StatisticsMetric, StatisticsRow,
)
from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


@dataclass(frozen=True)
class CardOpenerStatistics:
    target_id: str
    label: str
    metric: StatisticsMetric

    def to_dict(self) -> dict[str, object]:
        return {"target_id": self.target_id, "label": self.label, "metric": _metric_dict(self.metric)}


@dataclass(frozen=True)
class CardAnalysisStatisticsDashboard:
    target_id: str
    actor: str
    opener_target_id: str | None
    include_candidates: bool
    total: StatisticsMetric
    used: StatisticsMetric
    not_used: StatisticsMetric
    unknown: StatisticsMetric
    confirmed_used: StatisticsMetric
    candidate_used: StatisticsMetric
    by_opener: tuple[CardOpenerStatistics, ...]
    unknown_opener_count: int
    base_total: StatisticsMetric

    def to_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id, "actor": self.actor,
            "opener_target_id": self.opener_target_id, "include_candidates": self.include_candidates,
            "mode_label": "候補込みの参考比較" if self.include_candidates else "確認済みの比較",
            **{name: _metric_dict(getattr(self, name)) for name in (
                "total", "used", "not_used", "unknown", "confirmed_used", "candidate_used", "base_total"
            )},
            "by_opener": [row.to_dict() for row in self.by_opener],
            "unknown_opener_count": self.unknown_opener_count,
        }


class CardAnalysisStatisticsRepository:
    """1対戦を1件とし、不明を非使用へ変換しないカード分析です。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connect_history_database(self.database_path).close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> CardAnalysisStatisticsRepository:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def dashboard(
        self, filters: StatisticsFilter | None = None, *, target_id: str, actor: str = "self",
        opener_target_id: str | None = None, include_candidates: bool = False,
    ) -> CardAnalysisStatisticsDashboard:
        if actor not in {"self", "opponent"}:
            raise ValueError("使用者は自分または相手を指定してください")
        if not isinstance(include_candidates, bool):
            raise ValueError("候補参考モードは真偽値で指定してください")
        targets = {item.card_id: item for item in DuelCardRepository(self.database_path).list_identities()}
        with closing(connect_history_database(self.database_path)) as connection:
            identities = {row["target_id"]: row["card_id"] for row in connection.execute("SELECT * FROM card_target_identity_links")}
        selected_card = identities.get(target_id, target_id)
        selected_opener = identities.get(opener_target_id, opener_target_id)
        if selected_card not in targets or (selected_opener is not None and selected_opener not in targets):
            raise ValueError("対象カードがありません")
        base_rows = DuelStatisticsRepository(self.database_path).rows(filters)
        confirmed: set[str] = set()
        candidates: set[str] = set()
        nonuses: set[str] = set()
        openers: dict[str, str] = {}
        with closing(connect_history_database(self.database_path)) as connection:
            for row in connection.execute(
                "SELECT duel_id,state FROM duel_card_information WHERE card_id=? AND actor=?",
                (selected_card, actor),
            ):
                if row["state"] == "used":
                    confirmed.add(row["duel_id"])
                elif row["state"] == "not_used":
                    nonuses.add(row["duel_id"])
            for row in connection.execute(
                "SELECT duel_id,card_id FROM duel_card_openers WHERE actor=? AND status='confirmed'",
                (actor,),
            ):
                openers[row["duel_id"]] = row["card_id"]
            for row in connection.execute(
                "SELECT duel.duel_id,observation.action,observation.evidence_json FROM card_observations AS observation "
                "JOIN duel_records AS duel ON duel.recording_id=observation.recording_id "
                "JOIN card_target_identity_links AS identity ON identity.target_id=observation.target_id "
                "JOIN card_observation_links AS source ON source.event_id=observation.event_id "
                "JOIN recording_video_assets AS asset ON asset.asset_id=source.video_asset_id "
                "WHERE identity.card_id=? AND observation.actor=? AND observation.status='candidate' "
                "AND source.verified=1 AND asset.is_current=1 AND asset.state='present' "
                "AND source.video_generation=asset.generation AND source.time_basis=asset.time_basis",
                (selected_card, actor),
            ):
                if row["action"] != "unknown" and json.loads(row["evidence_json"]).get("supports_use") is True:
                    candidates.add(row["duel_id"])
        rows = tuple(row for row in base_rows if selected_opener is None or openers.get(row.duel_id) == selected_opener)
        groups: dict[str, list[StatisticsRow]] = defaultdict(list)
        for row in rows:
            if row.duel_id in openers:
                groups[openers[row.duel_id]].append(row)
        confirmed_rows, candidate_rows, unused_rows, unknown_rows = [], [], [], []
        for row in rows:
            duel = row.duel_id
            if duel in confirmed:
                confirmed_rows.append(row)
            elif duel in nonuses:
                unused_rows.append(row)
            elif duel in candidates:
                candidate_rows.append(row)
                if not include_candidates:
                    unknown_rows.append(row)
            else:
                unknown_rows.append(row)
        return CardAnalysisStatisticsDashboard(
            target_id, actor, opener_target_id, include_candidates, _metric(rows),
            _metric(confirmed_rows + (candidate_rows if include_candidates else [])),
            _metric(unused_rows), _metric(unknown_rows), _metric(confirmed_rows), _metric(candidate_rows),
            tuple(CardOpenerStatistics(key, targets[key].name, _metric(value))
                  for key, value in sorted(groups.items(), key=lambda item: targets[item[0]].name)),
            sum(row.duel_id not in openers for row in base_rows), _metric(base_rows),
        )


def _metric(rows: tuple[StatisticsRow, ...] | list[StatisticsRow]) -> StatisticsMetric:
    return StatisticsMetric(len(rows), sum(row.result == "win" for row in rows),
                            sum(row.result == "loss" for row in rows), sum(row.result == "draw" for row in rows))


def _metric_dict(metric: StatisticsMetric) -> dict[str, object]:
    return {**asdict(metric), "win_rate": metric.win_rate}
