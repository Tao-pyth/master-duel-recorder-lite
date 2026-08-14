from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid
import zipfile

from .config import AppConfigError, load_app_config
from .history_database import (
    CURRENT_SCHEMA_VERSION,
    HISTORY_DATABASE_NAME,
    HistoryDatabaseInfo,
    initialize_history_database,
)
from .runtime_paths import RuntimePaths


BACKUP_SCHEMA = "mdrl-data-backup-v1"
DATABASE_MEMBER = "data/history.sqlite3"
CONFIG_MEMBER = "config/app.toml"
MANIFEST_MEMBER = "manifest.json"
DEFAULT_MAX_GENERATIONS = 20
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
COUNT_TABLES = {
    "recordings": "recordings",
    "duels": "duel_records",
    "decks": "duel_catalog_entries WHERE kind = 'deck'",
    "tags": "duel_catalog_entries WHERE kind = 'tag'",
    "seasons": "seasons",
    "filters": "saved_duel_filters",
}


class DataProtectionError(RuntimeError):
    """データを検証可能な状態で保全・復元できない場合のエラーです。"""


@dataclass(frozen=True)
class BackupInfo:
    path: Path
    created_at: datetime
    reason: str
    schema_version: int
    size_bytes: int
    sha256: str
    protected: bool


@dataclass(frozen=True)
class RestorePreview:
    backup: BackupInfo
    current_schema_version: int
    current_counts: dict[str, int]
    backup_counts: dict[str, int]
    config_included: bool


@dataclass(frozen=True)
class IntegrityFinding:
    severity: str
    code: str
    message: str
    recommendation: str


@dataclass(frozen=True)
class IntegrityReport:
    checked_at: datetime
    findings: tuple[IntegrityFinding, ...]
    counts: dict[str, int]

    @property
    def healthy(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)


