from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import uuid

from .runtime_paths import RuntimePaths
from .upload_metadata import UploadMetadata, UploadMetadataError


UPLOAD_QUEUE_SCHEMA_VERSION = 1
UPLOAD_QUEUE_FILE_NAME = "upload-preparation.json"


class UploadQueueError(RuntimeError):
    """アップロード準備キューを安全に保存または遷移できない場合のエラーです。"""


class UploadQueueState(str, Enum):
    WAITING = "waiting"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class UploadQueueItem:
    queue_id: str
    recording_id: str
    metadata: UploadMetadata
    state: UploadQueueState
    attempts: int
    created_at: datetime
    updated_at: datetime
    export_path: Path | None = None
    manifest_path: Path | None = None
    validation: dict[str, object] | None = None
    error: str | None = None


ALLOWED_TRANSITIONS = {
    UploadQueueState.WAITING: {UploadQueueState.PROCESSING, UploadQueueState.CANCELLED},
    UploadQueueState.PROCESSING: {
        UploadQueueState.COMPLETED,
        UploadQueueState.FAILED,
        UploadQueueState.CANCELLED,
    },
    UploadQueueState.FAILED: {UploadQueueState.WAITING, UploadQueueState.CANCELLED},
    UploadQueueState.COMPLETED: set(),
    UploadQueueState.CANCELLED: set(),
}


class UploadQueueStore:
    def __init__(self, paths: RuntimePaths) -> None:
        self.queue_root = paths.queue.expanduser().resolve()
        self.path = self.queue_root / UPLOAD_QUEUE_FILE_NAME
        self.previous_path = self.queue_root / f"{UPLOAD_QUEUE_FILE_NAME}.previous"

    def list(self) -> tuple[UploadQueueItem, ...]:
        return tuple(sorted(self._load_items(), key=lambda item: (item.created_at, item.queue_id)))

    def get(self, queue_id: str) -> UploadQueueItem | None:
        identifier = _required_text(queue_id, "queue_id")
        return next((item for item in self._load_items() if item.queue_id == identifier), None)

    def enqueue(self, *, recording_id: str, metadata: UploadMetadata) -> UploadQueueItem:
        identifier = _required_text(recording_id, "recording_id")
        items = list(self._load_items())
        duplicate_states = {
            UploadQueueState.WAITING,
            UploadQueueState.PROCESSING,
            UploadQueueState.COMPLETED,
        }
        if any(item.recording_id == identifier and item.state in duplicate_states for item in items):
            raise UploadQueueError(f"同じ録画は既にアップロード準備キューへ登録されています: {identifier}")
        now = datetime.now(timezone.utc)
        item = UploadQueueItem(
            queue_id=uuid.uuid4().hex,
            recording_id=identifier,
            metadata=metadata,
            state=UploadQueueState.WAITING,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        items.append(item)
        self._save_items(items)
        return item

    def transition(
        self,
        queue_id: str,
        state: UploadQueueState,
        *,
        export_path: Path | None = None,
        manifest_path: Path | None = None,
        validation: dict[str, object] | None = None,
        error: str | None = None,
        increment_attempts: bool = False,
    ) -> UploadQueueItem:
        identifier = _required_text(queue_id, "queue_id")
        items = list(self._load_items())
        index = next((index for index, item in enumerate(items) if item.queue_id == identifier), None)
        if index is None:
            raise UploadQueueError(f"キュー項目が見つかりません: {identifier}")
        current = items[index]
        if state not in ALLOWED_TRANSITIONS[current.state]:
            raise UploadQueueError(
                f"キュー状態を遷移できません: {current.state.value} -> {state.value}"
            )
        updated = replace(
            current,
            state=state,
            attempts=current.attempts + (1 if increment_attempts else 0),
            updated_at=datetime.now(timezone.utc),
            export_path=export_path if export_path is not None else current.export_path,
            manifest_path=manifest_path if manifest_path is not None else current.manifest_path,
            validation=validation if validation is not None else current.validation,
            error=error,
        )
        items[index] = updated
        self._save_items(items)
        return updated

    def restore_interrupted(self) -> tuple[UploadQueueItem, ...]:
        items = list(self._load_items())
        restored: list[UploadQueueItem] = []
        for index, item in enumerate(items):
            if item.state is not UploadQueueState.PROCESSING:
                continue
            updated = replace(
                item,
                state=UploadQueueState.WAITING,
                updated_at=datetime.now(timezone.utc),
                error="前回のアップロード準備処理が中断されたため待機状態へ戻しました",
            )
            items[index] = updated
            restored.append(updated)
        if restored:
            self._save_items(items)
        return tuple(restored)

    def _load_items(self) -> tuple[UploadQueueItem, ...]:
        errors: list[str] = []
        for path in (self.path, self.previous_path):
            if not path.exists():
                continue
            try:
                return _parse_document(path.read_bytes())
            except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError, UploadQueueError) as exc:
                errors.append(f"{path.name}: {exc}")
        if errors:
            raise UploadQueueError("有効なアップロード準備キューを読めません: " + "; ".join(errors))
        return ()

    def _save_items(self, items: list[UploadQueueItem]) -> None:
        self.queue_root.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": UPLOAD_QUEUE_SCHEMA_VERSION,
            "items": [_item_to_dict(item) for item in items],
        }
        data = _encode_document(document)
        try:
            current_items: tuple[UploadQueueItem, ...] | None = None
            if self.path.exists():
                try:
                    current_items = _parse_document(self.path.read_bytes())
                except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError, UploadQueueError):
                    if self.previous_path.exists():
                        current_items = _parse_document(self.previous_path.read_bytes())
            if current_items is not None:
                previous = {
                    "schema_version": UPLOAD_QUEUE_SCHEMA_VERSION,
                    "items": [_item_to_dict(item) for item in current_items],
                }
                _atomic_replace(self.previous_path, _encode_document(previous))
            _atomic_replace(self.path, data)
        except (OSError, TypeError, ValueError, UploadQueueError) as exc:
            raise UploadQueueError(f"アップロード準備キューを保存できません: {exc}") from exc


