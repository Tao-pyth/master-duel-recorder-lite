from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Iterator
import uuid

from .card_analysis_models import (
    CARD_ACTIONS, CARD_ACTORS, CARD_JOB_STATES, CARD_STATUSES, CARD_TIME_KINDS,
    CardAnalysisConflictError, CardAnalysisError, CardAnalysisJob, CardAnalysisReview,
    CardInputFingerprint, CardNonuseConfirmation,
    CardObservation, CardObservationValues, CardOpenerConfirmation, CardTarget,
    CardTargetSnapshot, normalize_card_name,
)
from .history_database import HISTORY_DATABASE_NAME, connect_history_database
from .duel_card_repository import (
    DuelCardRepository, card_for_target, duel_for_recording,
    write_information, write_opener,
)
from .runtime_paths import RuntimePaths


CARD_PRESETS = (
    ("preset-maxx-c", "増殖するG", ("手札誘発",)),
    ("preset-silvy", "白き森のシルヴィ", ("初動",)),
)


class CardAnalysisRepository:
    def __init__(self, database_path: Path, *, operation_lock_path: Path | None = None) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.operation_lock_path = operation_lock_path or self.database_path.parent / "video-operations.lock"
        connect_history_database(self.database_path).close()

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> CardAnalysisRepository:
        from .data_protection import initialize_protected_history_database

        initialize_protected_history_database(paths)
        return cls(paths.db / HISTORY_DATABASE_NAME, operation_lock_path=paths.data / "video-operations.lock")

    @contextmanager
    def _claim_lock(self) -> Iterator[None]:
        from .recording_lock import RecordingBusyError, RecordingLock

        try:
            with RecordingLock.acquire(self.operation_lock_path, recording_id="card-analysis-claim"):
                yield
        except RecordingBusyError as exc:
            raise CardAnalysisConflictError("動画の整理・管理操作中はカード解析を開始できません") from exc

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(connect_history_database(self.database_path)) as connection:
                connection.execute("BEGIN IMMEDIATE")
                with connection:
                    yield connection
        except sqlite3.Error as exc:
            raise CardAnalysisError(f"カード分析データを保存できません: {exc}") from exc

    def list_targets(self, *, include_disabled: bool = True) -> tuple[CardTarget, ...]:
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT target.*,link.card_id FROM card_targets AS target "
                "LEFT JOIN card_target_identity_links AS link ON link.target_id=target.target_id "
                + ("" if include_disabled else "WHERE target.enabled = 1 ")
                + "ORDER BY target.name,target.target_id"
            ).fetchall()
        return tuple(_target(row) for row in rows)

    def get_target(self, target_id: str) -> CardTarget | None:
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute("SELECT target.*,link.card_id FROM card_targets AS target "
                                     "LEFT JOIN card_target_identity_links AS link ON link.target_id=target.target_id "
                                     "WHERE target.target_id=?", (target_id,)).fetchone()
        return _target(row) if row else None

    def save_target(
        self, *, name: str, aliases: tuple[str, ...] = (), roles: tuple[str, ...] = (),
        purpose: str = "", enabled: bool = True, target_id: str | None = None,
        expected_revision: int | None = None,
    ) -> CardTarget:
        name = _text(name, 200, "名称", required=True)
        identifier = _text(target_id if target_id is not None else uuid.uuid4().hex, 100, "対象ID", required=True)
        purpose = _text(purpose, 200, "用途")
        aliases = _texts(aliases, "別名", 30)
        roles = _texts(roles or ((purpose,) if purpose.strip() else ()), "用途", 20)
        if not isinstance(enabled, bool):
            raise ValueError("enabledは真偽値で指定してください")
        keys = [normalize_card_name(value) for value in (name, *aliases)]
        if len(set(keys)) != len(keys):
            raise ValueError("同じ正規化名の名称・別名を重複登録できません")
        now = _now()
        with self._transaction() as connection:
            current = connection.execute("SELECT * FROM card_targets WHERE target_id = ?", (identifier,)).fetchone()
            _revision(current, expected_revision)
            for row in connection.execute("SELECT * FROM card_targets WHERE target_id <> ?", (identifier,)):
                other = {normalize_card_name(str(row["name"]))} | {
                    normalize_card_name(value) for value in json.loads(row["aliases_json"])
                }
                if other.intersection(keys):
                    raise ValueError("別の対象と名称・別名が重複しています")
            revision = int(current["revision"]) + 1 if current else 1
            connection.execute(
                "INSERT INTO card_targets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(target_id) DO UPDATE SET name=excluded.name, "
                "normalized_name=excluded.normalized_name, aliases_json=excluded.aliases_json, "
                "roles_json=excluded.roles_json, enabled=excluded.enabled, revision=excluded.revision, "
                "updated_at=excluded.updated_at",
                (identifier, name, keys[0], _json(aliases), _json(roles), int(enabled), revision,
                 current["created_at"] if current else now, now),
            )
            link = connection.execute("SELECT card_id FROM card_target_identity_links WHERE target_id=?", (identifier,)).fetchone()
            if link is None:
                identity = connection.execute("SELECT card_id FROM card_identities WHERE normalized_name=?", (keys[0],)).fetchone()
                card_id = str(identity[0]) if identity else identifier
                if identity is None:
                    connection.execute("INSERT INTO card_identities VALUES (?,?,?,?,?)", (card_id, name, keys[0], now, now))
                connection.execute("INSERT INTO card_target_identity_links VALUES (?,?)", (identifier, card_id))
            else:
                connection.execute("UPDATE card_identities SET name=?,normalized_name=?,updated_at=? WHERE card_id=?", (name, keys[0], now, link[0]))
        result = self.get_target(identifier)
        assert result is not None
        return result

    def seed_presets(self) -> tuple[CardTarget, ...]:
        # 無効化・改名済みプリセットは再投入で上書きしません。
        for identifier, name, roles in CARD_PRESETS:
            if self.get_target(identifier) is None and not any(
                normalize_card_name(name) in {normalize_card_name(value) for value in (item.name, *item.aliases)}
                for item in self.list_targets()
            ):
                self.save_target(target_id=identifier, name=name, roles=roles)
        return self.list_targets()

    def create_job(
        self, recording_id: str, *, fingerprint: CardInputFingerprint | dict[str, object],
        analyzer_version: str, sample_fps: float = 2.0,
    ) -> CardAnalysisJob:
        fingerprint = _fingerprint(fingerprint)
        version = _text(analyzer_version, 100, "解析方式版", required=True)
        if isinstance(sample_fps, bool) or not isinstance(sample_fps, (int, float)) or not math.isfinite(sample_fps) or not 0.25 <= sample_fps <= 2:
            raise ValueError("sample_fpsは0.25から2.0で指定してください")
        identifier, now = uuid.uuid4().hex, _now()
        with self._transaction() as connection:
            _recording(connection, recording_id)
            active = connection.execute(
                "SELECT job_id FROM card_analysis_jobs WHERE recording_id = ? "
                "AND state IN ('queued', 'running', 'waiting')", (recording_id,),
            ).fetchone()
            if active:
                raise CardAnalysisConflictError("この録画には既に未完了の解析があります")
            targets = tuple(_target(row).snapshot() for row in connection.execute(
                "SELECT * FROM card_targets WHERE enabled = 1 ORDER BY target_id"
            ))
            if not targets:
                raise CardAnalysisError("有効な対象カードがありません")
            generation = int(connection.execute(
                "SELECT COALESCE(MAX(generation), 0) + 1 FROM card_analysis_jobs WHERE recording_id = ?",
                (recording_id,),
            ).fetchone()[0])
            connection.execute(
                "INSERT INTO card_analysis_jobs VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, 0, '', '{}', ?, ?)",
                (identifier, recording_id, generation, _json(fingerprint.to_dict()),
                 _json([target.to_dict() for target in targets]), version, float(sample_fps), now, now),
            )
            _bind_job_source(connection, identifier, recording_id, fingerprint, now)
        result = self.get_job(identifier)
        assert result is not None
        return result

    def get_job(self, job_id: str) -> CardAnalysisJob | None:
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute("SELECT * FROM card_analysis_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _job(row) if row else None

    def list_jobs(self, recording_id: str | None = None, *, state: str | None = None) -> tuple[CardAnalysisJob, ...]:
        clauses, parameters = [], []
        if recording_id is not None:
            clauses.append("recording_id = ?")
            parameters.append(recording_id)
        if state is not None:
            _choice(state, CARD_JOB_STATES, "ジョブ状態")
            clauses.append("state = ?")
            parameters.append(state)
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT * FROM card_analysis_jobs " + ("WHERE " + " AND ".join(clauses) if clauses else "")
                + " ORDER BY created_at, generation, job_id", parameters,
            ).fetchall()
        return tuple(_job(row) for row in rows)

    def claim_job(self, job_id: str) -> CardAnalysisJob:
        with self._claim_lock(), self._transaction() as connection:
            job = _require_job(connection, job_id)
            if job.state not in {"queued", "waiting"}:
                raise CardAnalysisConflictError("待機中の解析だけ開始できます")
            _recording(connection, job.recording_id)
            _require_video_source(connection, job_id=job.job_id)
            if connection.execute("SELECT 1 FROM card_analysis_jobs WHERE state = 'running'").fetchone():
                raise CardAnalysisConflictError("他の解析が実行中です")
            connection.execute(
                "UPDATE card_analysis_jobs SET state='running', progress=?, error='', updated_at=? WHERE job_id=?",
                (job.progress if job.result_metadata.get("checkpoint") else 0, _now(), job_id),
            )
        return self.get_job(job_id)

    def update_checkpoint(self, job_id: str, checkpoint: dict[str, object], *,
                          progress: float | None = None) -> CardAnalysisJob:
        if progress is not None and (isinstance(progress, bool) or not isinstance(progress, (int, float))
                                     or not math.isfinite(progress) or not 0 <= progress <= 1):
            raise ValueError("進捗は0から1で指定してください")
        # 候補テーブルに混ぜず、同じジョブの途中経過だけを永続化します。
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpointは構造化情報で指定してください")
        payload = json.loads(_json(checkpoint))
        with self._transaction() as connection:
            job = _require_job(connection, job_id)
            if job.state != "running":
                raise CardAnalysisConflictError("実行中の解析だけcheckpointを保存できます")
            _recording(connection, job.recording_id)
            _require_video_source(connection, job_id=job.job_id)
            source = _validate_checkpoint(job, payload)
            previous = job.result_metadata.get("checkpoint")
            if previous and payload["next_frame_index"] < previous["next_frame_index"]:
                raise CardAnalysisConflictError("古い解析checkpointでは上書きできません")
            metadata = {**job.result_metadata, "checkpoint": payload}
            connection.execute(
                "UPDATE card_analysis_jobs SET fingerprint_json=?,result_metadata_json=?,progress=?,updated_at=? WHERE job_id=?",
                (_json(source.to_dict()), _json(metadata), max(job.progress, float(progress))
                 if progress is not None else job.progress, _now(), job_id),
            )
        return self.get_job(job_id)

    def update_progress(self, job_id: str, progress: float) -> CardAnalysisJob:
        if isinstance(progress, bool) or not isinstance(progress, (int, float)) or not math.isfinite(progress) or not 0 <= progress <= 1:
            raise ValueError("進捗は0から1で指定してください")
        with self._transaction() as connection:
            job = _require_job(connection, job_id)
            if job.state != "running":
                raise CardAnalysisConflictError("実行中の解析だけ進捗を更新できます")
            connection.execute("UPDATE card_analysis_jobs SET progress=?, updated_at=? WHERE job_id=?",
                               (max(job.progress, float(progress)), _now(), job_id))
        return self.get_job(job_id)

    def complete_job(
        self, job_id: str, observations: tuple[CardObservationValues, ...] | list[dict[str, object]], *,
        fingerprint: CardInputFingerprint | dict[str, object] | None = None,
        result_metadata: dict[str, object] | None = None,
    ) -> CardAnalysisJob:
        values = tuple(_values(value) for value in observations)
        if len({value.source_key for value in values}) != len(values):
            raise ValueError("解析結果内に重複したsource_keyがあります")
        if result_metadata is not None and not isinstance(result_metadata, dict):
            raise ValueError("解析結果metadataは構造化情報で指定してください")
        final_metadata = dict(result_metadata or {})
        final_metadata.pop("checkpoint", None)
        metadata = _json(final_metadata)
        now = _now()
        with self._transaction() as connection:
            job = _require_job(connection, job_id)
            if job.state != "running":
                raise CardAnalysisConflictError("実行中の解析だけ完了できます")
            _recording(connection, job.recording_id)
            source = _require_video_source(connection, job_id=job.job_id)
            final_fingerprint = _fingerprint(fingerprint or job.fingerprint, require_hash=True)
            if (job.fingerprint.path, job.fingerprint.size_bytes, job.fingerprint.mtime_ns) != (
                final_fingerprint.path, final_fingerprint.size_bytes, final_fingerprint.mtime_ns
            ) or (job.fingerprint.sha256 and job.fingerprint.sha256 != final_fingerprint.sha256):
                raise CardAnalysisError("解析元のfingerprintが変わったため結果を保存できません")
            asset = connection.execute("SELECT * FROM recording_video_assets WHERE asset_id=?", (source["video_asset_id"],)).fetchone()
            if _different_fingerprint(asset, final_fingerprint):
                raise CardAnalysisConflictError("解析中に動画の内容が変わったため結果を保存できません")
            snapshot_ids = {target.target_id for target in job.target_snapshot}
            for value in values:
                _validate_observation(connection, job.recording_id, value)
                if value.target_id is not None and value.target_id not in snapshot_ids:
                    raise ValueError("解析開始時の対象snapshotにないカードです")
            _verify_previous_markers(connection, source, final_fingerprint)
            # 完了前の結果は保存せず、成功時だけ候補をまとめて入れ替えます。
            connection.execute(
                "DELETE FROM card_observations WHERE recording_id=? AND status='candidate' AND manual_locked=0",
                (job.recording_id,),
            )
            protected_keys = set()
            for old in connection.execute(
                "SELECT observation.event_id,observation.source_key,link.video_asset_id,link.video_generation,link.time_basis,link.verified "
                "FROM card_observations AS observation LEFT JOIN card_observation_links AS link ON link.event_id=observation.event_id "
                "WHERE observation.recording_id=?", (job.recording_id,),
            ).fetchall():
                if old["verified"] and (old["video_asset_id"], old["video_generation"], old["time_basis"]) == (source["video_asset_id"], source["video_generation"], source["time_basis"]):
                    protected_keys.add(old["source_key"])
                else:
                    # 一致を証明できない旧根拠は保管し、新動画の同名source_keyへ流用しません。
                    connection.execute("UPDATE card_observations SET source_key=? WHERE event_id=?", ("unverified:" + old["event_id"], old["event_id"]))
            for value in values:
                if value.source_key in protected_keys:
                    continue
                event_id = uuid.uuid5(uuid.NAMESPACE_URL, job.recording_id + "\n" + source["video_asset_id"] + "\n" + str(source["video_generation"]) + "\n" + value.source_key).hex
                _insert_observation(connection, event_id, job.recording_id, job.job_id,
                                    job.generation, value, "candidate", False, now)
                connection.execute(
                    "INSERT INTO card_observation_links "
                    "(event_id,video_asset_id,video_generation,time_basis,input_fingerprint_json,verified) "
                    "VALUES (?,?,?,?,?,1)",
                    (event_id, source["video_asset_id"], source["video_generation"], source["time_basis"],
                     _json(final_fingerprint.to_dict())),
                )
            connection.execute("UPDATE card_analysis_job_sources SET input_fingerprint_json=?,verified=1 WHERE job_id=?",
                               (_json(final_fingerprint.to_dict()), job.job_id))
            connection.execute("UPDATE recording_video_assets SET size_bytes=?,mtime_ns=?,sha256=?,state='present',updated_at=? WHERE asset_id=?",
                               (final_fingerprint.size_bytes, final_fingerprint.mtime_ns,
                                final_fingerprint.sha256, now, source["video_asset_id"]))
            connection.execute(
                "UPDATE card_analysis_jobs SET state='completed', progress=1, error='', "
                "fingerprint_json=?, result_metadata_json=?, updated_at=? WHERE job_id=?",
                (_json(final_fingerprint.to_dict()), metadata, now, job_id),
            )
        return self.get_job(job_id)

    def fail_job(self, job_id: str, error: str) -> CardAnalysisJob:
        return self._stop_job(job_id, "failed", _text(error, 4000, "エラー", required=True))

    def defer_job(self, job_id: str, reason: str = "中断して待機") -> CardAnalysisJob:
        return self._stop_job(job_id, "waiting", _text(reason, 4000, "待機理由"))

    def cancel_job(self, job_id: str, reason: str = "取り消しました") -> CardAnalysisJob:
        return self._stop_job(job_id, "cancelled", _text(reason, 4000, "取消理由"))

    def _stop_job(self, job_id: str, state: str, message: str) -> CardAnalysisJob:
        with self._transaction() as connection:
            job = _require_job(connection, job_id)
            if job.state not in {"queued", "running", "waiting"}:
                raise CardAnalysisConflictError("未完了の解析だけ状態を変更できます")
            connection.execute("UPDATE card_analysis_jobs SET state=?, error=?, updated_at=? WHERE job_id=?",
                               (state, message, _now(), job_id))
        return self.get_job(job_id)

    def recover_running(self) -> int:
        with self._transaction() as connection:
            jobs = tuple(_job(row) for row in connection.execute("SELECT * FROM card_analysis_jobs WHERE state='running'"))
            for job in jobs:
                connection.execute(
                    "UPDATE card_analysis_jobs SET state='waiting', progress=?, "
                    "error='前回の解析が中断しました。再試行できます', updated_at=? WHERE job_id=?",
                    (job.progress if job.result_metadata.get("checkpoint") else 0, _now(), job.job_id),
                )
            return len(jobs)

    def get_observation(self, event_id: str) -> CardObservation | None:
        with closing(connect_history_database(self.database_path)) as connection:
            row = connection.execute(_OBSERVATION_SELECT + " WHERE observation.event_id=?", (event_id,)).fetchone()
        return _observation(row) if row else None

    def verify_video_sources(self, recording_id: str, fingerprint: CardInputFingerprint) -> int:
        """呼出元が検証した現物SHAと一致する保存済み根拠だけを利用可能にします。"""
        fingerprint = _fingerprint(fingerprint, require_hash=True)
        with self._transaction() as connection:
            asset = connection.execute("SELECT * FROM recording_video_assets WHERE recording_id=? AND is_current=1", (recording_id,)).fetchone()
            if asset is None or asset["state"] in {"deleted", "unavailable"} or _different_fingerprint(asset, fingerprint):
                raise CardAnalysisConflictError("現在の動画実体と検証結果が一致しません")
            if connection.execute("SELECT 1 FROM video_deletion_journal WHERE asset_id=? AND state IN ('intent','staged','unlinked')", (asset["asset_id"],)).fetchone():
                raise CardAnalysisConflictError("動画整理中は根拠の利用状態を変更できません")
            connection.execute("UPDATE recording_video_assets SET size_bytes=?,mtime_ns=?,sha256=?,state='present',updated_at=? WHERE asset_id=?",
                               (fingerprint.size_bytes, fingerprint.mtime_ns, fingerprint.sha256, _now(), asset["asset_id"]))
            changed = 0
            for table, key in (("card_observation_links", "event_id"), ("card_analysis_job_sources", "job_id")):
                for row in connection.execute(
                    f"SELECT * FROM {table} WHERE video_asset_id=? AND video_generation=? AND time_basis=? AND verified=0",
                    (asset["asset_id"], asset["generation"], asset["time_basis"]),
                ).fetchall():
                    raw = json.loads(row["input_fingerprint_json"])
                    if raw.get("sha256") == fingerprint.sha256 and raw.get("size_bytes") == fingerprint.size_bytes:
                        connection.execute(f"UPDATE {table} SET verified=1 WHERE {key}=?", (row[key],))
                        changed += 1
            return changed

    def list_observations(self, recording_id: str, *, status: str | None = None) -> tuple[CardObservation, ...]:
        clause, parameters = " WHERE observation.recording_id=?", [recording_id]
        if status is not None:
            clause += " AND observation.status=?"
            parameters.append(_choice(status, CARD_STATUSES, "確認状態"))
        with closing(connect_history_database(self.database_path)) as connection:
            rows = connection.execute(_OBSERVATION_SELECT + clause + " ORDER BY observation.observed_ms, observation.event_id", parameters).fetchall()
        return tuple(_observation(row) for row in rows)

    def review_observation(
        self, event_id: str, *, expected_revision: int, values: CardObservationValues | dict[str, object] | None = None,
        status: str | None = None, is_opener: bool | None = None, opener_expected_revision: int | None = None,
        information_expected_revision: int | None = None, expected_generation: int | None = None,
    ) -> CardObservation:
        with self._transaction() as connection:
            row = connection.execute(_OBSERVATION_SELECT + " WHERE observation.event_id=?", (event_id,)).fetchone()
            if row is None:
                raise CardAnalysisError("カード観測がありません")
            _revision(row, expected_revision)
            current = _observation(row)
            if expected_generation is not None and (
                isinstance(expected_generation, bool) or not isinstance(expected_generation, int)
                or expected_generation != current.generation
            ):
                raise CardAnalysisConflictError("観測の解析世代が更新されています。再読込してください")
            _require_video_source(connection, event_id=event_id)
            updated = _values(values) if values is not None else current.values
            if updated.source_key != current.values.source_key:
                raise ValueError("訂正でsource_keyを変更できません")
            new_status = _choice(current.status if status is None else status, CARD_STATUSES, "確認状態")
            _validate_observation(connection, current.recording_id, updated)
            meaningful_confirmation = status == "confirmed" and updated.supports_use and updated.target_id and updated.actor != "unknown"
            if meaningful_confirmation:
                if _has_nonuse(connection, current.recording_id, updated.target_id, updated.actor):
                    raise CardAnalysisConflictError("このカードの非使用確認を解除してから使用を確定してください")
                duel_id = duel_for_recording(connection, current.recording_id, create=True)
                information = write_information(connection, duel_id, card_for_target(connection, updated.target_id), updated.actor, "used",
                                                expected_revision=information_expected_revision, source="review")
                connection.execute("UPDATE card_observation_links SET information_id=? WHERE event_id=?", (information.information_id, event_id))
            connection.execute(
                "UPDATE card_observations SET target_id=?, card_name=?, actor=?, action=?, observed_ms=?, actual_ms=?, "
                "time_kind=?, evidence_json=?, confidence=?, status=?, revision=revision+1, manual_locked=1, updated_at=? "
                "WHERE event_id=? AND revision=?",
                (updated.target_id, updated.card_name, updated.actor, updated.action, updated.observed_ms,
                 updated.actual_ms, updated.time_kind, _json(updated.evidence), updated.confidence,
                 new_status, _now(), event_id, expected_revision),
            )
            if not meaningful_confirmation and (
                new_status != "confirmed" or updated.actor != current.actor
                or updated.target_id != current.target_id or not updated.supports_use
            ):
                connection.execute("UPDATE card_observation_links SET information_id=NULL WHERE event_id=?", (event_id,))
            # 根拠・時刻の訂正や却下から、対戦正本・初動を変更しません。
            if is_opener is not None:
                if not isinstance(is_opener, bool):
                    raise ValueError("is_openerは真偽値で指定してください")
                if is_opener:
                    _set_opener(connection, current.recording_id, updated.actor, event_id, "confirmed", opener_expected_revision)
        result = self.get_observation(event_id)
        assert result is not None
        return result

    def update_observation(self, event_id: str, *, expected_revision: int, status: str | None = None,
                           is_opener: bool | None = None, opener_expected_revision: int | None = None,
                           information_expected_revision: int | None = None, expected_generation: int | None = None,
                           **changes: object) -> CardObservation:
        current = self.get_observation(event_id)
        if current is None:
            raise CardAnalysisError("カード観測がありません")
        aliases = {"card_target_id": "target_id", "actual_use_ms": "actual_ms", "name": "card_name"}
        values = replace(current.values, **{aliases.get(key, key): value for key, value in changes.items()})
        return self.review_observation(event_id, expected_revision=expected_revision, values=values,
                                       status=status, is_opener=is_opener, opener_expected_revision=opener_expected_revision,
                                       information_expected_revision=information_expected_revision, expected_generation=expected_generation)

    def confirm_observation(self, event_id: str, *, expected_revision: int,
                            information_expected_revision: int | None = None,
                            expected_generation: int | None = None) -> CardObservation:
        current = self.get_observation(event_id)
        if current is None:
            raise CardAnalysisError("カード観測がありません")
        return self.review_observation(
            event_id, expected_revision=expected_revision, status="confirmed",
            information_expected_revision=current.information_revision if information_expected_revision is None else information_expected_revision,
            expected_generation=current.generation if expected_generation is None else expected_generation,
        )

    def reject_observation(self, event_id: str, *, expected_revision: int) -> CardObservation:
        return self.review_observation(event_id, expected_revision=expected_revision, status="rejected")

    def set_nonuse(self, recording_id: str, target_id: str, actor: str, confirmed: bool = True,
                   *, expected_revision: int | None = None) -> CardNonuseConfirmation:
        _choice(actor, {"self", "opponent"}, "使用者")
        if not isinstance(confirmed, bool):
            raise ValueError("非使用確認は真偽値で指定してください")
        with self._transaction() as connection:
            _require_target(connection, target_id)
            duel_id = duel_for_recording(connection, recording_id, create=True)
            card_id = card_for_target(connection, target_id)
            current = connection.execute(
                "SELECT * FROM duel_card_information WHERE duel_id=? AND card_id=? AND actor=?",
                (duel_id, card_id, actor),
            ).fetchone()
            _revision(current, expected_revision)
            if current is not None and current["state"] == "used":
                raise CardAnalysisConflictError("確認済みの使用があります。対戦カード情報から訂正してください")
            result = write_information(connection, duel_id, card_id, actor, "not_used" if confirmed else "unknown",
                                       expected_revision=expected_revision, source="manual")
            return CardNonuseConfirmation(recording_id, target_id, actor, confirmed, result.revision, result.updated_at)

    def set_opener(self, recording_id: str, actor: str, *, event_id: str | None = None,
                   target_id: str | None = None, status: str = "confirmed",
                   expected_revision: int | None = None) -> CardOpenerConfirmation:
        with self._transaction() as connection:
            if status == "confirmed" and event_id is None and target_id is not None:
                duel_id = duel_for_recording(connection, recording_id, create=True)
                write_opener(connection, duel_id, actor, card_for_target(connection, target_id), status,
                             expected_revision=expected_revision, source="manual")
            else:
                _set_opener(connection, recording_id, actor, event_id, status, expected_revision)
        return next(item for item in self.get_review(recording_id).openers if item.actor == actor)

    def get_review(self, recording_id: str) -> CardAnalysisReview:
        with closing(connect_history_database(self.database_path)) as connection:
            jobs = connection.execute("SELECT * FROM card_analysis_jobs WHERE recording_id=? ORDER BY generation DESC", (recording_id,)).fetchall()
            duel_id = duel_for_recording(connection, recording_id)
            nonuses = connection.execute(
                "SELECT information.*,link.target_id FROM duel_card_information AS information "
                "JOIN card_target_identity_links AS link ON link.card_id=information.card_id "
                "WHERE information.duel_id=? AND information.state IN ('not_used','unknown') ORDER BY link.target_id,information.actor",
                (duel_id,),
            ).fetchall()
            openers = connection.execute(
                "SELECT opener.*,link.target_id,(SELECT observation.event_id FROM card_observations AS observation "
                "JOIN card_observation_links AS source ON source.event_id=observation.event_id "
                "JOIN duel_card_information AS information ON information.information_id=source.information_id "
                "WHERE observation.recording_id=? AND information.card_id=opener.card_id AND information.actor=opener.actor "
                "AND observation.status='confirmed' ORDER BY observation.observed_ms,observation.event_id LIMIT 1) AS event_id "
                "FROM duel_card_openers AS opener LEFT JOIN card_target_identity_links AS link ON link.card_id=opener.card_id "
                "WHERE opener.duel_id=? ORDER BY opener.actor", (recording_id, duel_id),
            ).fetchall()
            observation_rows = connection.execute(_OBSERVATION_SELECT + " WHERE observation.recording_id=? ORDER BY observation.observed_ms,observation.event_id", (recording_id,)).fetchall()
        return CardAnalysisReview(
            recording_id, _job(jobs[0]) if jobs else None,
            next((_job(row) for row in jobs if row["state"] == "completed"), None),
            tuple(_observation(row) for row in observation_rows),
            tuple(CardNonuseConfirmation(recording_id, str(row["target_id"]), str(row["actor"]),
                                        row["state"] == "not_used", int(row["revision"]), _date(row["updated_at"])) for row in nonuses),
            tuple(CardOpenerConfirmation(recording_id, str(row["actor"]), row["event_id"], row["target_id"],
                                        str(row["status"]), int(row["revision"]), _date(row["updated_at"])) for row in openers),
            duel_id, DuelCardRepository(self.database_path).get_review(duel_id).information if duel_id else (),
        )


