from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Iterator
import uuid

from .history_database import (
    HISTORY_DATABASE_NAME,
    HistoryDatabaseError,
    connect_history_database,
    initialize_history_database,
)
from .recording_session import RecordingResult, RecordingState
from .recording_failure import FailureClassification, RecoveryPolicy, classify_recording_failure
from .runtime_paths import RuntimePaths


HISTORY_STATES = {"starting", "recording", "completed", "failed"}
RECOVERY_STATES = {
    "not_required",
    "pending",
    "inspecting",
    "repairable",
    "repaired",
    "ignored",
    "unrecoverable",
}


class RecordingHistoryError(RuntimeError):
    """録画履歴を安全に読み書きできない場合のエラーです。"""


@dataclass(frozen=True)
class RecordingHistoryEntry:
    recording_id: str
    state: str
    source: str
    detection_reason: str | None
    output_path: Path
    container: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    size_bytes: int | None
    returncode: int | None
    error: str | None
    diagnostics: tuple[str, ...]
    failure_code: str | None
    recovery_policy: str | None
    recovery_state: str
    recovery_attempts: int
    recovery_message: str | None
    recovery_diagnostic: str | None
    audio_input: str | None
    audio_state: str
    audio_warning: str | None
    updated_at: datetime


@dataclass(frozen=True)
class RecoveryArtifact:
    artifact_id: str
    recording_id: str
    kind: str
    status: str
    output_path: Path
    size_bytes: int | None
    diagnostic: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class HistoryDeletionResult:
    recording_id: str
    deleted_files: tuple[Path, ...]
    missing_files: tuple[Path, ...]


@dataclass(frozen=True)
class HistoryQuery:
    state: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        if self.state is not None and self.state not in HISTORY_STATES:
            raise ValueError(f"未対応の録画状態です: {self.state}")
        _optional_aware_datetime(self.since, "since")
        _optional_aware_datetime(self.until, "until")
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("since は until 以前である必要があります")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 1000:
            raise ValueError("limit は1から1000の整数である必要があります")
        if isinstance(self.offset, bool) or not isinstance(self.offset, int) or self.offset < 0:
            raise ValueError("offset は0以上の整数である必要があります")


class ConsistencyIssueKind(str, Enum):
    MISSING = "missing"
    UNTRACKED = "untracked"
    SIZE_MISMATCH = "size_mismatch"
    INVALID_REFERENCE = "invalid_reference"


@dataclass(frozen=True)
class ConsistencyIssue:
    kind: ConsistencyIssueKind
    path: Path
    message: str
    recording_id: str | None = None