def _item_to_dict(item: UploadQueueItem) -> dict[str, object]:
    return {
        "queue_id": item.queue_id,
        "recording_id": item.recording_id,
        "metadata": item.metadata.to_dict(),
        "state": item.state.value,
        "attempts": item.attempts,
        "created_at": item.created_at.isoformat(timespec="microseconds"),
        "updated_at": item.updated_at.isoformat(timespec="microseconds"),
        "export_path": item.export_path.as_posix() if item.export_path else None,
        "manifest_path": item.manifest_path.as_posix() if item.manifest_path else None,
        "validation": item.validation,
        "error": item.error,
    }


def _parse_document(data: bytes) -> tuple[UploadQueueItem, ...]:
    if len(data) > 10 * 1024 * 1024:
        raise UploadQueueError("キューファイルが10MiBを超えています")
    document = json.loads(data.decode("utf-8"))
    if not isinstance(document, dict):
        raise UploadQueueError("キューのルートはobjectである必要があります")
    checksum = document.pop("checksum", None)
    if not isinstance(checksum, str) or checksum != _checksum(document):
        raise UploadQueueError("キューのチェックサムが一致しません")
    if set(document) != {"schema_version", "items"}:
        raise UploadQueueError("キューの項目がスキーマと一致しません")
    if document.get("schema_version") != UPLOAD_QUEUE_SCHEMA_VERSION:
        raise UploadQueueError(f"未対応のキュースキーマ版です: {document.get('schema_version')}")
    raw_items = document.get("items")
    if not isinstance(raw_items, list):
        raise UploadQueueError("items は配列である必要があります")
    items: list[UploadQueueItem] = []
    identifiers: set[str] = set()
    for value in raw_items:
        if not isinstance(value, dict):
            raise UploadQueueError("queue item はobjectである必要があります")
        expected_item_fields = {
            "queue_id",
            "recording_id",
            "metadata",
            "state",
            "attempts",
            "created_at",
            "updated_at",
            "export_path",
            "manifest_path",
            "validation",
            "error",
        }
        if set(value) != expected_item_fields:
            raise UploadQueueError("queue itemの項目がスキーマと一致しません")
        queue_id = _required_text(value.get("queue_id"), "queue_id")
        if queue_id in identifiers:
            raise UploadQueueError(f"queue_id が重複しています: {queue_id}")
        identifiers.add(queue_id)
        try:
            state = UploadQueueState(value.get("state"))
            metadata = UploadMetadata.from_dict(value.get("metadata"))
            attempts = value.get("attempts")
            if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
                raise UploadQueueError("attempts は0以上の整数である必要があります")
            validation = value.get("validation")
            if validation is not None and not isinstance(validation, dict):
                raise UploadQueueError("validation はobjectまたはnullである必要があります")
            items.append(
                UploadQueueItem(
                    queue_id=queue_id,
                    recording_id=_required_text(value.get("recording_id"), "recording_id"),
                    metadata=metadata,
                    state=state,
                    attempts=attempts,
                    created_at=_parse_datetime(value.get("created_at"), "created_at"),
                    updated_at=_parse_datetime(value.get("updated_at"), "updated_at"),
                    export_path=_optional_relative_path(value.get("export_path"), "export_path"),
                    manifest_path=_optional_relative_path(value.get("manifest_path"), "manifest_path"),
                    validation=dict(validation) if validation is not None else None,
                    error=_optional_text(value.get("error"), "error"),
                )
            )
        except (ValueError, UploadMetadataError) as exc:
            raise UploadQueueError(f"キュー項目の形式が不正です: {queue_id}: {exc}") from exc
    return tuple(items)


def _encode_document(document: dict[str, object]) -> bytes:
    copied = dict(document)
    copied["checksum"] = _checksum(document)
    return (json.dumps(copied, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _checksum(document: dict[str, object]) -> str:
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _atomic_replace(destination: Path, data: bytes) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def _required_text(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UploadQueueError(f"{key} は空でない文字列である必要があります")
    return value.strip()


def _optional_text(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise UploadQueueError(f"{key} は文字列またはnullである必要があります")
    return value


def _parse_datetime(value: object, key: str) -> datetime:
    text = _required_text(value, key)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise UploadQueueError(f"{key} にはタイムゾーンが必要です")
    return parsed.astimezone(timezone.utc)


def _optional_relative_path(value: object, key: str) -> Path | None:
    if value is None:
        return None
    path = Path(_required_text(value, key))
    if path.is_absolute() or ".." in path.parts:
        raise UploadQueueError(f"{key} は安全な相対パスである必要があります")
    return path
