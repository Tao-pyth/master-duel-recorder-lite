from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .card_analysis_repository import CardAnalysisRepository
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
        targets = {item.target_id: item for item in CardAnalysisRepository(self.database_path).list_targets()}
        if target_id not in targets or (opener_target_id is not None and opener_target_id not in targets):
            raise ValueError("対象カードがありません")
        base_rows = DuelStatisticsRepository(self.database_path).rows(filters)
        confirmed: set[str] = set()
        candidates: set[str] = set()
        nonuses: set[str] = set()
        openers: dict[str, str] = {}
        with closing(connect_history_database(self.database_path)) as connection:
            for row in connection.execute(
                "SELECT recording_id,action,status,evidence_json FROM card_observations "
                "WHERE target_id=? AND actor=? AND status IN ('confirmed','candidate')", (target_id, actor),
            ):
                if row["action"] == "unknown" or json.loads(row["evidence_json"]).get("supports_use") is not True:
                    continue
                (confirmed if row["status"] == "confirmed" else candidates).add(row["recording_id"])
            nonuses = {row[0] for row in connection.execute(
                "SELECT recording_id FROM card_nonuse_confirmations WHERE target_id=? AND actor=? AND confirmed=1",
                (target_id, actor),
            )}
            for row in connection.execute(
                "SELECT opener.recording_id, observation.target_id,observation.action,observation.evidence_json "
                "FROM card_opener_confirmations AS opener "
                "JOIN card_observations AS observation ON observation.event_id=opener.event_id "
                "WHERE opener.actor=? AND opener.status='confirmed' AND observation.status='confirmed'",
                (actor,),
            ):
                if row["target_id"] is not None and row["action"] != "unknown" and json.loads(row["evidence_json"]).get("supports_use") is True:
                    openers[row["recording_id"]] = row["target_id"]
        rows = tuple(row for row in base_rows if opener_target_id is None or openers.get(row.recording_id) == opener_target_id)
        groups: dict[str, list[StatisticsRow]] = defaultdict(list)
        for row in rows:
            if row.recording_id in openers:
                groups[openers[row.recording_id]].append(row)
        confirmed_rows, candidate_rows, unused_rows, unknown_rows = [], [], [], []
        for row in rows:
            recording = row.recording_id
            if recording in confirmed:
                confirmed_rows.append(row)
            elif recording in nonuses:
                unused_rows.append(row)
            elif recording in candidates:
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
            sum(row.recording_id not in openers for row in base_rows), _metric(base_rows),
        )


def _metric(rows: tuple[StatisticsRow, ...] | list[StatisticsRow]) -> StatisticsMetric:
    return StatisticsMetric(len(rows), sum(row.result == "win" for row in rows),
                            sum(row.result == "loss" for row in rows), sum(row.result == "draw" for row in rows))


def _metric_dict(metric: StatisticsMetric) -> dict[str, object]:
    return {**asdict(metric), "win_rate": metric.win_rate}
