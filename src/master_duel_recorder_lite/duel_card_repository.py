"""動画の存在や時刻から独立した、対戦カード情報の正本。"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator
import uuid

from .card_analysis_models import (
    CARD_ACTORS, CARD_INFORMATION_STATES, CardAnalysisConflictError, CardAnalysisError,
    CardIdentity, DuelCardInformation, DuelCardMigrationReview, DuelCardOpener, DuelCardReview, normalize_card_name,
)
from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths


class DuelCardRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        connect_history_database(self.database_path).close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> DuelCardRepository:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(connect_history_database(self.database_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                with connection:
                    yield connection
        except sqlite3.Error as exc:
            raise CardAnalysisError(f"対戦カード情報を保存できません: {exc}") from exc

    def list_identities(self) -> tuple[CardIdentity, ...]:
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute("SELECT * FROM card_identities ORDER BY name,card_id").fetchall()
        return tuple(_identity(row) for row in rows)

    def save_identity(self, *, name: str) -> CardIdentity:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200 or "\x00" in name:
            raise ValueError("カード名は1文字以上200文字以下で指定してください")
        name, normalized = name.strip(), normalize_card_name(name)
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM card_identities WHERE normalized_name=?", (normalized,)).fetchone()
            if row is None:
                now = _now()
                card_id = uuid.uuid4().hex
                connection.execute("INSERT INTO card_identities VALUES (?,?,?,?,?)", (card_id, name, normalized, now, now))
                row = connection.execute("SELECT * FROM card_identities WHERE card_id=?", (card_id,)).fetchone()
            return _identity(row)

    def get_review(self, duel_id: str) -> DuelCardReview:
        with closing(connect_history_database(self.database_path)) as connection:
            duel = require_duel(connection, duel_id)
            information = connection.execute(_INFORMATION_SELECT + " WHERE information.duel_id=? ORDER BY identity.name,information.actor", (duel_id,)).fetchall()
            openers = connection.execute(_OPENER_SELECT + " WHERE opener.duel_id=? ORDER BY opener.actor", (duel_id,)).fetchall()
            reviews = connection.execute(
                "SELECT review.*,identity.name AS target_name FROM card_migration_review_items review "
                "LEFT JOIN card_target_identity_links target ON target.target_id=json_extract(review.data_json,'$.target_id') "
                "LEFT JOIN card_identities identity ON identity.card_id=target.card_id "
                "WHERE review.status='needs_review' AND (review.duel_id=? OR (review.duel_id IS NULL AND review.recording_id=?)) "
                "ORDER BY review.created_at,review.review_id", (duel_id, duel["recording_id"]),
            ).fetchall()
        return DuelCardReview(duel_id, tuple(_information(row) for row in information), tuple(_opener(row) for row in openers),
                              tuple(_migration_review(row) for row in reviews))

    def save_information(
        self, duel_id: str, card_id: str, actor: str, state: str, *,
        expected_revision: int | None = None, is_opener: bool | None = None,
        opener_expected_revision: int | None = None,
    ) -> DuelCardInformation:
        with self._transaction() as connection:
            result = write_information(connection, duel_id, card_id, actor, state,
                                       expected_revision=expected_revision, source="manual")
            opener = connection.execute("SELECT * FROM duel_card_openers WHERE duel_id=? AND actor=?", (duel_id, actor)).fetchone()
            if is_opener is not None and not isinstance(is_opener, bool):
                raise ValueError("初動確認は真偽値で指定してください")
            if is_opener:
                write_opener(connection, duel_id, actor, card_id, "confirmed", expected_revision=opener_expected_revision, source="manual")
            elif opener and opener["card_id"] == card_id and (is_opener is False or state != "used"):
                write_opener(connection, duel_id, actor, None, "unknown", expected_revision=opener_expected_revision, source="manual")
            return result

    def set_opener(self, duel_id: str, actor: str, *, card_id: str | None = None,
                   status: str = "confirmed", expected_revision: int | None = None) -> DuelCardOpener:
        with self._transaction() as connection:
            return write_opener(connection, duel_id, actor, card_id, status,
                                expected_revision=expected_revision, source="manual")


_INFORMATION_SELECT = (
    "SELECT information.*,identity.name AS card_name FROM duel_card_information AS information "
    "JOIN card_identities AS identity ON identity.card_id=information.card_id"
)
_OPENER_SELECT = (
    "SELECT opener.*,identity.name AS card_name FROM duel_card_openers AS opener "
    "LEFT JOIN card_identities AS identity ON identity.card_id=opener.card_id"
)


def require_duel(connection: sqlite3.Connection, duel_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM duel_records WHERE duel_id=?", (duel_id,)).fetchone()
    if row is None:
        raise CardAnalysisError("対戦記録がありません")
    return row


def duel_for_recording(connection: sqlite3.Connection, recording_id: str, *, create: bool = False) -> str | None:
    row = connection.execute("SELECT duel_id FROM duel_records WHERE recording_id=?", (recording_id,)).fetchone()
    if row is not None:
        return str(row[0])
    recording = connection.execute("SELECT * FROM recordings WHERE recording_id=?", (recording_id,)).fetchone()
    if recording is None:
        raise CardAnalysisError("録画履歴がありません")
    if not create:
        return None
    # 移行と同じ決定的IDを使い、確認の再実行で戦績を増殖させません。
    payload = json.dumps(["mdrl-schema19", "draft", recording_id], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    duel_id = uuid.uuid5(uuid.NAMESPACE_URL, payload).hex
    now = _now()
    connection.execute(
        "INSERT INTO duel_records (duel_id,recording_id,entry_origin,occurred_at,status,result,"
        "play_order,coin_face,own_deck,opponent_deck,duel_type,notes,revision,created_at,updated_at) "
        "VALUES (?,?,'recording',?,'draft','unknown','unknown','unknown','','','other','',1,?,?)",
        (duel_id, recording_id, recording["started_at"] or recording["created_at"], now, now),
    )
    return duel_id


def card_for_target(connection: sqlite3.Connection, target_id: str) -> str:
    row = connection.execute("SELECT card_id FROM card_target_identity_links WHERE target_id=?", (target_id,)).fetchone()
    if row is None:
        raise CardAnalysisError("対象カードの同一性情報がありません")
    return str(row[0])


def check_revision(row: sqlite3.Row | None, expected: int | None) -> None:
    if expected is not None:
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError("expected_revisionは0以上の整数で指定してください")
        if expected != (int(row["revision"]) if row is not None else 0):
            raise CardAnalysisConflictError("対戦カード情報が更新されています。再読込してください")


def write_information(connection: sqlite3.Connection, duel_id: str, card_id: str, actor: str, state: str, *,
                      expected_revision: int | None, source: str) -> DuelCardInformation:
    require_duel(connection, duel_id)
    if not isinstance(actor, str) or not isinstance(state, str) or actor not in CARD_ACTORS or state not in CARD_INFORMATION_STATES:
        raise ValueError("使用者またはカード使用状態が不正です")
    if connection.execute("SELECT 1 FROM card_identities WHERE card_id=?", (card_id,)).fetchone() is None:
        raise CardAnalysisError("カードがありません")
    current = connection.execute("SELECT * FROM duel_card_information WHERE duel_id=? AND card_id=? AND actor=?", (duel_id, card_id, actor)).fetchone()
    check_revision(current, expected_revision)
    if current is not None and expected_revision is None:
        if current["state"] != state:
            raise CardAnalysisConflictError("既存の確認を変更するには再読込してrevisionを指定してください")
        return _information(connection.execute(_INFORMATION_SELECT + " WHERE information.information_id=?", (current["information_id"],)).fetchone())
    now = _now()
    identifier = str(current["information_id"]) if current is not None else uuid.uuid4().hex
    revision = int(current["revision"]) + 1 if current is not None else 1
    connection.execute(
        "INSERT INTO duel_card_information VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(duel_id,card_id,actor) DO UPDATE SET state=excluded.state,source=excluded.source,"
        "revision=excluded.revision,confirmed_at=excluded.confirmed_at,updated_at=excluded.updated_at",
        (identifier, duel_id, card_id, actor, state, source, revision, now if state != "unknown" else None,
         current["created_at"] if current is not None else now, now),
    )
    result = connection.execute("SELECT * FROM duel_card_information WHERE information_id=?", (identifier,)).fetchone()
    connection.execute("INSERT INTO duel_card_information_changes (information_id,revision,source,before_json,after_json,changed_at) VALUES (?,?,?,?,?,?)",
                       (identifier, revision, source, _json(dict(current) if current is not None else {}), _json(dict(result)), now))
    return _information(connection.execute(_INFORMATION_SELECT + " WHERE information.information_id=?", (identifier,)).fetchone())


def write_opener(connection: sqlite3.Connection, duel_id: str, actor: str, card_id: str | None, status: str, *,
                 expected_revision: int | None, source: str) -> DuelCardOpener:
    require_duel(connection, duel_id)
    if not isinstance(actor, str) or not isinstance(status, str) or actor not in {"self", "opponent"} or status not in {"confirmed", "unknown"}:
        raise ValueError("初動の使用者または確認状態が不正です")
    if status == "confirmed":
        use = connection.execute("SELECT state FROM duel_card_information WHERE duel_id=? AND card_id=? AND actor=?", (duel_id, card_id, actor)).fetchone()
        if use is None or use[0] != "used":
            raise ValueError("初動は同じ対戦・使用者の確認済み使用から選んでください")
    else:
        card_id = None
    current = connection.execute("SELECT * FROM duel_card_openers WHERE duel_id=? AND actor=?", (duel_id, actor)).fetchone()
    check_revision(current, expected_revision)
    if current is not None and expected_revision is None:
        if current["card_id"] != card_id or current["status"] != status:
            raise CardAnalysisConflictError("初動が更新されています。再読込してrevisionを指定してください")
        return _opener(connection.execute(_OPENER_SELECT + " WHERE opener.duel_id=? AND opener.actor=?", (duel_id, actor)).fetchone())
    now = _now()
    revision = int(current["revision"]) + 1 if current is not None else 1
    connection.execute(
        "INSERT INTO duel_card_openers VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(duel_id,actor) DO UPDATE SET card_id=excluded.card_id,status=excluded.status,source=excluded.source,"
        "revision=excluded.revision,confirmed_at=excluded.confirmed_at,updated_at=excluded.updated_at",
        (duel_id, actor, card_id, status, source, revision, now if status == "confirmed" else None,
         current["created_at"] if current is not None else now, now),
    )
    result = connection.execute("SELECT * FROM duel_card_openers WHERE duel_id=? AND actor=?", (duel_id, actor)).fetchone()
    connection.execute("INSERT INTO duel_card_opener_changes (duel_id,actor,revision,source,before_json,after_json,changed_at) VALUES (?,?,?,?,?,?,?)",
                       (duel_id, actor, revision, source, _json(dict(current) if current is not None else {}), _json(dict(result)), now))
    return _opener(connection.execute(_OPENER_SELECT + " WHERE opener.duel_id=? AND opener.actor=?", (duel_id, actor)).fetchone())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _identity(row: sqlite3.Row) -> CardIdentity:
    return CardIdentity(row["card_id"], row["name"], row["normalized_name"], datetime.fromisoformat(row["created_at"]), datetime.fromisoformat(row["updated_at"]))


def _information(row: sqlite3.Row) -> DuelCardInformation:
    return DuelCardInformation(row["information_id"], row["duel_id"], row["card_id"], row["card_name"], row["actor"], row["state"],
                               row["source"], int(row["revision"]), datetime.fromisoformat(row["confirmed_at"]) if row["confirmed_at"] else None,
                               datetime.fromisoformat(row["created_at"]), datetime.fromisoformat(row["updated_at"]))


def _opener(row: sqlite3.Row) -> DuelCardOpener:
    return DuelCardOpener(row["duel_id"], row["actor"], row["card_id"], row["card_name"], row["status"], row["source"], int(row["revision"]),
                          datetime.fromisoformat(row["confirmed_at"]) if row["confirmed_at"] else None,
                          datetime.fromisoformat(row["created_at"]), datetime.fromisoformat(row["updated_at"]))


def _migration_review(row: sqlite3.Row) -> DuelCardMigrationReview:
    data = json.loads(row["data_json"])
    data = data if isinstance(data, dict) else {}
    observation = data.get("observation") if row["source_kind"] == "opener" else data
    observation = observation if isinstance(observation, dict) else {}
    opener = data.get("opener", data) if row["source_kind"] == "opener" else {}
    opener = opener if isinstance(opener, dict) else {}
    evidence = observation.get("evidence", {})
    if "evidence_json" in observation:
        try:
            evidence = json.loads(observation["evidence_json"])
        except (TypeError, ValueError):
            evidence = {}
    support = evidence.get("supports_use") if isinstance(evidence, dict) else None
    confirmed = data.get("confirmed") if row["source_kind"] == "nonuse" else None
    return DuelCardMigrationReview(
        str(row["review_id"]), str(row["source_kind"]), str(row["reason"]),
        str(observation.get("card_name") or row["target_name"] or "不明"),
        str(observation.get("actor") or opener.get("actor") or data.get("actor") or "unknown"),
        str(observation.get("action") or "unknown"), str(observation.get("status") or "unknown"),
        support if isinstance(support, bool) else None,
        bool(confirmed) if confirmed in (0, 1) else None,
        str(opener.get("status") or "unknown") if row["source_kind"] == "opener" else None,
    )
