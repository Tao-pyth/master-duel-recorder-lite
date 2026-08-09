from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import unicodedata
import uuid

from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


EVENT_TYPES = {"duel_start", "turn_change", "duel_result", "marker"}
ACTORS = {"self", "opponent", "unknown"}
OUTCOMES = {"win", "loss", "draw", "unknown"}
SOURCES = {"manual", "detected", "system"}
STATUSES = {"candidate", "confirmed", "rejected"}


class DuelTimelineError(RuntimeError):
    """対戦タイムラインを安全に更新できない場合のエラーです。"""


@dataclass(frozen=True)
class DuelEvent:
    event_id: str
    recording_id: str
    elapsed_ms: int
    event_type: str
    actor: str | None
    outcome: str | None
    label: str
    source: str
    confidence: float | None
    status: str
    detector_id: str | None
    detector_version: str | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "event_id": self.event_id,
            "recording_id": self.recording_id,
            "elapsed_ms": self.elapsed_ms,
            "event_type": self.event_type,
            "actor": self.actor,
            "outcome": self.outcome,
            "label": self.label,
            "source": self.source,
            "confidence": self.confidence,
            "status": self.status,
            "detector_id": self.detector_id,
            "detector_version": self.detector_version,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class DuelTimelineRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connection = connect_history_database(self.database_path)
        connection.close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> DuelTimelineRepository:
        return cls(paths.db / HISTORY_DATABASE_NAME)

    def add(
        self,
        recording_id: str,
        *,
        elapsed_ms: int,
        event_type: str,
        actor: str | None = None,
        outcome: str | None = None,
        label: str = "",
        source: str = "manual",
        confidence: float | None = None,
        status: str = "confirmed",
        detector_id: str | None = None,
        detector_version: str | None = None,
        event_id: str | None = None,
    ) -> DuelEvent:
        identifier = _text(recording_id, 200, "recording_id", required=True)
        if (
            isinstance(elapsed_ms, bool)
            or not isinstance(elapsed_ms, int)
            or elapsed_ms < 0
        ):
            raise ValueError("elapsed_msは0以上の整数である必要があります")
        normalized_type = _choice(event_type, EVENT_TYPES, "event_type")
        normalized_actor = _optional_choice(actor, ACTORS, "actor")
        normalized_outcome = _optional_choice(outcome, OUTCOMES, "outcome")
        normalized_source = _choice(source, SOURCES, "source")
        normalized_status = _choice(status, STATUSES, "status")
        normalized_label = _text(label, 200, "label")
        normalized_detector_id = _optional_text(detector_id, 100, "detector_id")
        normalized_detector_version = _optional_text(
            detector_version, 50, "detector_version"
        )
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            raise ValueError("confidenceは0から1である必要があります")
        if normalized_type == "duel_result" and normalized_outcome is None:
            raise ValueError("duel_resultにはoutcomeが必要です")
        if normalized_type != "duel_result" and normalized_outcome is not None:
            raise ValueError("outcomeはduel_resultだけに指定できます")
        if normalized_type != "turn_change" and normalized_actor is not None:
            raise ValueError("actorはturn_changeだけに指定できます")
        if normalized_type == "marker" and not normalized_label:
            raise ValueError("markerにはlabelが必要です")
        if normalized_source == "detected":
            if normalized_status != "candidate":
                raise ValueError(
                    "自動判定イベントはcandidateとして追加する必要があります"
                )
            if (
                confidence is None
                or normalized_detector_id is None
                or normalized_detector_version is None
            ):
                raise ValueError(
                    "自動判定イベントにはconfidence、detector_id、detector_versionが必要です"
                )
        now = datetime.now(timezone.utc)
        generated_id = _text(
            event_id or uuid.uuid4().hex, 100, "event_id", required=True
        )
        try:
            with (
                closing(connect_history_database(self.database_path)) as connection,
                connection,
            ):
                duration = self._recording_duration(connection, identifier)
                self._validate_elapsed(elapsed_ms, duration)
                if normalized_status == "confirmed":
                    self._validate_confirmation(
                        connection,
                        identifier,
                        elapsed_ms,
                        normalized_type,
                        exclude=None,
                    )
                connection.execute(
                    """
                    INSERT INTO duel_events (
                        event_id, recording_id, elapsed_ms, event_type, actor, outcome,
                        label, source, confidence, status, detector_id, detector_version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generated_id,
                        identifier,
                        elapsed_ms,
                        normalized_type,
                        normalized_actor,
                        normalized_outcome,
                        normalized_label,
                        normalized_source,
                        float(confidence) if confidence is not None else None,
                        normalized_status,
                        normalized_detector_id,
                        normalized_detector_version,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
        except DuelTimelineError:
            raise
        except sqlite3.Error as exc:
            raise DuelTimelineError(f"対戦イベントを追加できません: {exc}") from exc
        event = self.get(generated_id)
        assert event is not None
        return event

    def get(self, event_id: str) -> DuelEvent | None:
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute(
                "SELECT * FROM duel_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return _event(row) if row is not None else None

    def list(
        self,
        recording_id: str,
        *,
        status: str | None = None,
        event_type: str | None = None,
    ) -> tuple[DuelEvent, ...]:
        clauses = ["recording_id = ?"]
        parameters: list[object] = [recording_id]
        if status is not None:
            clauses.append("status = ?")
            parameters.append(_choice(status, STATUSES, "status"))
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(_choice(event_type, EVENT_TYPES, "event_type"))
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT * FROM duel_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY elapsed_ms, event_id",
                parameters,
            ).fetchall()
        return tuple(_event(row) for row in rows)

    def confirm(self, event_id: str) -> DuelEvent:
        return self._transition(event_id, "confirmed")

    def reject(self, event_id: str) -> DuelEvent:
        return self._transition(event_id, "rejected")

    def _transition(self, event_id: str, target: str) -> DuelEvent:
        now = datetime.now(timezone.utc)
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
            row = connection.execute(
                "SELECT * FROM duel_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                raise DuelTimelineError(f"対戦イベントが見つかりません: {event_id}")
            if row["status"] != "candidate":
                raise DuelTimelineError("candidateイベントだけを確認または却下できます")
            if target == "confirmed":
                duration = self._recording_duration(connection, row["recording_id"])
                self._validate_elapsed(row["elapsed_ms"], duration)
                self._validate_confirmation(
                    connection,
                    row["recording_id"],
                    row["elapsed_ms"],
                    row["event_type"],
                    exclude=event_id,
                )
            connection.execute(
                "UPDATE duel_events SET status = ?, updated_at = ? WHERE event_id = ?",
                (target, now.isoformat(), event_id),
            )
        event = self.get(event_id)
        assert event is not None
        return event

    @staticmethod
    def _recording_duration(
        connection: sqlite3.Connection, recording_id: str
    ) -> float | None:
        row = connection.execute(
            "SELECT duration_seconds FROM recordings WHERE recording_id = ?",
            (recording_id,),
        ).fetchone()
        if row is None:
            raise DuelTimelineError(f"録画履歴が見つかりません: {recording_id}")
        return row["duration_seconds"]

    @staticmethod
    def _validate_elapsed(elapsed_ms: int, duration_seconds: float | None) -> None:
        if duration_seconds is not None and elapsed_ms > round(duration_seconds * 1000):
            raise DuelTimelineError("イベント時刻が録画時間を超えています")

    @staticmethod
    def _validate_confirmation(
        connection: sqlite3.Connection,
        recording_id: str,
        elapsed_ms: int,
        event_type: str,
        *,
        exclude: str | None,
    ) -> None:
        rows = connection.execute(
            "SELECT event_id, elapsed_ms, event_type FROM duel_events "
            "WHERE recording_id = ? AND status = 'confirmed' AND event_id != COALESCE(?, '')",
            (recording_id, exclude),
        ).fetchall()
        if event_type in {"duel_start", "duel_result"} and any(
            row["event_type"] == event_type for row in rows
        ):
            raise DuelTimelineError(f"確定済み{event_type}は1件だけ登録できます")
        starts = [
            row["elapsed_ms"] for row in rows if row["event_type"] == "duel_start"
        ]
        results = [
            row["elapsed_ms"] for row in rows if row["event_type"] == "duel_result"
        ]
        turns = [
            row["elapsed_ms"] for row in rows if row["event_type"] == "turn_change"
        ]
        if event_type == "duel_start" and any(
            value <= elapsed_ms for value in turns + results
        ):
            raise DuelTimelineError(
                "対戦開始は確定済みターン・結果より前である必要があります"
            )
        if event_type == "turn_change":
            if not starts or elapsed_ms <= starts[0]:
                raise DuelTimelineError(
                    "ターン切り替えは確定済み対戦開始より後である必要があります"
                )
            if results and elapsed_ms >= results[0]:
                raise DuelTimelineError(
                    "ターン切り替えは確定済み対戦結果より前である必要があります"
                )
        if event_type == "duel_result":
            if (
                not starts
                or elapsed_ms <= starts[0]
                or any(value >= elapsed_ms for value in turns)
            ):
                raise DuelTimelineError(
                    "対戦結果は開始・ターン切り替えより後である必要があります"
                )


def _event(row: sqlite3.Row) -> DuelEvent:
    return DuelEvent(
        event_id=row["event_id"],
        recording_id=row["recording_id"],
        elapsed_ms=row["elapsed_ms"],
        event_type=row["event_type"],
        actor=row["actor"],
        outcome=row["outcome"],
        label=row["label"],
        source=row["source"],
        confidence=row["confidence"],
        status=row["status"],
        detector_id=row["detector_id"],
        detector_version=row["detector_version"],
        created_at=_datetime(row["created_at"]),
        updated_at=_datetime(row["updated_at"]),
    )


def _choice(value: str, allowed: set[str], key: str) -> str:
    normalized = value.strip().casefold()
    if normalized not in allowed:
        raise ValueError(f"{key}が不正です: {value}")
    return normalized


def _optional_choice(value: str | None, allowed: set[str], key: str) -> str | None:
    return _choice(value, allowed, key) if value is not None else None


def _text(value: str, maximum: int, key: str, *, required: bool = False) -> str:
    normalized = unicodedata.normalize("NFC", value.strip())
    if required and not normalized:
        raise ValueError(f"{key}は空にできません")
    if len(normalized) > maximum:
        raise ValueError(f"{key}は{maximum}文字以内である必要があります")
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        raise ValueError(f"{key}に制御文字は使用できません")
    return normalized


def _optional_text(value: str | None, maximum: int, key: str) -> str | None:
    if value is None:
        return None
    normalized = _text(value, maximum, key)
    return normalized or None


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise DuelTimelineError("イベント日時にタイムゾーンがありません")
    return parsed.astimezone(timezone.utc)