class DataProtectionService:
    def __init__(
        self,
        paths: RuntimePaths,
        *,
        max_generations: int = DEFAULT_MAX_GENERATIONS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if max_generations < 2 or max_bytes < 1024 * 1024:
            raise ValueError("バックアップ保持上限が小さすぎます")
        self.paths = paths
        self.database_path = (paths.db / HISTORY_DATABASE_NAME).resolve()
        self.config_path = (paths.config / "app.toml").resolve()
        self.backup_directory = (paths.data / "backups").resolve()
        self.max_generations = max_generations
        self.max_bytes = max_bytes

    def create_backup(self, reason: str, *, protected: bool = False) -> BackupInfo:
        normalized_reason = _safe_reason(reason)
        if not self.database_path.is_file():
            raise DataProtectionError("バックアップ対象の履歴DBがありません")
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc)
        identifier = uuid.uuid4().hex
        destination = self.backup_directory / (
            f"mdrl-{stamp:%Y%m%dT%H%M%S%fZ}-{normalized_reason}-{identifier}.mdrl-backup"
        )
        temporary = destination.with_suffix(".tmp")
        try:
            with tempfile.TemporaryDirectory(dir=self.backup_directory) as temporary_dir:
                snapshot = Path(temporary_dir) / "history.sqlite3"
                _sqlite_backup(self.database_path, snapshot)
                schema_version, counts = _validate_database(snapshot)
                members: dict[str, Path] = {DATABASE_MEMBER: snapshot}
                if self.config_path.is_file():
                    members[CONFIG_MEMBER] = self.config_path
                manifest = {
                    "schema": BACKUP_SCHEMA,
                    "created_at": stamp.isoformat(),
                    "reason": normalized_reason,
                    "protected": protected,
                    "schema_version": schema_version,
                    "counts": counts,
                    "members": {
                        name: {
                            "size": path.stat().st_size,
                            "sha256": _file_hash(path),
                        }
                        for name, path in members.items()
                    },
                }
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                    for name, path in members.items():
                        archive.write(path, name)
                    archive.writestr(
                        MANIFEST_MEMBER,
                        json.dumps(manifest, ensure_ascii=False, indent=2),
                    )
            os.replace(temporary, destination)
            info = self.inspect_backup(destination)
            self.rotate()
            return info
        except (
            OSError,
            sqlite3.Error,
            zipfile.BadZipFile,
            ValueError,
            DataProtectionError,
        ) as exc:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            if isinstance(exc, DataProtectionError):
                raise
            raise DataProtectionError(f"バックアップを作成できません: {exc}") from exc

    def list_backups(self) -> tuple[BackupInfo, ...]:
        if not self.backup_directory.is_dir():
            return ()
        results: list[BackupInfo] = []
        for path in self.backup_directory.glob("*.mdrl-backup"):
            try:
                results.append(self.inspect_backup(path))
            except DataProtectionError:
                continue
        return tuple(sorted(results, key=lambda item: item.created_at, reverse=True))

    def inspect_backup(self, path: Path) -> BackupInfo:
        source = path.expanduser().resolve()
        manifest = _read_manifest(source)
        with zipfile.ZipFile(source) as archive:
            _verify_members(archive, manifest)
        return BackupInfo(
            source,
            _parse_datetime(manifest["created_at"]),
            str(manifest["reason"]),
            int(manifest["schema_version"]),
            source.stat().st_size,
            _file_hash(source),
            bool(manifest.get("protected", False)),
        )

    def preview_restore(self, path: Path) -> RestorePreview:
        source = path.expanduser().resolve()
        backup = self.inspect_backup(source)
        with tempfile.TemporaryDirectory() as temporary_dir:
            database, config_included = _extract_verified(source, Path(temporary_dir))
            schema_version, backup_counts = _validate_database(database)
        if schema_version > CURRENT_SCHEMA_VERSION:
            raise DataProtectionError(
                f"バックアップのDBスキーマ版{schema_version}はこのアプリでは復元できません"
            )
        current_schema, current_counts = _validate_database(self.database_path)
        return RestorePreview(
            backup,
            current_schema,
            current_counts,
            backup_counts,
            config_included,
        )

    def restore(
        self, path: Path, *, create_safety_backup: bool = True
    ) -> RestorePreview:
        preview = self.preview_restore(path)
        if create_safety_backup:
            self.create_backup("pre-restore", protected=True)
        rollback_db = self.database_path.with_name(
            f".{self.database_path.name}.{uuid.uuid4().hex}.rollback"
        )
        rollback_config = self.config_path.with_name(
            f".{self.config_path.name}.{uuid.uuid4().hex}.rollback"
        )
        replaced_db = False
        replaced_config = False
        try:
            with tempfile.TemporaryDirectory(dir=self.paths.data) as temporary_dir:
                candidate_db, config_included = _extract_verified(
                    preview.backup.path, Path(temporary_dir)
                )
                _validate_database(candidate_db)
                _sqlite_backup(self.database_path, rollback_db)
                replacement = self.database_path.with_name(
                    f".{self.database_path.name}.{uuid.uuid4().hex}.restore"
                )
                shutil.copy2(candidate_db, replacement)
                os.replace(replacement, self.database_path)
                replaced_db = True
                config_existed = self.config_path.is_file()
                if config_included:
                    candidate_config = Path(temporary_dir) / CONFIG_MEMBER
                    self.config_path.parent.mkdir(parents=True, exist_ok=True)
                    if self.config_path.is_file():
                        shutil.copy2(self.config_path, rollback_config)
                    config_replacement = self.config_path.with_name(
                        f".{self.config_path.name}.{uuid.uuid4().hex}.restore"
                    )
                    shutil.copy2(candidate_config, config_replacement)
                    os.replace(config_replacement, self.config_path)
                    replaced_config = True
                _validate_database(self.database_path)
            rollback_db.unlink(missing_ok=True)
            rollback_config.unlink(missing_ok=True)
            return preview
        except Exception as exc:
            if replaced_db and rollback_db.is_file():
                os.replace(rollback_db, self.database_path)
            if replaced_config:
                if rollback_config.is_file():
                    os.replace(rollback_config, self.config_path)
                elif not config_existed:
                    self.config_path.unlink(missing_ok=True)
            rollback_db.unlink(missing_ok=True)
            rollback_config.unlink(missing_ok=True)
            raise DataProtectionError(f"復元に失敗したため元データへ戻しました: {exc}") from exc

    def rotate(self) -> None:
        backups = list(self.list_backups())
        retained = [item for item in backups if item.protected]
        removable = [item for item in backups if not item.protected]
        total = sum(item.size_bytes for item in retained)
        keep: set[Path] = {item.path for item in retained}
        for item in removable:
            if len(keep) < self.max_generations and total + item.size_bytes <= self.max_bytes:
                keep.add(item.path)
                total += item.size_bytes
        root = self.backup_directory.resolve()
        for item in removable:
            if item.path not in keep and item.path.parent.resolve() == root:
                item.path.unlink(missing_ok=True)

    def diagnose(self) -> IntegrityReport:
        findings: list[IntegrityFinding] = []
        counts: dict[str, int] = {}
        try:
            load_app_config(user_data_dir=self.paths.root)
        except AppConfigError as exc:
            findings.append(
                IntegrityFinding(
                    "error",
                    "invalid_config",
                    f"設定を読み込めません: {exc}",
                    "設定画面で値を確認するか、検証済みバックアップから復元してください",
                )
            )
        try:
            _schema, counts = _validate_database(self.database_path)
        except DataProtectionError as exc:
            findings.append(
                IntegrityFinding("error", "database", str(exc), "検証済みバックアップから復元してください")
            )
            return IntegrityReport(datetime.now(timezone.utc), tuple(findings), counts)
        with closing(_read_only_connection(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT recording_id, output_path, size_bytes FROM recordings ORDER BY recording_id"
            ).fetchall()
            referenced: set[Path] = set()
            for recording_id, stored, expected_size in rows:
                relative = Path(str(stored))
                if relative.is_absolute() or ".." in relative.parts:
                    findings.append(
                        IntegrityFinding(
                            "error", "unsafe_recording_path", f"録画参照が不正です: {recording_id}", "録画参照を確認してください"
                        )
                    )
                    continue
                referenced.add(relative)
                recording = self.paths.recordings / relative
                if not recording.is_file():
                    findings.append(
                        IntegrityFinding(
                            "warning", "missing_recording", f"録画ファイルがありません: {recording_id}", "録画ファイルを再関連付けしてください"
                        )
                    )
                elif expected_size is not None and recording.stat().st_size != expected_size:
                    findings.append(
                        IntegrityFinding(
                            "warning",
                            "recording_size_mismatch",
                            f"録画サイズが履歴と一致しません: {recording_id}",
                            "録画内容を確認してから再関連付けしてください",
                        )
                    )
        if self.paths.recordings.is_dir():
            for recording in self.paths.recordings.rglob("*"):
                if not recording.is_file() or recording.suffix.casefold() not in {".mkv", ".mp4"}:
                    continue
                relative = recording.relative_to(self.paths.recordings)
                if relative not in referenced:
                    findings.append(
                        IntegrityFinding(
                            "warning",
                            "untracked_recording",
                            f"履歴に未登録の録画があります: {relative.as_posix()}",
                            "必要に応じて既存履歴へ再関連付けしてください",
                        )
                    )
        if not findings:
            findings.append(IntegrityFinding("ok", "healthy", "整合性に問題はありません", "操作は不要です"))
        return IntegrityReport(datetime.now(timezone.utc), tuple(findings), counts)


def initialize_protected_history_database(paths: RuntimePaths) -> HistoryDatabaseInfo:
    protection = DataProtectionService(paths)
    return initialize_history_database(
        paths.db / HISTORY_DATABASE_NAME,
        recordings_root=paths.recordings,
        migration_backup_factory=lambda version: protection.create_backup(
            f"pre-migration-v{version}", protected=True
        ).path,
    )


def _safe_reason(value: str) -> str:
    normalized = "-".join(part for part in value.strip().lower().replace("_", "-").split("-") if part)
    if not normalized or any(not (char.isalnum() or char == "-") for char in normalized):
        raise DataProtectionError("バックアップ理由は英数字とハイフンで指定してください")
    return normalized[:40]


def _sqlite_backup(source_path: Path, destination: Path) -> None:
    source = _read_only_connection(source_path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10)


def _validate_database(path: Path) -> tuple[int, dict[str, int]]:
    if not path.is_file():
        raise DataProtectionError(f"履歴DBがありません: {path}")
    try:
        with closing(_read_only_connection(path)) as connection:
            quick = connection.execute("PRAGMA quick_check").fetchone()
            if quick is None or quick[0] != "ok":
                raise DataProtectionError(f"SQLite quick_checkに失敗しました: {quick}")
            foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign:
                raise DataProtectionError("SQLiteの外部キー整合性に問題があります")
            row = connection.execute(
                "SELECT version FROM schema_version WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise DataProtectionError("DBスキーマ版を確認できません")
            tables = {
                str(item[0])
                for item in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            counts = {}
            for name, clause in COUNT_TABLES.items():
                table = clause.split(maxsplit=1)[0]
                counts[name] = (
                    int(connection.execute(f"SELECT COUNT(*) FROM {clause}").fetchone()[0])
                    if table in tables
                    else 0
                )
            return int(row[0]), counts
    except sqlite3.Error as exc:
        raise DataProtectionError(f"履歴DBを検証できません: {exc}") from exc


def _read_manifest(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise DataProtectionError(f"バックアップがありません: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            allowed = {MANIFEST_MEMBER, DATABASE_MEMBER, CONFIG_MEMBER}
            if not names.issubset(allowed) or MANIFEST_MEMBER not in names or DATABASE_MEMBER not in names:
                raise DataProtectionError("バックアップに未許可のファイルがあります")
            manifest = json.loads(archive.read(MANIFEST_MEMBER).decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise DataProtectionError(f"バックアップを読み取れません: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != BACKUP_SCHEMA:
        raise DataProtectionError("未対応のバックアップ形式です")
    if not isinstance(manifest.get("members"), dict):
        raise DataProtectionError("バックアップmanifestが不正です")
    return manifest


def _verify_members(archive: zipfile.ZipFile, manifest: dict[str, object]) -> None:
    members = manifest["members"]
    assert isinstance(members, dict)
    for name, metadata in members.items():
        if name not in {DATABASE_MEMBER, CONFIG_MEMBER} or not isinstance(metadata, dict):
            raise DataProtectionError("バックアップmember定義が不正です")
        payload = archive.read(name)
        if len(payload) != int(metadata.get("size", -1)):
            raise DataProtectionError(f"バックアップmemberのサイズが不正です: {name}")
        if hashlib.sha256(payload).hexdigest() != metadata.get("sha256"):
            raise DataProtectionError(f"バックアップmemberのハッシュが不正です: {name}")


def _extract_verified(path: Path, destination: Path) -> tuple[Path, bool]:
    manifest = _read_manifest(path)
    with zipfile.ZipFile(path) as archive:
        _verify_members(archive, manifest)
        for name in manifest["members"]:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    return destination / DATABASE_MEMBER, (destination / CONFIG_MEMBER).is_file()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise DataProtectionError("バックアップ作成日時が不正です")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DataProtectionError("バックアップ作成日時が不正です") from exc
    if parsed.tzinfo is None:
        raise DataProtectionError("バックアップ作成日時にタイムゾーンがありません")
    return parsed.astimezone(timezone.utc)
