from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any


RESULT_STOP_EVENT = "result_stopped"
FAILED_STOP_EVENTS = frozenset({"boundary_stopped", "recording_stopped"})
RECOVERY_GRACE = timedelta(seconds=3)
POST_STOP_ACTIVITY_WINDOW = timedelta(seconds=120)
POST_STOP_BOARD_THRESHOLD = 0.35
POST_STOP_BOARD_MATCHES = 3


@dataclass(frozen=True)
class LiveDuelAttempt:
    session_id: str
    attempt: int
    candidate_at: datetime
    confirmed_at: datetime | None
    stopped_at: datetime | None
    stop_event: str | None
    monitoring_recovered: bool
    post_stop_duel_activity: bool
    stream_restarts: int

    @property
    def passed(self) -> bool:
        return (
            self.confirmed_at is not None
            and self.stop_event == RESULT_STOP_EVENT
            and self.monitoring_recovered
            and not self.post_stop_duel_activity
        )

    @property
    def failure_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.confirmed_at is None:
            reasons.append("盤面未確定")
        if self.stopped_at is None:
            reasons.append("停止未検出")
        elif self.stop_event != RESULT_STOP_EVENT:
            reasons.append("結果以外で停止")
        if self.stopped_at is not None and not self.monitoring_recovered:
            reasons.append("停止後の監視復帰未確認")
        if self.post_stop_duel_activity:
            reasons.append("結果停止後も盤面継続")
        return tuple(reasons)


@dataclass(frozen=True)
class LiveValidationReport:
    since: datetime | None
    sessions: int
    attempts: tuple[LiveDuelAttempt, ...]
    discarded_candidates: int
    malformed_events: tuple[str, ...]
    stream_restarts: int
    observed_match_error: bool
    observed_replay: bool
    observed_overlay: bool
    minimum_effective_fps: float | None
    average_effective_fps: float | None

    @property
    def passed_attempts(self) -> int:
        return sum(attempt.passed for attempt in self.attempts)

    @property
    def failed_attempts(self) -> int:
        return len(self.attempts) - self.passed_attempts

    @property
    def maximum_consecutive_passes(self) -> int:
        maximum = current = 0
        for attempt in self.attempts:
            current = current + 1 if attempt.passed else 0
            maximum = max(maximum, current)
        return maximum

    @property
    def latest_consecutive_passes(self) -> int:
        current = 0
        for attempt in reversed(self.attempts):
            if not attempt.passed:
                break
            current += 1
        return current

    def gate_passed(self, required_consecutive: int) -> bool:
        if required_consecutive <= 0:
            raise ValueError("required_consecutive must be positive")
        return (
            not self.malformed_events
            and self.latest_consecutive_passes >= required_consecutive
        )

    def to_document(self, required_consecutive: int) -> dict[str, object]:
        return {
            "schema_version": 1,
            "since": _isoformat(self.since),
            "sessions": self.sessions,
            "attempts": len(self.attempts),
            "passed_attempts": self.passed_attempts,
            "failed_attempts": self.failed_attempts,
            "discarded_candidates": self.discarded_candidates,
            "maximum_consecutive_passes": self.maximum_consecutive_passes,
            "latest_consecutive_passes": self.latest_consecutive_passes,
            "required_consecutive": required_consecutive,
            "gate_passed": self.gate_passed(required_consecutive),
            "stream_restarts": self.stream_restarts,
            "minimum_effective_fps": self.minimum_effective_fps,
            "average_effective_fps": self.average_effective_fps,
            "observations": {
                "match_error": self.observed_match_error,
                "replay": self.observed_replay,
                "overlay": self.observed_overlay,
            },
            "malformed_events": list(self.malformed_events),
            "duels": [
                {
                    "session": attempt.session_id,
                    "attempt": attempt.attempt,
                    "passed": attempt.passed,
                    "candidate_at": attempt.candidate_at.isoformat(),
                    "confirmed_at": _isoformat(attempt.confirmed_at),
                    "stopped_at": _isoformat(attempt.stopped_at),
                    "stop_event": attempt.stop_event,
                    "monitoring_recovered": attempt.monitoring_recovered,
                    "post_stop_duel_activity": attempt.post_stop_duel_activity,
                    "stream_restarts": attempt.stream_restarts,
                    "failure_reasons": list(attempt.failure_reasons),
                }
                for attempt in self.attempts
            ],
        }


