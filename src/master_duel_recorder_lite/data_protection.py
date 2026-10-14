from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Iterator
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
from .recording_lock import RecordingBusyError, RecordingLock


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
    "card_targets": "card_targets",
    "card_analysis_jobs": "card_analysis_jobs",
    "card_observations": "card_observations",
    "card_nonuse_confirmations": "card_nonuse_confirmations",
    "card_opener_confirmations": "card_opener_confirmations",
    "card_identities": "card_identities",
    "duel_card_information": "duel_card_information",
    "duel_card_openers": "duel_card_openers",
    "duel_card_information_changes": "duel_card_information_changes",
    "duel_card_opener_changes": "duel_card_opener_changes",
    "recording_video_assets": "recording_video_assets",
    "recording_video_protection": "recording_video_protection",
    "video_deletion_history": "video_deletion_history",
    "video_deletion_journal": "video_deletion_journal",
    "upload_cleanup_requests": "upload_cleanup_requests",
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
        with self._backup_lock():
            return self._create_backup(reason, protected=protected)

    @contextmanager
    def retained_backup(self, reason: str) -> Iterator[BackupInfo]:
        """削除処理が終わるまで、別プロセスの世代整理からバックアップを守ります。"""
        with self._backup_lock():
            info = self._create_backup(reason, protected=False)
            yield info

    @contextmanager
    def _backup_lock(self) -> Iterator[None]:
        try:
            lock = RecordingLock.acquire(
                self.paths.data / "data-protection.lock", recording_id="data-protection"
            )
        except (RecordingBusyError, OSError) as exc:
            raise DataProtectionError("別のバックアップ・データ保護処理が実行中です") from exc
        with lock:
            yield

    def _create_backup(self, reason: str, *, protected: bool) -> BackupInfo:
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
            self._rotate(pinned_paths=(destination,))
            if self.inspect_backup(destination).sha256 != info.sha256:
                raise DataProtectionError("作成したバックアップを保持できません")
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
        from .video_management import VideoManagementError, video_management_gate

        try:
            with video_management_gate(self.paths, database_path=self.database_path):
                return self._restore_locked(path, create_safety_backup=create_safety_backup)
        except VideoManagementError as exc:
            raise DataProtectionError(str(exc)) from exc

    def _restore_locked(self, path: Path, *, create_safety_backup: bool) -> RestorePreview:
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
        config_existed = self.config_path.is_file()
        replacements: list[Path] = []
        try:
            with tempfile.TemporaryDirectory(dir=self.paths.data) as temporary_dir:
                candidate_db, config_included = _extract_verified(
                    preview.backup.path, Path(temporary_dir)
                )
                _validate_database(candidate_db)
                if config_included:
                    load_app_config(user_data_dir=Path(temporary_dir))
                # 候補で移行と隔離を完了してから、現在のDBを置き換えます。
                _prepare_restored_database(candidate_db, self.paths, Path(temporary_dir), self)
                _validate_database(candidate_db)
                _sqlite_backup(self.database_path, rollback_db)
                replacement = self.database_path.with_name(
                    f".{self.database_path.name}.{uuid.uuid4().hex}.restore"
                )
                replacements.append(replacement)
                shutil.copy2(candidate_db, replacement)
                os.replace(replacement, self.database_path)
                replaced_db = True
                if config_included:
                    candidate_config = Path(temporary_dir) / CONFIG_MEMBER
                    self.config_path.parent.mkdir(parents=True, exist_ok=True)
                    if self.config_path.is_file():
                        shutil.copy2(self.config_path, rollback_config)
                    config_replacement = self.config_path.with_name(
                        f".{self.config_path.name}.{uuid.uuid4().hex}.restore"
                    )
                    replacements.append(config_replacement)
                    shutil.copy2(candidate_config, config_replacement)
                    os.replace(config_replacement, self.config_path)
                    replaced_config = True
                _validate_database(self.database_path)
                if config_included:
                    load_app_config(user_data_dir=self.paths.root)
            rollback_db.unlink(missing_ok=True)
            rollback_config.unlink(missing_ok=True)
            return preview
        except Exception as exc:
            rollback_errors: list[str] = []
            try:
                if replaced_db and rollback_db.is_file():
                    os.replace(rollback_db, self.database_path)
            except Exception as rollback_error:
                rollback_errors.append(f"DB: {rollback_error}")
            try:
                if replaced_config:
                    if rollback_config.is_file():
                        os.replace(rollback_config, self.config_path)
                    elif not config_existed:
                        self.config_path.unlink(missing_ok=True)
            except Exception as rollback_error:
                rollback_errors.append(f"設定: {rollback_error}")
            if rollback_errors:
                raise DataProtectionError(
                    f"復元に失敗し、元データへの復帰も確認できません: {exc}; "
                    f"{' / '.join(rollback_errors)}。退避DB・設定と保護バックアップを保持しました"
                ) from exc
            rollback_db.unlink(missing_ok=True)
            rollback_config.unlink(missing_ok=True)
            raise DataProtectionError(f"復元に失敗したため元データへ戻しました: {exc}") from exc
        finally:
            for replacement in replacements:
                replacement.unlink(missing_ok=True)

    def rotate(self) -> None:
        with self._backup_lock():
            self._rotate()

    def _rotate(self, *, pinned_paths: tuple[Path, ...] = ()) -> None:
        backups = list(self.list_backups())
        pins = {path.resolve() for path in pinned_paths}
        retained = [item for item in backups if item.protected or item.path in pins]
        removable = [item for item in backups if not item.protected and item.path not in pins]
        total = sum(item.size_bytes for item in retained)
        keep: set[Path] = {item.path for item in retained}
        if not pins.issubset(keep):
            raise DataProtectionError("処理に必要なバックアップを確認できません")
        # 移行前の保護世代は残す一方、削除用の通常世代を確保できなければ中止します。
        if any(item.path in pins and not item.protected for item in retained):
            if len(keep) > self.max_generations or total > self.max_bytes:
                raise DataProtectionError("削除前バックアップを保持する世代数・容量が不足しています")
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
            tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            video_states = {}
            if "recording_video_assets" in tables:
                video_states = {
                    row[0]: row[1:]
                    for row in connection.execute(
                        "SELECT a.recording_id,a.state,h.reason,h.cause_known FROM recording_video_assets a "
                        "LEFT JOIN video_deletion_history h ON h.asset_id=a.asset_id AND h.generation=a.generation WHERE a.is_current=1"
                    )
                }
                pending = connection.execute("SELECT COUNT(*) FROM video_deletion_journal WHERE state IN ('intent','staged','unlinked')").fetchone()[0]
                if pending:
                    findings.append(IntegrityFinding(
                        "error", "pending_video_deletion", f"未完了の動画削除が{pending}件あります",
                        "通常GUI起動で削除を回復してから管理操作を実行してください",
                    ))
                retries = connection.execute("SELECT COUNT(*) FROM upload_cleanup_requests WHERE state='pending'").fetchone()[0]
                if retries:
                    findings.append(IntegrityFinding(
                        "warning", "pending_upload_cleanup", f"投稿後削除の再試行待ちが{retries}件あります",
                        "最新の投稿後削除設定・保護状態を確認し、通常GUIを再起動してください",
                    ))
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
                video_state = video_states.get(recording_id)
                if video_state is not None and video_state[0] == "deleted":
                    restored = video_state[1] == "restored_deleted"
                    findings.append(IntegrityFinding(
                        "warning" if restored else "ok",
                        "restored_deleted_recording" if restored else "deleted_recording",
                        (f"復元時削除済み: {recording_id}（元の削除原因は不明）" if restored
                         else f"元動画は削除済みです: {recording_id}（{video_state[1] or '原因の記録なし'}）"),
                        "DB・設定のバックアップには動画本体は含まれません。管理情報は保持されています",
                    ))
                    continue
                if video_state is not None and video_state[0] == "unavailable":
                    findings.append(IntegrityFinding(
                        "warning", "recording_unavailable", f"動画の保存先・実体を確認できません: {recording_id}",
                        "保存先の接続・読み取り権限を確認してください。削除済みとは判定していません",
                    ))
                    continue
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


