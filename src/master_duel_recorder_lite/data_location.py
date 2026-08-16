from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid

from .history_database import HISTORY_DATABASE_NAME
from .runtime_paths import RuntimePaths, local_application_data_root


class DataLocationError(RuntimeError):
    """データ保存先を安全に切り替えられない場合のエラーです。"""


@dataclass(frozen=True)
class DataRelocationResult:
    source: Path
    destination: Path
    copied_bytes: int
    restart_required: bool = True


def runtime_root_pointer_path() -> Path:
    app_root = local_application_data_root()
    return app_root.parent / f".{app_root.name}.runtime-root.json"


def load_runtime_root_pointer() -> Path | None:
    path = runtime_root_pointer_path()
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        raw = document.get("runtime_root") if isinstance(document, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            return None
        return Path(raw).expanduser().resolve()
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def save_runtime_root_pointer(root: Path) -> Path:
    destination = root.expanduser().resolve()
    pointer = runtime_root_pointer_path()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    temporary = pointer.with_name(f".{pointer.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(
        {"schema_version": 1, "runtime_root": str(destination)},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, pointer)
    finally:
        temporary.unlink(missing_ok=True)
    return pointer


def relocate_runtime_data(paths: RuntimePaths, destination: Path) -> DataRelocationResult:
    source = paths.root.expanduser().resolve()
    target = destination.expanduser().resolve()
    _validate_target(source, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{target.name}.mdrl-move-", dir=target.parent)
    ).resolve()
    try:
        shutil.rmtree(staging)
        shutil.copytree(source, staging, copy_function=shutil.copy2)
        _validate_copy(staging)
        copied_bytes = sum(
            item.stat().st_size for item in staging.rglob("*") if item.is_file()
        )
        if target.exists():
            target.rmdir()
        os.replace(staging, target)
        save_runtime_root_pointer(target)
        return DataRelocationResult(source, target, copied_bytes)
    except DataLocationError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise DataLocationError(f"データ保存先を変更できません: {exc}") from exc


def _validate_target(source: Path, target: Path) -> None:
    if source == target:
        raise DataLocationError("現在と同じデータ保存先です")
    if target == Path(target.anchor):
        raise DataLocationError("ドライブ直下はデータ保存先に指定できません")
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise DataLocationError("現在の保存先と親子関係にあるフォルダは指定できません")
    if target.exists():
        if not target.is_dir():
            raise DataLocationError("データ保存先はフォルダである必要があります")
        if any(target.iterdir()):
            raise DataLocationError("空の専用フォルダを指定してください")
    try:
        usage = shutil.disk_usage(target.parent if target.parent.exists() else source)
        required = sum(item.stat().st_size for item in source.rglob("*") if item.is_file())
        if usage.free < required + 64 * 1024 * 1024:
            raise DataLocationError("データ移行に必要な空き容量がありません")
    except OSError as exc:
        raise DataLocationError(f"保存先の空き容量を確認できません: {exc}") from exc


def _validate_copy(root: Path) -> None:
    database = root / "data" / "db" / HISTORY_DATABASE_NAME
    if not database.is_file():
        return
    with closing(sqlite3.connect(database)) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if integrity is None or integrity[0] != "ok" or foreign_keys:
        raise DataLocationError("移行後データのSQLite整合性確認に失敗しました")