_OBSERVATION_SELECT = """
SELECT observation.*,link.information_id,COALESCE(information.revision,0) AS information_revision,
target_link.card_id,link.video_generation,
(link.verified=1 AND asset.is_current=1 AND asset.state='present'
 AND asset.generation=link.video_generation AND asset.time_basis=link.time_basis) AS video_verified, EXISTS (
    SELECT 1 FROM duel_card_openers AS opener
    WHERE opener.duel_id=duel.duel_id AND opener.actor=observation.actor
      AND opener.card_id=target_link.card_id AND opener.status='confirmed'
      AND observation.status='confirmed' AND information.state='used'
      AND link.information_id=information.information_id
      AND observation.action<>'unknown' AND json_extract(observation.evidence_json,'$.supports_use')=1
) AS is_opener FROM card_observations AS observation
LEFT JOIN card_observation_links AS link ON link.event_id=observation.event_id
LEFT JOIN recording_video_assets AS asset ON asset.asset_id=link.video_asset_id
LEFT JOIN card_target_identity_links AS target_link ON target_link.target_id=observation.target_id
LEFT JOIN duel_records AS duel ON duel.recording_id=observation.recording_id
LEFT JOIN duel_card_information AS information ON information.duel_id=duel.duel_id
    AND information.card_id=target_link.card_id AND information.actor=observation.actor
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _text(value: str, maximum: int, label: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value.strip()) > maximum or "\x00" in value:
        raise ValueError(f"{label}の値が不正です")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{label}を入力してください")
    return value


def _texts(values: tuple[str, ...], label: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or len(values) > maximum:
        raise ValueError(f"{label}の一覧が不正です")
    result = tuple(_text(value, 200, label, required=True) for value in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{label}が重複しています")
    return result


def _choice(value: str, allowed: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{label}の値が不正です: {value}")
    return value


def _revision(row: sqlite3.Row | None, expected: int | None) -> None:
    if expected is not None:
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError("expected_revisionは0以上の整数で指定してください")
        if expected != (int(row["revision"]) if row else 0):
            raise CardAnalysisConflictError("編集の競合を検出しました。再読込してください")


def _fingerprint(value: CardInputFingerprint | dict[str, object], *, require_hash: bool = False) -> CardInputFingerprint:
    value = CardInputFingerprint.from_dict(value) if isinstance(value, dict) else value
    if not isinstance(value, CardInputFingerprint):
        raise ValueError("fingerprintの形式が不正です")
    _text(value.path, 4000, "録画パス", required=True)
    for number in (value.size_bytes, value.mtime_ns):
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise ValueError("fingerprintのサイズ・更新時刻が不正です")
    if not isinstance(value.sha256, str) or (value.sha256 and re.fullmatch(r"[0-9a-f]{64}", value.sha256) is None):
        raise ValueError("SHA256の形式が不正です")
    if require_hash and not value.sha256:
        raise ValueError("解析完了にはSHA256の確認が必要です")
    return value


def _values(value: CardObservationValues | dict[str, object]) -> CardObservationValues:
    value = CardObservationValues(**value) if isinstance(value, dict) else value
    if not isinstance(value, CardObservationValues):
        raise ValueError("カード観測の形式が不正です")
    _text(value.card_name, 200, "カード名", required=value.target_id is not None)
    _text(value.source_key, 1000, "根拠ID", required=True)
    _choice(value.actor, CARD_ACTORS, "使用者")
    _choice(value.action, CARD_ACTIONS, "行動")
    _choice(value.time_kind, CARD_TIME_KINDS, "時刻の意味")
    for number in (value.observed_ms, value.actual_ms):
        if number is not None and (isinstance(number, bool) or not isinstance(number, int) or number < 0):
            raise ValueError("時刻は0以上のミリ秒整数で指定してください")
    if value.observed_ms is None or (value.time_kind == "actual_use" and value.actual_ms is None):
        raise ValueError("観測時刻と確認済み使用時刻を指定してください")
    if value.confidence is not None and (isinstance(value.confidence, bool) or not isinstance(value.confidence, (int, float)) or not math.isfinite(value.confidence) or not 0 <= value.confidence <= 1):
        raise ValueError("認識信頼度は0から1で指定してください")
    if not isinstance(value.evidence, dict):
        raise ValueError("根拠は構造化情報で指定してください")
    if "supports_use" in value.evidence and not isinstance(value.evidence["supports_use"], bool):
        raise ValueError("supports_useは真偽値で指定してください")
    _json(value.evidence)
    return value


def _validate_checkpoint(job: CardAnalysisJob, value: dict[str, object]) -> CardInputFingerprint:
    required = {"schema", "analyzer_version", "source_fingerprint", "targets_sha256", "sample_fps",
                "source_fps", "reported_frames", "next_frame_index", "next_sample_ms", "last_timestamp",
                "observations", "state", "timestamp_fallback", "warnings"}
    if not required.issubset(value) or value["schema"] != "card-analysis-checkpoint-v1":
        raise ValueError("checkpointのスキーマが不正です")
    if value["analyzer_version"] != job.analyzer_version or value["sample_fps"] != job.sample_fps:
        raise CardAnalysisError("checkpointの解析方式版・fpsがジョブと一致しません")
    source = _fingerprint(value["source_fingerprint"], require_hash=True)
    if (source.path, source.size_bytes, source.mtime_ns) != (
        job.fingerprint.path, job.fingerprint.size_bytes, job.fingerprint.mtime_ns
    ) or (job.fingerprint.sha256 and job.fingerprint.sha256 != source.sha256):
        raise CardAnalysisError("checkpointの解析元fingerprintがジョブと一致しません")
    targets_sha256 = hashlib.sha256(json.dumps(
        [target.to_dict() for target in job.target_snapshot], sort_keys=True, ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    if value["targets_sha256"] != targets_sha256:
        raise CardAnalysisError("checkpointの対象snapshotがジョブと一致しません")
    for key in ("reported_frames", "next_frame_index"):
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            raise ValueError(f"checkpointの{key}は0以上の整数で指定してください")
    for key in ("source_fps", "next_sample_ms", "last_timestamp"):
        number = value[key]
        minimum = -1 if key == "last_timestamp" else 0
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or number < minimum:
            raise ValueError(f"checkpointの{key}が不正です")
    if value["source_fps"] <= 0:
        raise ValueError("checkpointのsource_fpsは0より大きい必要があります")
    if not isinstance(value["observations"], list) or not isinstance(value["state"], dict):
        raise ValueError("checkpointの観測・解析stateが不正です")
    if not isinstance(value["timestamp_fallback"], bool) or not isinstance(value["warnings"], list) or not all(
        isinstance(warning, str) for warning in value["warnings"]
    ):
        raise ValueError("checkpointの時刻・警告情報が不正です")
    return source


def _recording(connection: sqlite3.Connection, recording_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM recordings WHERE recording_id=?", (recording_id,)).fetchone()
    if row is None or row["state"] != "completed":
        raise CardAnalysisError("正常完了した録画だけを解析・確認できます")
    return row


def _bind_job_source(connection: sqlite3.Connection, job_id: str, recording_id: str,
                     fingerprint: CardInputFingerprint, now: str) -> None:
    asset = connection.execute("SELECT * FROM recording_video_assets WHERE recording_id=? AND is_current=1", (recording_id,)).fetchone()
    if asset is None:
        recording = _recording(connection, recording_id)
        identifier = uuid.uuid4().hex
        generation = int(connection.execute("SELECT COALESCE(MAX(generation),0)+1 FROM recording_video_assets WHERE recording_id=?", (recording_id,)).fetchone()[0])
        connection.execute(
            "INSERT INTO recording_video_assets (asset_id,recording_id,generation,path,size_bytes,mtime_ns,sha256,state,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,'unverified',?,?)",
            (identifier, recording_id, generation, recording["output_path"], fingerprint.size_bytes, fingerprint.mtime_ns, fingerprint.sha256, now, now),
        )
        asset = connection.execute("SELECT * FROM recording_video_assets WHERE asset_id=?", (identifier,)).fetchone()
    if asset["state"] in {"deleted", "unavailable"}:
        raise CardAnalysisConflictError("現在の動画は利用できません。動画を確認してから解析してください")
    if _different_fingerprint(asset, fingerprint):
        raise CardAnalysisConflictError("解析入力と現在の動画が一致しません。動画を再関連付けしてください")
    connection.execute(
        "INSERT INTO card_analysis_job_sources (job_id,video_asset_id,video_generation,time_basis,input_fingerprint_json,verified) "
        "VALUES (?,?,?,?,?,?)",
        (job_id, asset["asset_id"], asset["generation"], asset["time_basis"], _json(fingerprint.to_dict()), 1),
    )


def _different_fingerprint(asset: sqlite3.Row, fingerprint: CardInputFingerprint) -> bool:
    if asset["sha256"] and fingerprint.sha256:
        return asset["sha256"] != fingerprint.sha256 or (
            asset["size_bytes"] is not None and asset["size_bytes"] != fingerprint.size_bytes
        )
    return any(asset[key] is not None and asset[key] != value for key, value in (
        ("size_bytes", fingerprint.size_bytes), ("mtime_ns", fingerprint.mtime_ns),
    ))


def _require_video_source(connection: sqlite3.Connection, *, job_id: str | None = None,
                          event_id: str | None = None) -> sqlite3.Row:
    if job_id is not None:
        row = connection.execute("SELECT * FROM card_analysis_job_sources WHERE job_id=?", (job_id,)).fetchone()
    else:
        row = connection.execute("SELECT * FROM card_observation_links WHERE event_id=?", (event_id,)).fetchone()
    if row is None:
        raise CardAnalysisConflictError("動画との関連を確認できません。再解析または再関連付けしてください")
    asset = connection.execute("SELECT * FROM recording_video_assets WHERE asset_id=?", (row["video_asset_id"],)).fetchone()
    if asset is None or not asset["is_current"] or asset["generation"] != row["video_generation"] or asset["time_basis"] != row["time_basis"] or asset["state"] in {"deleted", "unavailable"}:
        raise CardAnalysisConflictError("動画が削除・差し替えされています。古い解析結果は保存できません")
    if connection.execute("SELECT 1 FROM video_deletion_journal WHERE asset_id=? AND state IN ('intent','staged','unlinked')", (asset["asset_id"],)).fetchone():
        raise CardAnalysisConflictError("動画整理中のため解析結果を保存できません")
    raw = json.loads(row["input_fingerprint_json"])
    if raw and _different_fingerprint(asset, CardInputFingerprint.from_dict(raw)):
        raise CardAnalysisConflictError("解析入力と動画の内容が一致しません")
    if not row["verified"]:
        if asset["state"] != "present" or not asset["sha256"] or raw.get("sha256") != asset["sha256"]:
            raise CardAnalysisConflictError("旧データと現在の動画の一致を確認できません。対戦カード情報は動画なしで編集できます")
        table, key, identifier = ("card_analysis_job_sources", "job_id", job_id) if job_id is not None else ("card_observation_links", "event_id", event_id)
        connection.execute(f"UPDATE {table} SET verified=1 WHERE {key}=?", (identifier,))
    return row


def _verify_previous_markers(connection: sqlite3.Connection, source: sqlite3.Row, fingerprint: CardInputFingerprint) -> None:
    for marker in connection.execute(
        "SELECT * FROM card_observation_links WHERE video_asset_id=? AND video_generation=? AND time_basis=? AND verified=0",
        (source["video_asset_id"], source["video_generation"], source["time_basis"]),
    ).fetchall():
        raw = json.loads(marker["input_fingerprint_json"])
        if raw.get("sha256") == fingerprint.sha256 and raw.get("size_bytes") == fingerprint.size_bytes:
            connection.execute("UPDATE card_observation_links SET verified=1 WHERE event_id=?", (marker["event_id"],))


def _require_target(connection: sqlite3.Connection, target_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM card_targets WHERE target_id=?", (target_id,)).fetchone()
    if row is None:
        raise CardAnalysisError("対象カードがありません")
    return row


def _validate_observation(connection: sqlite3.Connection, recording_id: str, value: CardObservationValues) -> None:
    recording = _recording(connection, recording_id)
    if value.target_id is not None:
        _require_target(connection, value.target_id)
    duration = recording["duration_seconds"]
    if duration is not None and any(number is not None and number > round(float(duration) * 1000)
                                    for number in (value.observed_ms, value.actual_ms)):
        raise ValueError("カードの時刻が録画時間を超えています")


def _require_job(connection: sqlite3.Connection, job_id: str) -> CardAnalysisJob:
    row = connection.execute("SELECT * FROM card_analysis_jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise CardAnalysisError("解析ジョブがありません")
    return _job(row)


def _has_nonuse(connection: sqlite3.Connection, recording_id: str, target_id: str, actor: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM duel_card_information AS information "
        "JOIN duel_records AS duel ON duel.duel_id=information.duel_id "
        "JOIN card_target_identity_links AS link ON link.card_id=information.card_id "
        "WHERE duel.recording_id=? AND link.target_id=? AND information.actor=? AND information.state='not_used'",
        (recording_id, target_id, actor),
    ).fetchone() is not None


def _set_opener(connection: sqlite3.Connection, recording_id: str, actor: str, event_id: str | None,
                status: str, expected_revision: int | None) -> None:
    _choice(actor, {"self", "opponent"}, "初動の使用者")
    _choice(status, {"confirmed", "unknown"}, "初動の確認状態")
    if status == "confirmed":
        row = connection.execute("SELECT * FROM card_observations WHERE event_id=?", (event_id,)).fetchone()
        if row is None or row["recording_id"] != recording_id or row["actor"] != actor or row["status"] != "confirmed" or row["target_id"] is None or not _row_values(row).supports_use:
            raise ValueError("初動は同じ録画・使用者の確認済み使用から選んでください")
        _require_video_source(connection, event_id=event_id)
        card_id = card_for_target(connection, str(row["target_id"]))
    else:
        card_id = None
    duel_id = duel_for_recording(connection, recording_id, create=True)
    write_opener(connection, duel_id, actor, card_id, status, expected_revision=expected_revision, source="review")


def _insert_observation(connection: sqlite3.Connection, event_id: str, recording_id: str, job_id: str | None,
                        generation: int, value: CardObservationValues, status: str, locked: bool, now: str) -> None:
    connection.execute(
        "INSERT INTO card_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, recording_id, job_id, generation, value.target_id, value.card_name, value.actor, value.action,
         value.observed_ms, value.actual_ms, value.time_kind, _json(value.evidence), value.confidence,
         value.source_key, status, 1, int(locked), _json(value.to_dict()), now, now),
    )


def _target(row: sqlite3.Row) -> CardTarget:
    return CardTarget(str(row["target_id"]), str(row["name"]), tuple(json.loads(row["aliases_json"])),
                      tuple(json.loads(row["roles_json"])), bool(row["enabled"]), int(row["revision"]),
                      _date(row["created_at"]), _date(row["updated_at"]),
                      row["card_id"] if "card_id" in row.keys() else None)


def _job(row: sqlite3.Row) -> CardAnalysisJob:
    return CardAnalysisJob(str(row["job_id"]), str(row["recording_id"]), int(row["generation"]), str(row["state"]),
                           CardInputFingerprint.from_dict(json.loads(row["fingerprint_json"])),
                           tuple(CardTargetSnapshot.from_dict(value) for value in json.loads(row["target_snapshot_json"])),
                           str(row["analyzer_version"]), float(row["sample_fps"]), float(row["progress"]), str(row["error"]),
                           json.loads(row["result_metadata_json"]), _date(row["created_at"]), _date(row["updated_at"]))


def _row_values(row: sqlite3.Row) -> CardObservationValues:
    return CardObservationValues(row["target_id"], str(row["card_name"]), str(row["actor"]), str(row["action"]),
                                 int(row["observed_ms"]), row["actual_ms"], str(row["time_kind"]),
                                 json.loads(row["evidence_json"]), str(row["source_key"]), row["confidence"])


def _observation(row: sqlite3.Row) -> CardObservation:
    return CardObservation(str(row["event_id"]), str(row["recording_id"]), row["job_id"], int(row["generation"]),
                           _row_values(row), str(row["status"]), int(row["revision"]), bool(row["manual_locked"]),
                           bool(row["is_opener"]), _date(row["created_at"]), _date(row["updated_at"]),
                           row["information_id"], int(row["information_revision"]), row["card_id"],
                           row["video_generation"], bool(row["video_verified"]))