def _prepare_restored_database(
    candidate: Path, paths: RuntimePaths, extracted_root: Path, protection: DataProtectionService,
) -> None:
    """復元候補内だけを移行・隔離し、動画実体を変更せずに状態を確認します。"""
    candidate_paths = replace(paths, db=candidate.parent, config=extracted_root / "config")
    candidate_protection = DataProtectionService(
        candidate_paths, max_generations=protection.max_generations, max_bytes=protection.max_bytes,
    )
    initialize_history_database(
        candidate, recordings_root=None,
        migration_backup_factory=lambda version: candidate_protection.create_backup(
            f"pre-restore-migration-v{version}", protected=True,
        ).path,
    )
    from .video_lifecycle import VideoLifecycleRepository

    lifecycle = VideoLifecycleRepository(database_path=candidate, recordings_root=paths.recordings)
    with closing(_read_only_connection(candidate)) as source:
        unregistered = source.execute(
            "SELECT recording_id FROM recordings r WHERE NOT EXISTS "
            "(SELECT 1 FROM recording_video_assets a WHERE a.recording_id=r.recording_id)"
        ).fetchall()
    for recording_id, in unregistered:
        lifecycle.ensure_current_asset(recording_id)
    stamp = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(candidate)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        for table in ("card_analysis_job_sources", "card_observation_links", "duel_event_sources"):
            connection.execute(f"UPDATE {table} SET verified=0")
            key = "job_id" if table == "card_analysis_job_sources" else "event_id"
            for row in connection.execute(f"SELECT {key},input_fingerprint_json FROM {table}").fetchall():
                proof = json.loads(row["input_fingerprint_json"])
                if not isinstance(proof, dict):
                    raise DataProtectionError("復元候補の動画識別情報が不正です")
                if proof.get("schema") == "mdrl-timeline-capture-v1":
                    proof["verification_origin"] = "backup_restore"
                    connection.execute(f"UPDATE {table} SET input_fingerprint_json=? WHERE {key}=?",
                                       (json.dumps(proof, ensure_ascii=False), row[key]))
        connection.execute("UPDATE recording_video_assets SET state='unverified',updated_at=? WHERE state<>'deleted'", (stamp,))
        connection.execute("DELETE FROM video_asset_uses")
        connection.execute(
            "UPDATE card_analysis_jobs SET state='waiting',error=?,updated_at=? WHERE state='running'",
            ("バックアップ復元後の解析入力は確認待ちです", stamp),
        )
        connection.execute(
            "UPDATE youtube_uploads SET state='failed',error=?,updated_at=? WHERE state IN ('preparing','uploading')",
            ("バックアップ復元後の投稿状況は未確認です。再投稿前にYouTube Studioを確認してください", stamp),
        )
        pending_assets: set[str] = set()
        for intent in connection.execute("SELECT intent_id,asset_id,metadata_json FROM video_deletion_journal WHERE state IN ('intent','staged','unlinked')").fetchall():
            metadata = json.loads(intent["metadata_json"])
            if not isinstance(metadata, dict):
                raise DataProtectionError("復元候補の削除journal情報が不正です")
            metadata["restored_from_backup"] = True
            metadata["restored_at"] = stamp
            connection.execute("UPDATE video_deletion_journal SET metadata_json=? WHERE intent_id=?", (json.dumps(metadata, ensure_ascii=False), intent["intent_id"]))
            pending_assets.add(intent["asset_id"])
        for asset in connection.execute("SELECT * FROM recording_video_assets WHERE is_current=1 AND state<>'deleted'").fetchall():
            # snapshotの未完了intentは、この復元では実行・完了へ変換しません。
            if asset["asset_id"] in pending_assets:
                continue
            state = _restored_file_state(paths, asset["path"])
            if state == "missing":
                connection.execute("UPDATE recording_video_assets SET state='deleted',updated_at=? WHERE asset_id=?", (stamp, asset["asset_id"]))
                connection.execute(
                    "INSERT OR IGNORE INTO video_deletion_history (deletion_id,recording_id,asset_id,generation,reason,deleted_at,original_path,size_bytes,sha256,cause_known,metadata_json) "
                    "VALUES (?,?,?,?,'restored_deleted',?,?,?,?,0,?)",
                    (uuid.uuid4().hex, asset["recording_id"], asset["asset_id"], asset["generation"],
                     stamp, asset["path"], asset["size_bytes"], asset["sha256"],
                     json.dumps({"restored_at": stamp, "original_deletion_time_known": False}, ensure_ascii=False)),
                )
            elif state == "unavailable":
                connection.execute("UPDATE recording_video_assets SET state='unavailable',updated_at=? WHERE asset_id=?", (stamp, asset["asset_id"]))


def _restored_file_state(paths: RuntimePaths, relative: str) -> str:
    from .recording_retention import safe_file_path

    try:
        root = paths.recordings
        if not root.stat().st_mode:
            return "unavailable"
        # statの成功だけで未接続や読み取り権限を推定しないよう、保存先への到達を確認します。
        with os.scandir(root) as entries:
            next(entries, None)
        target = safe_file_path(root, relative, must_exist=False)
        try:
            target.stat()
        except FileNotFoundError:
            # 保存先が途中で見えなくなった場合は『削除済み』としません。
            with os.scandir(root) as entries:
                next(entries, None)
            return "missing"
        return "present" if target.is_file() else "unavailable"
    except (OSError, ValueError, RuntimeError):
        return "unavailable"


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
