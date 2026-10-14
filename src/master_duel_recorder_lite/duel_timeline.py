from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
from typing import TYPE_CHECKING, Iterator
import unicodedata
import uuid

from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .runtime_paths import RuntimePaths
from .video_lifecycle import VideoAsset, VideoLifecycleError, VideoLifecycleRepository, _object

if TYPE_CHECKING:
    from .recording_retention import FileFingerprint


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
    video_asset_id: str | None = None
    video_generation: int | None = None
    time_basis: str | None = None
    video_verified: bool = False

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
            "video_asset_id": self.video_asset_id,
            "video_generation": self.video_generation,
            "time_basis": self.time_basis,
            "video_verified": self.video_verified,
        }


class DuelTimelineRepository:
    def __init__(self, database_path: Path, *, recordings_root: Path | None = None) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.recordings_root = recordings_root.expanduser().resolve() if recordings_root is not None else None
        connection = connect_history_database(self.database_path)
        connection.close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> DuelTimelineRepository:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME, recordings_root=paths.recordings)

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
                self._source_proof(identifier, allow_capture=normalized_source == "detected" and normalized_status == "candidate") as proof,
                closing(connect_history_database(self.database_path)) as connection,
                connection,
            ):
                connection.execute("BEGIN IMMEDIATE")
                asset, fingerprint, verified = proof
                self._guard_asset(connection, asset)
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
                connection.execute(
                    "INSERT INTO duel_event_sources (event_id,video_asset_id,video_generation,time_basis,input_fingerprint_json,verified) "
                    "VALUES (?,?,?,?,?,?)",
                    (generated_id, asset.asset_id, asset.generation, asset.time_basis,
                     json.dumps(fingerprint, sort_keys=True, separators=(",", ":")), int(verified)),
                )
                self._guard_file(asset, fingerprint, complete=verified)
        except DuelTimelineError:
            raise
        except (sqlite3.Error, VideoLifecycleError, OSError) as exc:
            raise DuelTimelineError(f"対戦イベントを追加できません: {exc}") from exc
        event = self.get(generated_id)
        assert event is not None
        return event

    def get(self, event_id: str) -> DuelEvent | None:
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute(
                _EVENT_SELECT + " WHERE events.event_id = ?", (event_id,)
            ).fetchone()
        return self._event(row) if row is not None else None

    def list(
        self,
        recording_id: str,
        *,
        status: str | None = None,
        event_type: str | None = None,
    ) -> tuple[DuelEvent, ...]:
        clauses = ["events.recording_id = ?"]
        parameters: list[object] = [recording_id]
        if status is not None:
            clauses.append("events.status = ?")
            parameters.append(_choice(status, STATUSES, "status"))
        if event_type is not None:
            clauses.append("events.event_type = ?")
            parameters.append(_choice(event_type, EVENT_TYPES, "event_type"))
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                _EVENT_SELECT + " WHERE "
                + " AND ".join(clauses)
                + " ORDER BY events.elapsed_ms, events.event_id",
                parameters,
            ).fetchall()
        return tuple(self._event(row) for row in rows)

    def confirm(self, event_id: str) -> DuelEvent:
        return self._transition(event_id, "confirmed")

    def reject(self, event_id: str) -> DuelEvent:
        return self._transition(event_id, "rejected")

    def update_marker_label(self, event_id: str, label: str) -> DuelEvent:
        if self.recordings_root is None:
            raise DuelTimelineError("動画の保存先を明示してからイベントを更新してください")
        normalized_id = _text(event_id, 100, "event_id", required=True)
        normalized_label = _text(label, 200, "label", required=True)
        now = datetime.now(timezone.utc)
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                _EVENT_SELECT + " WHERE events.event_id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise DuelTimelineError(f"対戦イベントが見つかりません: {event_id}")
            self._guard_event(connection, row)
            if row["event_type"] != "marker":
                raise DuelTimelineError("markerイベントだけを編集できます")
            if row["source"] != "manual":
                raise DuelTimelineError("自動判定イベントのラベルは編集できません")
            connection.execute(
                "UPDATE duel_events SET label = ?, updated_at = ? WHERE event_id = ?",
                (normalized_label, now.isoformat(), normalized_id),
            )
        event = self.get(normalized_id)
        assert event is not None
        return event

    def _transition(self, event_id: str, target: str) -> DuelEvent:
        if self.recordings_root is None:
            raise DuelTimelineError("動画の保存先を明示してからイベントを更新してください")
        before = self.get(event_id)
        if before is None:
            raise DuelTimelineError(f"対戦イベントが見つかりません: {event_id}")
        if target == "confirmed":
            self.verify_video_sources(before.recording_id)
        now = datetime.now(timezone.utc)
        with (
            closing(connect_history_database(self.database_path)) as connection,
            connection,
        ):
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                _EVENT_SELECT + " WHERE events.event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                raise DuelTimelineError(f"対戦イベントが見つかりません: {event_id}")
            self._guard_event(connection, row)
            if row["status"] != "candidate":
                raise DuelTimelineError("candidateイベントだけを確認または却下できます")
            if target == "confirmed":
                if not self._event(row).video_verified:
                    raise DuelTimelineError("動画内容・時刻基準の一致を確認できないイベントは確定できません")
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

    @contextmanager
    def _source_proof(
        self, recording_id: str, *, allow_capture: bool = False, seal_asset: bool = True,
        fingerprint: FileFingerprint | None = None,
    ) -> Iterator[tuple[VideoAsset, dict[str, object], bool]]:
        if self.recordings_root is None:
            raise DuelTimelineError("動画の保存先を明示してからイベントを更新してください")
        from .recording_retention import fingerprint_file, safe_file_path

        repository = VideoLifecycleRepository(database_path=self.database_path, recordings_root=self.recordings_root)
        asset = repository.ensure_current_asset(recording_id)
        if asset.state in {"deleted", "unavailable"}:
            raise DuelTimelineError("現在の動画を確認できません")
        owner_id, pid = f"timeline:{uuid.uuid4().hex}", os.getpid()
        lease = repository.register_use(asset_id=asset.asset_id, owner_id=owner_id, purpose="timeline", process_id=pid)
        try:
            with closing(connect_history_database(self.database_path)) as connection:
                recording = connection.execute("SELECT * FROM recordings WHERE recording_id=?", (recording_id,)).fetchone()
            active = recording["state"] in {"starting", "recording"}
            if active and not allow_capture:
                raise DuelTimelineError("録画中は自動検出候補だけを登録できます")
            path = safe_file_path(self.recordings_root, asset.path, must_exist=not active)
            proof: dict[str, object] = {
                "schema": "mdrl-timeline-capture-v1" if active else "mdrl-timeline-video-v1",
                "path": asset.path, "recording_id": recording_id, "asset_id": asset.asset_id,
                "generation": asset.generation, "time_basis": asset.time_basis,
            }
            if active:
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    stat = None
                proof.update(size_bytes=stat.st_size if stat else 0, mtime_ns=stat.st_mtime_ns if stat else 0,
                             sha256="", device=stat.st_dev if stat else 0, inode=stat.st_ino if stat else 0,
                             session_id=_session_id(recording), verification_origin="local_capture")
            else:
                fingerprint = fingerprint or fingerprint_file(path)
                if fingerprint.size_bytes <= 0:
                    raise DuelTimelineError("動画が空です")
                if asset.sha256 and (asset.size_bytes, asset.sha256) != (fingerprint.size_bytes, fingerprint.sha256):
                    raise DuelTimelineError("元動画の内容が登録済みの動画実体と一致しません")
                if seal_asset:
                    with closing(connect_history_database(self.database_path)) as connection:
                        previous = connection.execute(
                            "SELECT input_fingerprint_json FROM duel_event_sources WHERE video_asset_id=? AND video_generation=?",
                            (asset.asset_id, asset.generation),
                        ).fetchall()
                    if any(_full_proof(expected) and (expected["size_bytes"], expected["sha256"])
                           != (fingerprint.size_bytes, fingerprint.sha256)
                           for expected in (_fingerprint(row[0]) for row in previous)):
                        raise DuelTimelineError("同じ動画世代の既存マーカーと内容が一致しません")
                    asset = repository.set_verified_asset(
                        asset.asset_id, size_bytes=fingerprint.size_bytes, mtime_ns=fingerprint.mtime_ns, sha256=fingerprint.sha256,
                    )
                proof.update(size_bytes=fingerprint.size_bytes, mtime_ns=fingerprint.mtime_ns, sha256=fingerprint.sha256,
                             device=fingerprint.device, inode=fingerprint.inode)
            yield asset, proof, not active
        except (VideoLifecycleError, OSError, RuntimeError, ValueError) as exc:
            if isinstance(exc, DuelTimelineError):
                raise
            raise DuelTimelineError(f"動画の根拠を確認できません: {exc}") from exc
        finally:
            repository.release_use(lease.use_id, owner_id=owner_id, process_id=pid)

    @staticmethod
    def _guard_asset(connection: sqlite3.Connection, asset: VideoAsset) -> None:
        current = connection.execute("SELECT * FROM recording_video_assets WHERE asset_id=?", (asset.asset_id,)).fetchone()
        if (current is None or not current["is_current"] or current["state"] in {"deleted", "unavailable"}
                or (current["recording_id"], current["generation"], current["path"], current["time_basis"])
                != (asset.recording_id, asset.generation, asset.path, asset.time_basis)):
            raise DuelTimelineError("動画の実体・世代・時刻基準が変わりました")
        if connection.execute(
            "SELECT 1 FROM video_deletion_journal WHERE recording_id=? AND state IN ('intent','staged','unlinked')",
            (asset.recording_id,),
        ).fetchone():
            raise DuelTimelineError("動画削除の回復が未完了です")

    @staticmethod
    def _guard_event(connection: sqlite3.Connection, row: sqlite3.Row) -> None:
        if (not row["video_asset_id"] or not row["video_is_current"] or row["video_pending"]
                or row["video_state"] in {"deleted", "unavailable"}
                or row["video_generation"] != row["asset_generation"]
                or row["time_basis"] != row["asset_time_basis"]):
            raise DuelTimelineError("イベントの動画実体・世代・時刻基準を確認できません")

    def _event(self, row: sqlite3.Row) -> DuelEvent:
        event = _event(row)
        verified = bool(row["video_verified"] and row["video_is_current"] and not row["video_pending"]
                        and row["video_state"] == "present" and row["video_generation"] == row["asset_generation"]
                        and row["time_basis"] == row["asset_time_basis"])
        expected = _fingerprint(row["input_fingerprint_json"])
        verified = bool(verified and _full_proof(expected)
                        and (expected["size_bytes"], expected["sha256"]) == (row["asset_size"], row["asset_sha"])
                        and expected.get("time_basis", row["time_basis"]) == row["asset_time_basis"])
        if verified:
            if self.recordings_root is None:
                verified = False
            else:
                try:
                    from .recording_retention import safe_file_path

                    actual = safe_file_path(self.recordings_root, row["asset_path"]).stat()
                    verified = (actual.st_size, actual.st_mtime_ns) == (row["asset_size"], row["asset_mtime"])
                except (RuntimeError, OSError, ValueError):
                    verified = False
        return replace(event, video_verified=verified)

    def _guard_file(self, asset: VideoAsset, proof: dict[str, object], *, complete: bool) -> None:
        from .recording_retention import safe_file_path

        try:
            path = safe_file_path(self.recordings_root, asset.path, must_exist=complete)
            if complete:
                actual = path.stat()
                if (actual.st_size, actual.st_mtime_ns, actual.st_dev, actual.st_ino) != (
                    proof["size_bytes"], proof["mtime_ns"], proof["device"], proof["inode"],
                ):
                    raise DuelTimelineError("イベントの保存中に動画が変更されました")
            elif proof.get("inode", 0):
                actual = path.stat()
                if (actual.st_dev, actual.st_ino) != (proof["device"], proof["inode"]):
                    raise DuelTimelineError("候補の保存中に録画ファイルが差し替わりました")
        except (RuntimeError, OSError, ValueError) as exc:
            if isinstance(exc, DuelTimelineError):
                raise
            raise DuelTimelineError(f"動画の保存先を再確認できません: {exc}") from exc

    def verify_video_sources(
        self, recording_id: str, *, fingerprint: FileFingerprint | None = None,
    ) -> tuple[DuelEvent, ...]:
        """保存された内容の証拠と現物を照合し、一致するマーカーだけを利用可能にします。"""
        identifier = _text(recording_id, 200, "recording_id", required=True)
        if self.recordings_root is None:
            raise DuelTimelineError("動画の保存先を明示してから一致を検証してください")
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(_EVENT_SELECT + " WHERE events.recording_id=?", (identifier,)).fetchall()
        if not any(_has_proof(_fingerprint(row["input_fingerprint_json"])) for row in rows):
            return self.list(identifier)
        try:
            with self._source_proof(identifier, seal_asset=False, fingerprint=fingerprint) as (asset, actual, _verified):
                with closing(connect_history_database(self.database_path)) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    self._guard_asset(connection, asset)
                    recording = connection.execute("SELECT * FROM recordings WHERE recording_id=?", (identifier,)).fetchone()
                    current_rows = connection.execute(_EVENT_SELECT + " WHERE events.recording_id=?", (identifier,)).fetchall()
                    any_match = False
                    for row in current_rows:
                        expected = _fingerprint(row["input_fingerprint_json"])
                        owner_matches = (row["video_asset_id"] == asset.asset_id and row["video_generation"] == asset.generation
                                         and row["time_basis"] == asset.time_basis)
                        basis_matches = expected.get("time_basis", row["time_basis"]) == asset.time_basis
                        full_match = _full_proof(expected) and (expected["size_bytes"], expected["sha256"]) == (actual["size_bytes"], actual["sha256"])
                        capture_match = (
                            _local_capture_proof(expected)
                            and (expected.get("inode"), expected.get("device"), expected.get("session_id"))
                            == (actual["inode"], actual["device"], _session_id(recording))
                            and recording["size_bytes"] == actual["size_bytes"]
                        )
                        matched = bool(owner_matches and basis_matches and (full_match or capture_match))
                        any_match = any_match or matched
                        saved = {**actual, **({"session_id": expected["session_id"]} if capture_match else {})} if matched else expected
                        connection.execute(
                            "UPDATE duel_event_sources SET verified=?,input_fingerprint_json=? WHERE event_id=?",
                            (int(matched), json.dumps(saved, sort_keys=True, separators=(",", ":")), row["event_id"]),
                        )
                    if any_match:
                        connection.execute(
                            "UPDATE recording_video_assets SET state='present',size_bytes=?,mtime_ns=?,sha256=?,updated_at=? WHERE asset_id=?",
                            (actual["size_bytes"], actual["mtime_ns"], actual["sha256"], datetime.now(timezone.utc).isoformat(), asset.asset_id),
                        )
                    self._guard_file(asset, actual, complete=True)
        except (VideoLifecycleError, RuntimeError, OSError, ValueError) as exc:
            with closing(connect_history_database(self.database_path)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("UPDATE duel_event_sources SET verified=0 WHERE event_id IN (SELECT event_id FROM duel_events WHERE recording_id=?)", (identifier,))
            if isinstance(exc, DuelTimelineError):
                raise
            raise DuelTimelineError(f"動画の一致を検証できません: {exc}") from exc
        return self.list(identifier)

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

    def _validate_confirmation(
        self,
        connection: sqlite3.Connection,
        recording_id: str,
        elapsed_ms: int,
        event_type: str,
        *,
        exclude: str | None,
    ) -> None:
        rows = connection.execute(
            _EVENT_SELECT + " WHERE events.recording_id = ? AND events.status = 'confirmed' "
            "AND events.event_id != COALESCE(?, '')",
            (recording_id, exclude),
        ).fetchall()
        rows = [row for row in rows if self._event(row).video_verified]
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


_EVENT_SELECT = (
    "SELECT events.*,sources.video_asset_id,sources.video_generation,sources.time_basis,"
    "sources.verified AS video_verified,sources.input_fingerprint_json,assets.is_current AS video_is_current,"
    "assets.state AS video_state,assets.generation AS asset_generation,assets.path AS asset_path,"
    "assets.time_basis AS asset_time_basis,assets.size_bytes AS asset_size,assets.mtime_ns AS asset_mtime,assets.sha256 AS asset_sha,"
    "EXISTS(SELECT 1 FROM video_deletion_journal journal WHERE journal.recording_id=events.recording_id "
    "AND journal.state IN ('intent','staged','unlinked')) AS video_pending "
    "FROM duel_events events LEFT JOIN duel_event_sources sources USING(event_id) "
    "LEFT JOIN recording_video_assets assets ON assets.asset_id=sources.video_asset_id"
)


def _fingerprint(value: object) -> dict[str, object]:
    try:
        return _object(value, "マーカーの動画証拠")
    except VideoLifecycleError:
        return {}


def _full_proof(value: dict[str, object]) -> bool:
    digest, size = value.get("sha256"), value.get("size_bytes")
    return (isinstance(digest, str) and len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)
            and isinstance(size, int) and not isinstance(size, bool) and size > 0)


def _has_proof(value: dict[str, object]) -> bool:
    return _full_proof(value) or _local_capture_proof(value)


def _local_capture_proof(value: dict[str, object]) -> bool:
    # inode/sessionは同じ録画処理内の証拠。JSON取込やDB復元を越えて信頼しません。
    return (value.get("schema") == "mdrl-timeline-capture-v1"
            and value.get("verification_origin", "local_capture") == "local_capture"
            and isinstance(value.get("inode"), int) and not isinstance(value["inode"], bool) and value["inode"] > 0)


def _session_id(recording: sqlite3.Row) -> str:
    return f"{recording['recording_id']}:{recording['started_at'] or recording['created_at']}"


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
        video_asset_id=row["video_asset_id"],
        video_generation=row["video_generation"],
        time_basis=row["time_basis"],
        video_verified=False,
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
