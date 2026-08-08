from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

from .runtime_paths import RuntimePaths


STATE_SCHEMA_VERSION = 1
STATE_FILE_NAME = "recording-state.json"
STATE_VALUES = {"starting", "recording", "completed", "failed"}


class RecordingStateStoreError(RuntimeError):
    """録画状態を原子的に保存または検証できない場合のエラーです。"""


@dataclass(frozen=True)
class PersistedRecordingState:
    recording_id: str
    pid: int
    state: str
    source: str
    output_path: Path
    started_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class LoadedRecordingState:
    value: PersistedRecordingState
    source_path: Path
    used_previous: bool


class RecordingStateStore:
    def __init__(self, paths: RuntimePaths) -> None:
        self.data_root = paths.data.expanduser().resolve()
        self.recordings_root = paths.recordings.expanduser().resolve()
        self.state_path = self.data_root / STATE_FILE_NAME
        self.previous_path = self.data_root / f"{STATE_FILE_NAME}.previous"

    def save(
        self,
        *,
        recording_id: str,
        state: str,
        source: str,
        output_path: Path,
        started_at: datetime | None,
        pid: int | None = None,
        updated_at: datetime | None = None,
    ) -> PersistedRecordingState:
        relative_path = self._relative_output_path(output_path)
        value = PersistedRecordingState(
            recording_id=_required_text(recording_id, "recording_id"),
            pid=_positive_pid(os.getpid() if pid is None else pid),
            state=_state_value(state),
            source=_required_text(source, "source"),
            output_path=relative_path,
            started_at=_optional_utc(started_at, "started_at"),
            updated_at=_utc(updated_at or datetime.now(timezone.utc), "updated_at"),
        )
        document = _serialize(value)
        self.data_root.mkdir(parents=True, exist_ok=True)
        try:
            existing = self._load_path(self.state_path)
            if existing is not None:
                self._atomic_replace(self.previous_path, _serialize(existing))
            self._atomic_replace(self.state_path, document)
            _sync_directory(self.data_root)
        except (OSError, TypeError, ValueError, RecordingStateStoreError) as exc:
            raise RecordingStateStoreError(f"録画状態を保存できません: {self.state_path}: {exc}") from exc
        return value

    def load(self) -> LoadedRecordingState | None:
        errors: list[str] = []
        for path, previous in ((self.state_path, False), (self.previous_path, True)):
            if not path.exists():
                continue
            try:
                value = self._load_path(path)
                if value is not None:
                    return LoadedRecordingState(value, path, previous)
            except (OSError, TypeError, ValueError, RecordingStateStoreError) as exc:
                errors.append(f"{path.name}: {exc}")
        if errors:
            raise RecordingStateStoreError("有効な録画状態を読み込めません: " + "; ".join(errors))
        return None

    def absolute_output_path(self, value: PersistedRecordingState) -> Path:
        try:
            resolved = (self.recordings_root / value.output_path).resolve()
            resolved.relative_to(self.recordings_root)
            return resolved
        except (OSError, ValueError) as exc:
            raise RecordingStateStoreError("録画状態の出力パスが保存先外部を指しています") from exc

    def _relative_output_path(self, output_path: Path) -> Path:
        try:
            resolved = output_path.expanduser().resolve()
            relative = resolved.relative_to(self.recordings_root)
        except (OSError, ValueError) as exc:
            raise RecordingStateStoreError(
                f"録画状態の出力パスはrecordings配下である必要があります: {output_path}"
            ) from exc
        if not relative.parts:
            raise RecordingStateStoreError("録画状態の出力パスが空です")
        return relative

    def _load_path(self, path: Path) -> PersistedRecordingState | None:
        if not path.exists():
            return None
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise RecordingStateStoreError("状態ファイルが1MiBを超えています")
        document = json.loads(raw.decode("utf-8"))
        if not isinstance(document, dict):
            raise RecordingStateStoreError("状態ファイルのルートはJSON objectである必要があります")
        checksum = document.pop("checksum", None)
        expected = _checksum(document)
        if not isinstance(checksum, str) or checksum != expected:
            raise RecordingStateStoreError("状態ファイルのチェックサムが一致しません")
        version = document.get("schema_version")
        if version != STATE_SCHEMA_VERSION:
            raise RecordingStateStoreError(f"未対応の状態スキーマ版です: {version}")
        relative_path = Path(_required_text(document.get("output_path"), "output_path"))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RecordingStateStoreError("output_pathは安全な相対パスである必要があります")
        started_raw = document.get("started_at")
        return PersistedRecordingState(
            recording_id=_required_text(document.get("recording_id"), "recording_id"),
            pid=_positive_pid(document.get("pid")),
            state=_state_value(document.get("state")),
            source=_required_text(document.get("source"), "source"),
            output_path=relative_path,
            started_at=_parse_datetime(started_raw) if started_raw is not None else None,
            updated_at=_parse_datetime(_required_text(document.get("updated_at"), "updated_at")),
        )

    def _atomic_replace(self, destination: Path, data: bytes) -> None:
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)


def _serialize(value: PersistedRecordingState) -> bytes:
    document: dict[str, object] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "recording_id": value.recording_id,
        "pid": value.pid,
        "state": value.state,
        "source": value.source,
        "output_path": value.output_path.as_posix(),
        "started_at": _format_datetime(value.started_at) if value.started_at else None,
        "updated_at": _format_datetime(value.updated_at),
    }
    document["checksum"] = _checksum(document)
    return (json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _checksum(document: dict[str, object]) -> str:
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _required_text(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecordingStateStoreError(f"{key} は空でない文字列である必要があります")
    return value.strip()


def _positive_pid(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RecordingStateStoreError("pid は正の整数である必要があります")
    return value


def _state_value(value: object) -> str:
    if not isinstance(value, str) or value not in STATE_VALUES:
        raise RecordingStateStoreError(f"未対応の録画状態です: {value}")
    return value


def _optional_utc(value: datetime | None, key: str) -> datetime | None:
    return _utc(value, key) if value is not None else None


def _utc(value: datetime, key: str) -> datetime:
    if value.tzinfo is None:
        raise RecordingStateStoreError(f"{key} にはタイムゾーンが必要です")
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise RecordingStateStoreError("状態ファイルの時刻にはタイムゾーンが必要です")
    return parsed.astimezone(timezone.utc)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
