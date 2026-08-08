from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .recording_history import RecordingHistoryEntry
from .runtime_paths import RuntimePaths
from .upload_export import UploadExportResult
from .upload_metadata import UploadMetadata
from .upload_queue import UploadQueueItem


UPLOAD_MANIFEST_SCHEMA_VERSION = 1


class UploadManifestError(RuntimeError):
    """アップロード準備マニフェストを安全に生成または検証できない場合のエラーです。"""


class UploadManifestWriter:
    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths
        self.manifest_root = (paths.queue / "manifests").resolve()

    def write(
        self,
        *,
        item: UploadQueueItem,
        history: RecordingHistoryEntry,
        export: UploadExportResult,
    ) -> Path:
        if not export.succeeded or export.output_path is None or export.output_validation is None:
            raise UploadManifestError("検証済みエクスポートだけをマニフェストへ記録できます")
        if item.recording_id != history.recording_id:
            raise UploadManifestError("キューと録画履歴のrecording_idが一致しません")
        source_path = (self.paths.recordings / history.output_path).resolve()
        export_path = export.output_path.resolve()
        source_relative = _relative_to_root(source_path, self.paths.root.resolve(), "source")
        export_relative = _relative_to_root(export_path, self.paths.root.resolve(), "export")
        source_validation = export.source_validation
        output_validation = export.output_validation
        document: dict[str, object] = {
            "schema_version": UPLOAD_MANIFEST_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "queue_id": item.queue_id,
            "recording_id": history.recording_id,
            "source": _file_document(source_path, source_relative),
            "export": {
                **_file_document(export_path, export_relative),
                "container": output_validation.container,
                "duration_seconds": output_validation.duration_seconds,
                "stream_types": list(output_validation.stream_types),
            },
            "metadata": item.metadata.to_dict(),
            "validation": {
                "source_status": source_validation.status.value,
                "source_warnings": list(source_validation.warnings),
                "export_status": output_validation.status.value,
                "export_warnings": list(output_validation.warnings),
            },
        }
        validate_upload_manifest(document)
        self.manifest_root.mkdir(parents=True, exist_ok=True)
        final_path = self.manifest_root / f"{item.queue_id}.json"
        if final_path.exists():
            existing = json.loads(final_path.read_text(encoding="utf-8"))
            validate_upload_manifest(existing)
            existing_export = existing.get("export")
            new_export = document["export"]
            assert isinstance(existing_export, dict)
            assert isinstance(new_export, dict)
            if (
                existing.get("queue_id") == item.queue_id
                and existing.get("recording_id") == history.recording_id
                and existing_export.get("sha256") == new_export.get("sha256")
            ):
                return final_path
            raise UploadManifestError(f"異なる既存マニフェストを上書きしません: {final_path}")
        temporary = final_path.with_name(f".{final_path.name}.{uuid.uuid4().hex}.tmp")
        data = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        try:
            with temporary.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, final_path)
        except OSError as exc:
            raise UploadManifestError(f"マニフェストを保存できません: {final_path}: {exc}") from exc
        return final_path


def validate_upload_manifest(value: object) -> None:
    if not isinstance(value, dict):
        raise UploadManifestError("manifest はobjectである必要があります")
    expected_top = {
        "schema_version",
        "generated_at",
        "queue_id",
        "recording_id",
        "source",
        "export",
        "metadata",
        "validation",
    }
    if set(value) != expected_top:
        raise UploadManifestError("manifestの項目がスキーマと一致しません")
    if value["schema_version"] != UPLOAD_MANIFEST_SCHEMA_VERSION:
        raise UploadManifestError("未対応のmanifestスキーマ版です")
    _required_text(value["queue_id"], "queue_id")
    _required_text(value["recording_id"], "recording_id")
    generated = datetime.fromisoformat(_required_text(value["generated_at"], "generated_at"))
    if generated.tzinfo is None:
        raise UploadManifestError("generated_atにはタイムゾーンが必要です")
    _validate_file(value["source"], export=False)
    _validate_file(value["export"], export=True)
    UploadMetadata.from_dict(value["metadata"])
    validation = value["validation"]
    if not isinstance(validation, dict) or set(validation) != {
        "source_status",
        "source_warnings",
        "export_status",
        "export_warnings",
    }:
        raise UploadManifestError("validationの項目がスキーマと一致しません")
    for key in ("source_status", "export_status"):
        if validation[key] not in {"valid", "warning"}:
            raise UploadManifestError(f"{key} はvalidまたはwarningである必要があります")
    for key in ("source_warnings", "export_warnings"):
        if not isinstance(validation[key], list) or not all(
            isinstance(item, str) for item in validation[key]
        ):
            raise UploadManifestError(f"{key} は文字列配列である必要があります")


def _validate_file(value: object, *, export: bool) -> None:
    if not isinstance(value, dict):
        raise UploadManifestError("file情報はobjectである必要があります")
    expected = {"path", "size_bytes", "sha256"}
    if export:
        expected.update({"container", "duration_seconds", "stream_types"})
    if set(value) != expected:
        raise UploadManifestError("file情報の項目がスキーマと一致しません")
    path = Path(_required_text(value["path"], "path"))
    if path.is_absolute() or ".." in path.parts:
        raise UploadManifestError("file pathは安全な相対パスである必要があります")
    size = value["size_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise UploadManifestError("size_bytesは正の整数である必要があります")
    digest = value["sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise UploadManifestError("sha256の形式が不正です")
    if export:
        _required_text(value["container"], "container")
        duration = value["duration_seconds"]
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
            raise UploadManifestError("duration_secondsは正の数値である必要があります")
        streams = value["stream_types"]
        if (
            not isinstance(streams, list)
            or not all(isinstance(stream, str) for stream in streams)
            or "video" not in streams
        ):
            raise UploadManifestError("stream_typesにはvideoが必要です")


def _file_document(path: Path, relative: Path) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise UploadManifestError(f"マニフェスト対象ファイルが存在しないか空です: {path}")
    return {
        "path": relative.as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _relative_to_root(path: Path, root: Path, key: str) -> Path:
    try:
        return path.relative_to(root)
    except ValueError as exc:
        raise UploadManifestError(f"{key}はuser_data配下である必要があります") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_text(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UploadManifestError(f"{key} は空でない文字列である必要があります")
    return value.strip()