class RecordingHistoryRepository:
    def __init__(
        self,
        *,
        database_path: Path,
        recordings_root: Path,
    ) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.recordings_root = recordings_root.expanduser().resolve()
        try:
            initialize_history_database(self.database_path)
        except (OSError, HistoryDatabaseError) as exc:
            raise RecordingHistoryError(str(exc)) from exc

    @classmethod
    def from_runtime_paths(cls, paths: RuntimePaths) -> RecordingHistoryRepository:
        return cls(
            database_path=paths.db / HISTORY_DATABASE_NAME,
            recordings_root=paths.recordings,
        )

    def register_starting(
        self,
        *,
        recording_id: str,
        output_path: Path,
        container: str,
        source: str,
        detection_reason: str | None = None,
        audio_input: str | None = None,
        created_at: datetime | None = None,
    ) -> RecordingHistoryEntry:
        identifier = _required_text(recording_id, "recording_id")
        normalized_source = _required_text(source, "source")
        normalized_container = container.strip().lower()
        if normalized_container not in {"mkv", "mp4"}:
            raise RecordingHistoryError("container はmkvまたはmp4である必要があります")
        relative_path = self._relative_output_path(output_path)
        timestamp = _utc(created_at or datetime.now(timezone.utc), "created_at")
        reason = detection_reason.strip() if detection_reason and detection_reason.strip() else None
        normalized_audio = audio_input.strip() if audio_input and audio_input.strip() else None
        audio_state = "configured" if normalized_audio else "disabled"
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO recordings (
                        recording_id, state, source, detection_reason, output_path,
                        container, created_at, diagnostics_json, audio_input,
                        audio_state, updated_at
                    ) VALUES (?, 'starting', ?, ?, ?, ?, ?, '[]', ?, ?, ?)
                    """,
                    (
                        identifier,
                        normalized_source,
                        reason,
                        relative_path.as_posix(),
                        normalized_container,
                        _format_datetime(timestamp),
                        normalized_audio,
                        audio_state,
                        _format_datetime(timestamp),
                    ),
                )
            entry = self.get(identifier)
            assert entry is not None
            return entry
        except sqlite3.IntegrityError as exc:
            raise RecordingHistoryError(f"録画履歴を新規登録できません: {identifier}: {exc}") from exc

    def mark_recording(self, recording_id: str, *, started_at: datetime) -> RecordingHistoryEntry:
        identifier = _required_text(recording_id, "recording_id")
        timestamp = _utc(started_at, "started_at")
        formatted = _format_datetime(timestamp)
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE recordings
                SET state = 'recording', started_at = ?, updated_at = ?
                WHERE recording_id = ? AND state = 'starting'
                """,
                (formatted, formatted, identifier),
            )
        if cursor.rowcount == 0:
            existing = self.get(identifier)
            if existing is None:
                raise RecordingHistoryError(f"録画履歴が見つかりません: {identifier}")
            if existing.state != "recording" or existing.started_at != timestamp:
                raise RecordingHistoryError(
                    f"録画履歴をrecordingへ遷移できません: {identifier}: {existing.state}"
                )
            return existing
        entry = self.get(identifier)
        assert entry is not None
        return entry

    def finalize(self, recording_id: str, result: RecordingResult) -> RecordingHistoryEntry:
        identifier = _required_text(recording_id, "recording_id")
        if result.state not in {RecordingState.COMPLETED, RecordingState.FAILED}:
            raise RecordingHistoryError("最終状態ではない録画結果は履歴へ確定できません")
        existing_before = self.get(identifier)
        if existing_before is None:
            raise RecordingHistoryError(f"録画履歴が見つかりません: {identifier}")
        result_path = self._relative_output_path(result.output_path)
        if result_path != existing_before.output_path:
            raise RecordingHistoryError(
                f"録画結果のファイルが開始履歴と一致しません: {identifier}"
            )
        state = result.state.value
        started_at = _utc(result.started_at, "started_at") if result.started_at else None
        ended_at = _utc(result.ended_at, "ended_at") if result.ended_at else datetime.now(timezone.utc)
        duration = None
        if started_at is not None:
            duration = max(0.0, (ended_at - started_at).total_seconds())
        diagnostics_json = json.dumps(list(result.diagnostics), ensure_ascii=False)
        classification = None
        recovery_state = "not_required"
        if result.state is RecordingState.FAILED:
            classification = classify_recording_failure(
                error=result.error,
                returncode=result.returncode,
                output_exists=result.output_path.is_file(),
                output_size=result.size_bytes,
            )
            recovery_state = (
                "unrecoverable"
                if classification.policy is RecoveryPolicy.UNRECOVERABLE
                else "pending"
            )
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE recordings
                SET state = ?, started_at = COALESCE(started_at, ?), ended_at = ?,
                    duration_seconds = ?, size_bytes = ?, returncode = ?, error = ?,
                    diagnostics_json = ?, failure_code = ?, recovery_policy = ?,
                    recovery_state = ?, recovery_message = ?, recovery_diagnostic = ?,
                    audio_state = CASE
                        WHEN audio_state = 'configured' AND ? = 'completed' THEN 'recorded'
                        WHEN audio_state = 'configured' AND ? = 'failed' THEN 'failed'
                        ELSE audio_state
                    END,
                    updated_at = ?
                WHERE recording_id = ? AND state IN ('starting', 'recording')
                """,
                (
                    state,
                    _format_datetime(started_at) if started_at else None,
                    _format_datetime(ended_at),
                    duration,
                    result.size_bytes,
                    result.returncode,
                    result.error,
                    diagnostics_json,
                    classification.code.value if classification else None,
                    classification.policy.value if classification else None,
                    recovery_state,
                    classification.user_message if classification else None,
                    classification.internal_diagnostic if classification else None,
                    state,
                    state,
                    _format_datetime(ended_at),
                    identifier,
                ),
            )
        if cursor.rowcount == 0:
            existing = self.get(identifier)
            if existing is None:
                raise RecordingHistoryError(f"録画履歴が見つかりません: {identifier}")
            if existing.state != state:
                raise RecordingHistoryError(
                    f"確定済み録画履歴の状態を変更できません: {identifier}: {existing.state}"
                )
            return existing
        entry = self.get(identifier)
        assert entry is not None
        return entry

    def mark_interrupted(
        self,
        recording_id: str,
        *,
        classification: FailureClassification,
        ended_at: datetime,
        size_bytes: int,
    ) -> RecordingHistoryEntry:
        identifier = _required_text(recording_id, "recording_id")
        timestamp = _utc(ended_at, "ended_at")
        existing = self.get(identifier)
        if existing is None:
            raise RecordingHistoryError(f"録画履歴が見つかりません: {identifier}")
        if existing.state == "failed" and existing.failure_code == classification.code.value:
            return existing
        if existing.state not in {"starting", "recording"}:
            raise RecordingHistoryError(
                f"中断状態へ更新できない録画履歴です: {identifier}: {existing.state}"
            )
        duration = None
        if existing.started_at is not None:
            duration = max(0.0, (timestamp - existing.started_at).total_seconds())
        recovery_state = (
            "unrecoverable"
            if classification.policy is RecoveryPolicy.UNRECOVERABLE
            else "pending"
        )
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE recordings
                SET state = 'failed', ended_at = ?, duration_seconds = ?, size_bytes = ?,
                    error = ?, failure_code = ?, recovery_policy = ?, recovery_state = ?,
                    recovery_message = ?, recovery_diagnostic = ?, updated_at = ?
                    , audio_state = CASE
                        WHEN audio_state = 'configured' THEN 'failed' ELSE audio_state END
                WHERE recording_id = ? AND state IN ('starting', 'recording')
                """,
                (
                    _format_datetime(timestamp),
                    duration,
                    size_bytes,
                    classification.user_message,
                    classification.code.value,
                    classification.policy.value,
                    recovery_state,
                    classification.user_message,
                    classification.internal_diagnostic,
                    _format_datetime(timestamp),
                    identifier,
                ),
            )
        entry = self.get(identifier)
        assert entry is not None
        return entry

    def recovery_entries(self) -> tuple[RecordingHistoryEntry, ...]:
        with self._connection(write=False) as connection:
            rows = connection.execute(
                """
                SELECT * FROM recordings
                WHERE recovery_state NOT IN ('not_required', 'repaired', 'ignored')
                ORDER BY updated_at DESC, recording_id DESC
                """
            ).fetchall()
        return tuple(self._entry_from_row(row) for row in rows)

    def set_recovery_state(
        self,
        recording_id: str,
        *,
        state: str,
        message: str,
        diagnostic: str,
        increment_attempts: bool = False,
    ) -> RecordingHistoryEntry:
        identifier = _required_text(recording_id, "recording_id")
        if state not in RECOVERY_STATES - {"not_required"}:
            raise RecordingHistoryError(f"未対応の復旧状態です: {state}")
        timestamp = datetime.now(timezone.utc)
        increment = 1 if increment_attempts else 0
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE recordings
                SET recovery_state = ?, recovery_message = ?, recovery_diagnostic = ?,
                    recovery_attempts = recovery_attempts + ?, updated_at = ?
                WHERE recording_id = ? AND state = 'failed'
                """,
                (
                    state,
                    _required_text(message, "message"),
                    _required_text(diagnostic, "diagnostic"),
                    increment,
                    _format_datetime(timestamp),
                    identifier,
                ),
            )
        if cursor.rowcount == 0:
            raise RecordingHistoryError(f"復旧対象の失敗履歴が見つかりません: {identifier}")
        entry = self.get(identifier)
        assert entry is not None
        return entry

    def add_recovery_artifact(
        self,
        *,
        artifact_id: str,
        recording_id: str,
        output_path: Path,
        kind: str,
        status: str,
        size_bytes: int | None,
        diagnostic: str | None,
    ) -> RecoveryArtifact:
        if kind not in {"recovered", "partial"}:
            raise RecordingHistoryError(f"未対応の復旧成果物種別です: {kind}")
        if status not in {"created", "valid", "failed"}:
            raise RecordingHistoryError(f"未対応の復旧成果物状態です: {status}")
        relative_path = self._relative_output_path(output_path)
        timestamp = datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO recovery_artifacts (
                    artifact_id, recording_id, kind, status, output_path,
                    size_bytes, diagnostic, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _required_text(artifact_id, "artifact_id"),
                    _required_text(recording_id, "recording_id"),
                    kind,
                    status,
                    relative_path.as_posix(),
                    size_bytes,
                    diagnostic,
                    _format_datetime(timestamp),
                    _format_datetime(timestamp),
                ),
            )
        artifacts = self.recovery_artifacts(recording_id)
        return next(item for item in artifacts if item.artifact_id == artifact_id)

    def recovery_artifacts(self, recording_id: str) -> tuple[RecoveryArtifact, ...]:
        identifier = _required_text(recording_id, "recording_id")
        with self._connection(write=False) as connection:
            rows = connection.execute(
                """
                SELECT * FROM recovery_artifacts
                WHERE recording_id = ? ORDER BY created_at DESC, artifact_id DESC
                """,
                (identifier,),
            ).fetchall()
        return tuple(
            RecoveryArtifact(
                artifact_id=row["artifact_id"],
                recording_id=row["recording_id"],
                kind=row["kind"],
                status=row["status"],
                output_path=Path(row["output_path"]),
                size_bytes=row["size_bytes"],
                diagnostic=row["diagnostic"],
                created_at=_parse_datetime(row["created_at"]),
                updated_at=_parse_datetime(row["updated_at"]),
            )
            for row in rows
        )

    def get(self, recording_id: str) -> RecordingHistoryEntry | None:
        identifier = _required_text(recording_id, "recording_id")
        with self._connection(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM recordings WHERE recording_id = ?",
                (identifier,),
            ).fetchone()
        return self._entry_from_row(row) if row is not None else None

    def query(self, query: HistoryQuery | None = None) -> tuple[RecordingHistoryEntry, ...]:
        selected = query or HistoryQuery()
        clauses: list[str] = []
        parameters: list[object] = []
        if selected.state is not None:
            clauses.append("state = ?")
            parameters.append(selected.state)
        if selected.since is not None:
            clauses.append("COALESCE(started_at, created_at) >= ?")
            parameters.append(_format_datetime(_utc(selected.since, "since")))
        if selected.until is not None:
            clauses.append("COALESCE(started_at, created_at) <= ?")
            parameters.append(_format_datetime(_utc(selected.until, "until")))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((selected.limit, selected.offset))
        sql = (
            "SELECT * FROM recordings"
            f"{where} ORDER BY COALESCE(started_at, created_at) DESC, recording_id DESC "
            "LIMIT ? OFFSET ?"
        )
        with self._connection(write=False) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return tuple(self._entry_from_row(row) for row in rows)

    def delete(self, recording_id: str) -> HistoryDeletionResult:
        identifier = _required_text(recording_id, "recording_id")
        entry = self.get(identifier)
        if entry is None:
            raise RecordingHistoryError(f"録画履歴が見つかりません: {identifier}")
        artifacts = self.recovery_artifacts(identifier)
        relative_paths = (entry.output_path, *(artifact.output_path for artifact in artifacts))
        paths: list[Path] = []
        for relative_path in relative_paths:
            path = self._resolved_recording_path(relative_path)
            if path not in paths:
                paths.append(path)

        staging_root = self.recordings_root / ".delete-staging" / uuid.uuid4().hex
        moved: list[tuple[Path, Path]] = []
        missing: list[Path] = []
        try:
            for index, path in enumerate(paths):
                if not path.exists():
                    missing.append(path)
                    continue
                if not path.is_file():
                    raise RecordingHistoryError(f"削除対象がファイルではありません: {path}")
                staging_root.mkdir(parents=True, exist_ok=True)
                staged = staging_root / f"{index:04d}-{path.name}"
                path.replace(staged)
                moved.append((path, staged))

            with self._connection() as connection:
                connection.execute(
                    "DELETE FROM duel_record_tags WHERE recording_id = ?", (identifier,)
                )
                connection.execute(
                    "DELETE FROM duel_record_tag_links WHERE recording_id = ?", (identifier,)
                )
                connection.execute(
                    "DELETE FROM duel_record_changes WHERE recording_id = ?", (identifier,)
                )
                connection.execute("DELETE FROM duel_events WHERE recording_id = ?", (identifier,))
                connection.execute("DELETE FROM duel_records WHERE recording_id = ?", (identifier,))
                connection.execute(
                    "DELETE FROM recovery_artifacts WHERE recording_id = ?", (identifier,)
                )
                cursor = connection.execute(
                    "DELETE FROM recordings WHERE recording_id = ?", (identifier,)
                )
                if cursor.rowcount != 1:
                    raise RecordingHistoryError(f"録画履歴が見つかりません: {identifier}")
        except (OSError, RecordingHistoryError) as exc:
            restore_errors: list[str] = []
            for original, staged in reversed(moved):
                try:
                    original.parent.mkdir(parents=True, exist_ok=True)
                    staged.replace(original)
                except OSError as restore_exc:
                    restore_errors.append(f"{original}: {restore_exc}")
            self._remove_empty_staging(staging_root)
            detail = f"録画履歴を削除できません: {identifier}: {exc}"
            if restore_errors:
                detail += f" / ファイル復元失敗: {'; '.join(restore_errors)}"
            raise RecordingHistoryError(detail) from exc

        cleanup_errors: list[str] = []
        for _original, staged in moved:
            try:
                staged.unlink(missing_ok=True)
            except OSError as exc:
                cleanup_errors.append(f"{staged}: {exc}")
        self._remove_empty_staging(staging_root)
        if cleanup_errors:
            raise RecordingHistoryError(
                "履歴は削除しましたが、退避した録画ファイルを削除できません: "
                + "; ".join(cleanup_errors)
            )
        return HistoryDeletionResult(
            recording_id=identifier,
            deleted_files=tuple(original for original, _staged in moved),
            missing_files=tuple(missing),
        )

    def check_consistency(self) -> tuple[ConsistencyIssue, ...]:
        with self._connection(write=False) as connection:
            rows = connection.execute("SELECT * FROM recordings ORDER BY recording_id").fetchall()
            artifact_rows = connection.execute(
                "SELECT * FROM recovery_artifacts ORDER BY artifact_id"
            ).fetchall()
        entries = tuple(self._entry_from_row(row) for row in rows)
        issues: list[ConsistencyIssue] = []
        registered: set[Path] = set()
        for entry in entries:
            try:
                path = (self.recordings_root / entry.output_path).resolve()
                path.relative_to(self.recordings_root)
            except (OSError, ValueError):
                issues.append(
                    ConsistencyIssue(
                        ConsistencyIssueKind.INVALID_REFERENCE,
                        entry.output_path,
                        "履歴のファイル参照が録画保存先の外部を指しています",
                        entry.recording_id,
                    )
                )
                continue
            registered.add(path)
            if not path.is_file():
                issues.append(
                    ConsistencyIssue(
                        ConsistencyIssueKind.MISSING,
                        path,
                        "履歴に対応する録画ファイルがありません",
                        entry.recording_id,
                    )
                )
                continue
            actual_size = path.stat().st_size
            if entry.size_bytes is not None and entry.size_bytes != actual_size:
                issues.append(
                    ConsistencyIssue(
                        ConsistencyIssueKind.SIZE_MISMATCH,
                        path,
                        f"履歴サイズ{entry.size_bytes} bytes、実ファイル{actual_size} bytesです",
                        entry.recording_id,
                    )
                )

        for row in artifact_rows:
            relative = Path(row["output_path"])
            try:
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("unsafe recovery artifact path")
                path = (self.recordings_root / relative).resolve()
                path.relative_to(self.recordings_root)
            except (OSError, ValueError):
                issues.append(
                    ConsistencyIssue(
                        ConsistencyIssueKind.INVALID_REFERENCE,
                        relative,
                        "復旧成果物の参照が録画保存先の外部を指しています",
                        row["recording_id"],
                    )
                )
                continue
            registered.add(path)
            if not path.is_file():
                issues.append(
                    ConsistencyIssue(
                        ConsistencyIssueKind.MISSING,
                        path,
                        "履歴に対応する復旧成果物がありません",
                        row["recording_id"],
                    )
                )
                continue
            actual_size = path.stat().st_size
            if row["size_bytes"] is not None and row["size_bytes"] != actual_size:
                issues.append(
                    ConsistencyIssue(
                        ConsistencyIssueKind.SIZE_MISMATCH,
                        path,
                        f"復旧履歴サイズ{row['size_bytes']} bytes、実ファイル{actual_size} bytesです",
                        row["recording_id"],
                    )
                )

        if self.recordings_root.is_dir():
            for path in sorted(self.recordings_root.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in {".mkv", ".mp4"}:
                    continue
                resolved = path.resolve()
                if resolved not in registered:
                    issues.append(
                        ConsistencyIssue(
                            ConsistencyIssueKind.UNTRACKED,
                            resolved,
                            "録画ファイルが履歴へ登録されていません",
                        )
                    )
        return tuple(issues)

    def _relative_output_path(self, output_path: Path) -> Path:
        try:
            resolved = output_path.expanduser().resolve()
            relative = resolved.relative_to(self.recordings_root)
        except (OSError, ValueError) as exc:
            raise RecordingHistoryError(
                f"録画ファイルは録画保存先の配下である必要があります: {output_path}"
            ) from exc
        if not relative.parts:
            raise RecordingHistoryError("録画ファイルの相対パスが空です")
        return relative

    def _resolved_recording_path(self, relative_path: Path) -> Path:
        try:
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise ValueError("unsafe relative path")
            path = (self.recordings_root / relative_path).resolve()
            path.relative_to(self.recordings_root)
            return path
        except (OSError, ValueError) as exc:
            raise RecordingHistoryError(
                f"削除対象が録画保存先の外部を指しています: {relative_path}"
            ) from exc

    def _remove_empty_staging(self, staging_root: Path) -> None:
        for directory in (staging_root, staging_root.parent):
            try:
                directory.rmdir()
            except OSError:
                pass

    def _entry_from_row(self, row: sqlite3.Row) -> RecordingHistoryEntry:
        try:
            diagnostics_value = json.loads(row["diagnostics_json"])
            if not isinstance(diagnostics_value, list) or not all(
                isinstance(item, str) for item in diagnostics_value
            ):
                raise ValueError("diagnostics_json must be a string array")
            output_path = Path(row["output_path"])
            if output_path.is_absolute() or ".." in output_path.parts:
                raise ValueError("output_path must be relative")
            return RecordingHistoryEntry(
                recording_id=row["recording_id"],
                state=row["state"],
                source=row["source"],
                detection_reason=row["detection_reason"],
                output_path=output_path,
                container=row["container"],
                created_at=_parse_datetime(row["created_at"]),
                started_at=_parse_datetime(row["started_at"]) if row["started_at"] else None,
                ended_at=_parse_datetime(row["ended_at"]) if row["ended_at"] else None,
                duration_seconds=row["duration_seconds"],
                size_bytes=row["size_bytes"],
                returncode=row["returncode"],
                error=row["error"],
                diagnostics=tuple(diagnostics_value),
                failure_code=row["failure_code"],
                recovery_policy=row["recovery_policy"],
                recovery_state=row["recovery_state"],
                recovery_attempts=row["recovery_attempts"],
                recovery_message=row["recovery_message"],
                recovery_diagnostic=row["recovery_diagnostic"],
                audio_input=row["audio_input"],
                audio_state=row["audio_state"],
                audio_warning=row["audio_warning"],
                updated_at=_parse_datetime(row["updated_at"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RecordingHistoryError(f"録画履歴レコードの形式が不正です: {exc}") from exc

    @contextmanager
    def _connection(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        try:
            connection = connect_history_database(self.database_path)
        except HistoryDatabaseError as exc:
            raise RecordingHistoryError(str(exc)) from exc
        try:
            if write:
                with connection:
                    yield connection
            else:
                yield connection
        except sqlite3.Error as exc:
            raise RecordingHistoryError(f"録画履歴DBの操作に失敗しました: {exc}") from exc
        finally:
            connection.close()


def _required_text(value: str, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecordingHistoryError(f"{key} は空でない文字列である必要があります")
    return value.strip()


def _optional_aware_datetime(value: datetime | None, key: str) -> None:
    if value is not None and value.tzinfo is None:
        raise ValueError(f"{key} にはタイムゾーンが必要です")


def _utc(value: datetime, key: str) -> datetime:
    if value.tzinfo is None:
        raise RecordingHistoryError(f"{key} にはタイムゾーンが必要です")
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)