@dataclass
class _AttemptBuilder:
    session_id: str
    attempt: int
    candidate_at: datetime
    confirmed_at: datetime | None = None
    stopped_at: datetime | None = None
    stop_event: str | None = None
    stream_restarts: int = 0


def evaluate_live_diagnostics(
    directory: Path,
    *,
    since: datetime | None = None,
) -> LiveValidationReport:
    root = directory.expanduser().resolve()
    attempts: list[LiveDuelAttempt] = []
    malformed: list[str] = []
    discarded = restarts = sessions = 0
    observed_match_error = observed_replay = observed_overlay = False
    effective_fps_values: list[float] = []

    for path in sorted(root.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            started_at = _timestamp(document.get("started_at"))
            if since is not None and started_at < _as_aware(since):
                continue
            transitions = _transitions(document)
            samples = _samples(document)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            malformed.append(f"{path.stem}: {exc}")
            continue

        sessions += 1
        session_id = path.stem
        latest_sample = max(
            (_timestamp(sample.get("at")) for sample in samples),
            default=None,
        )
        observed_match_error |= any(
            sample.get("state") == "match_error" or _score(sample, "error") >= 0.70
            for sample in samples
        )
        observed_replay |= any(
            sample.get("state") == "replay" or _score(sample, "replay") >= 0.80
            for sample in samples
        )
        observed_overlay |= any(_score(sample, "overlay") >= 0.70 for sample in samples)
        effective_fps_values.extend(
            value
            for sample in samples
            if (value := _effective_fps(sample)) is not None
        )

        current: _AttemptBuilder | None = None
        attempt_number = 0
        for transition_index, transition in enumerate(transitions):
            event = transition["event"]
            at = transition["at"]
            if event == "candidate_started":
                if current is not None:
                    attempts.append(_finish(current, latest_sample, samples))
                attempt_number += 1
                current = _AttemptBuilder(session_id, attempt_number, at)
            elif event == "duel_confirmed":
                if current is None:
                    malformed.append(f"{session_id}: 候補開始前に盤面確定")
                elif current.confirmed_at is not None:
                    malformed.append(f"{session_id}: 盤面確定が重複")
                else:
                    current.confirmed_at = at
            elif event in FAILED_STOP_EVENTS or event == RESULT_STOP_EVENT:
                if current is None:
                    malformed.append(f"{session_id}: 候補開始前に{event}")
                else:
                    current.stopped_at = at
                    current.stop_event = event
                    next_candidate_at = next(
                        (
                            item["at"]
                            for item in transitions[transition_index + 1 :]
                            if item["event"] == "candidate_started"
                        ),
                        None,
                    )
                    attempts.append(
                        _finish(current, latest_sample, samples, next_candidate_at)
                    )
                    current = None
            elif event == "candidate_discarded":
                if current is None:
                    malformed.append(f"{session_id}: 候補開始前に候補破棄")
                elif current.confirmed_at is not None:
                    current.stopped_at = at
                    current.stop_event = event
                    attempts.append(_finish(current, latest_sample, samples))
                else:
                    discarded += 1
                current = None
            elif event == "stream_restarted":
                restarts += 1
                if current is not None:
                    current.stream_restarts += 1
        if current is not None:
            attempts.append(_finish(current, latest_sample, samples))

    return LiveValidationReport(
        sessions=sessions,
        since=_as_aware(since) if since is not None else None,
        attempts=tuple(attempts),
        discarded_candidates=discarded,
        malformed_events=tuple(malformed),
        stream_restarts=restarts,
        observed_match_error=observed_match_error,
        observed_replay=observed_replay,
        observed_overlay=observed_overlay,
        minimum_effective_fps=(
            round(min(effective_fps_values), 3) if effective_fps_values else None
        ),
        average_effective_fps=(
            round(sum(effective_fps_values) / len(effective_fps_values), 3)
            if effective_fps_values
            else None
        ),
    )


def render_live_validation_markdown(
    report: LiveValidationReport,
    required_consecutive: int,
) -> str:
    passed = "合格" if report.gate_passed(required_consecutive) else "未達"
    lines = [
        "# 自動監視 実戦連続試験",
        "",
        f"判定: **{passed}**",
        "",
        f"- 対象セッション: {report.sessions}",
        f"- 試験開始: {_isoformat(report.since) or '全期間'}",
        f"- 成功戦: {report.passed_attempts}/{len(report.attempts)}",
        f"- 最新連続成功: {report.latest_consecutive_passes}/{required_consecutive}",
        f"- 最大連続成功: {report.maximum_consecutive_passes}",
        f"- 候補破棄: {report.discarded_candidates}",
        f"- ストリーム再起動: {report.stream_restarts}",
        f"- 実効fps（最小 / 平均）: {_fps(report.minimum_effective_fps)} / {_fps(report.average_effective_fps)}",
        "",
        "| セッション | 戦 | 開始 | 盤面 | 結果停止 | 監視復帰 | 停止後盤面 | 判定 |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for attempt in report.attempts:
        lines.append(
            "| "
            + " | ".join(
                (
                    attempt.session_id,
                    str(attempt.attempt),
                    "OK",
                    "OK" if attempt.confirmed_at else "NG",
                    "OK" if attempt.stop_event == RESULT_STOP_EVENT else "NG",
                    "OK" if attempt.monitoring_recovered else "NG",
                    "NG" if attempt.post_stop_duel_activity else "OK",
                    "合格" if attempt.passed else " / ".join(attempt.failure_reasons),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 異常系観測",
            "",
            f"- マッチエラー: {_yes_no(report.observed_match_error)}",
            f"- リプレイ: {_yes_no(report.observed_replay)}",
            f"- オーバーレイ: {_yes_no(report.observed_overlay)}",
        ]
    )
    if report.malformed_events:
        lines.extend(["", "## 診断エラー", ""])
        lines.extend(f"- {item}" for item in report.malformed_events)
    return "\n".join(lines) + "\n"


def _transitions(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema_version") != 1:
        raise ValueError("未対応のschema_version")
    raw = document.get("transitions")
    if not isinstance(raw, list):
        raise ValueError("transitionsが配列ではありません")
    parsed: list[dict[str, Any]] = []
    previous: datetime | None = None
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("event"), str):
            raise ValueError("transition形式が不正です")
        at = _timestamp(item.get("at"))
        if previous is not None and at < previous:
            raise ValueError("transition時刻が逆順です")
        parsed.append({"event": item["event"], "at": at})
        previous = at
    return parsed


def _samples(document: dict[str, Any]) -> list[dict[str, Any]]:
    raw = document.get("samples")
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValueError("samplesが配列ではありません")
    return raw


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("時刻がありません")
    return _as_aware(datetime.fromisoformat(value))


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("タイムゾーンなしの時刻は使用できません")
    return value


def _score(sample: dict[str, Any], key: str) -> float:
    scores = sample.get("scores")
    if not isinstance(scores, dict):
        return 0.0
    value = scores.get(key, 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _effective_fps(sample: dict[str, Any]) -> float | None:
    value = sample.get("effective_fps", 0.0)
    return float(value) if isinstance(value, (int, float)) and value > 0 else None


def _finish(
    current: _AttemptBuilder,
    latest_sample: datetime | None,
    samples: list[dict[str, Any]],
    next_candidate_at: datetime | None = None,
) -> LiveDuelAttempt:
    recovered = (
        current.stopped_at is not None
        and latest_sample is not None
        and latest_sample >= current.stopped_at + RECOVERY_GRACE
    )
    return LiveDuelAttempt(
        session_id=current.session_id,
        attempt=current.attempt,
        candidate_at=current.candidate_at,
        confirmed_at=current.confirmed_at,
        stopped_at=current.stopped_at,
        stop_event=current.stop_event,
        monitoring_recovered=recovered,
        post_stop_duel_activity=_post_stop_duel_activity(
            samples,
            current.stopped_at,
            next_candidate_at,
        ),
        stream_restarts=current.stream_restarts,
    )


def _post_stop_duel_activity(
    samples: list[dict[str, Any]],
    stopped_at: datetime | None,
    next_candidate_at: datetime | None,
) -> bool:
    if stopped_at is None:
        return False
    start = stopped_at + RECOVERY_GRACE
    end = min(
        next_candidate_at or stopped_at + POST_STOP_ACTIVITY_WINDOW,
        stopped_at + POST_STOP_ACTIVITY_WINDOW,
    )
    matches = sum(
        start <= _timestamp(sample.get("at")) < end
        and _score(sample, "board") >= POST_STOP_BOARD_THRESHOLD
        for sample in samples
    )
    return matches >= POST_STOP_BOARD_MATCHES


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _yes_no(value: bool) -> str:
    return "確認" if value else "未確認"


def _fps(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "記録なし"
