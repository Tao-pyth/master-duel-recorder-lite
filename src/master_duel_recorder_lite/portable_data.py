"""管理JSONの形式と隔離DBへの材料化。実行キューや動画ファイルを扱いません。"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
from typing import Iterator
from urllib.parse import parse_qs, urlparse
import uuid

from .card_analysis_models import normalize_card_name
from .history_database import CURRENT_SCHEMA_VERSION, _MIGRATIONS, initialize_history_database


PORTABLE_SCHEMA = "mdrl-managed-data-v2"
LEGACY_SCHEMA = "mdrl-managed-data-v1"
LEGACY_TABLES = (
    "recordings", "duel_catalog_entries", "seasons", "duel_records", "duel_record_tags",
    "duel_record_changes", "duel_events", "duel_record_tag_links", "duel_editor_preferences",
    "saved_duel_filters",
)
PORTABLE_TABLES = (
    "recordings", "duel_catalog_entries", "seasons", "duel_records", "duel_record_tags",
    "duel_record_changes", "duel_record_tag_links", "duel_editor_preferences", "saved_duel_filters",
    "deck_tag_links", "card_identities", "card_targets", "card_target_identity_links",
    "duel_card_information", "duel_card_information_changes", "duel_card_openers", "duel_card_opener_changes",
    "recording_video_assets", "recording_video_protection", "duel_events", "duel_event_sources",
    "card_observations", "card_observation_links", "youtube_uploads", "video_deletion_history",
    "card_migration_review_items",
)
FORBIDDEN_TABLES = frozenset({
    "card_analysis_jobs", "card_analysis_job_sources", "video_deletion_journal", "upload_cleanup_requests",
    "video_asset_uses", "upload_queue", "queue", "oauth", "credentials",
})
_SECRET_KEYS = frozenset({
    "access_token", "refresh_token", "id_token", "client_secret", "authorization",
    "oauth", "credentials", "checkpoint", "video_bytes", "file_contents",
})
_FLAG_COLUMNS = frozenset({"protected", "is_current", "verified", "enabled", "manual_locked", "cause_known",
                           "is_archived", "opponent_only", "hidden_from_history_statistics", "deck_only"})
_LEGACY_DEFAULTS = {
    "duel_records": {"coin_face": "unknown"},
    "seasons": {"report_goal": "", "report_highlights": "", "report_challenges": "",
                "report_next_plan": "", "report_revision": 0},
}


class PortableDataError(RuntimeError):
    """持ち出す利用者データの構成・参照関係が不正です。"""


@dataclass(frozen=True)
class PortableImportPlan:
    source_schema: str
    tables: dict[str, list[dict[str, object]]]
    id_maps: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return sum(len(rows) for rows in self.tables.values())


@contextmanager
def _empty_schema() -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            for version in range(1, CURRENT_SCHEMA_VERSION + 1):
                _MIGRATIONS[version](connection)
        yield connection


def get_portable_export(database_path: Path, recordings_root: Path | None = None) -> dict[str, object]:
    """保存済み情報のみを書き出します。DB移行・動画読込・処理再開はしません。"""
    path = database_path.expanduser().resolve()
    root = recordings_root.expanduser().resolve() if recordings_root else path.parent.parent / "recordings"
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            row = connection.execute("SELECT version FROM schema_version WHERE singleton=1").fetchone()
            if row is None or row[0] != CURRENT_SCHEMA_VERSION:
                raise PortableDataError("管理JSONの書出しには移行済みの履歴DBが必要です")
            tables = {
                table: [dict(row) for row in connection.execute(
                    f"SELECT * FROM {table}" + (" WHERE state='completed'" if table == "youtube_uploads" else "")
                )] for table in PORTABLE_TABLES
            }
        for table, rows in tables.items():
            for row in rows:
                if table == "recordings":
                    row["output_path"] = _portable_path(row["output_path"], root)
                if table == "recording_video_assets":
                    row["path"] = _portable_path(row["path"], root)
                if table == "card_observations":
                    row["job_id"] = None
                if table in {"card_observation_links", "duel_event_sources"}:
                    fingerprint = _json_column(row["input_fingerprint_json"])
                    if fingerprint:
                        fingerprint["path"] = _portable_path(fingerprint["path"], root)
                    row["input_fingerprint_json"] = _json(fingerprint)
                if table == "youtube_uploads":
                    row["prepare_queue_id"] = None
                if table == "video_deletion_history":
                    row["original_path"] = _portable_path(row["original_path"], root)
                    row["prepare_queue_id"] = None
                    row["metadata_json"] = "{}"
                    if row["upload_id"] not in {item["upload_id"] for item in tables["youtube_uploads"]}:
                        row["upload_id"] = None
                if table == "card_migration_review_items":
                    row["data_json"] = _json(_clear_execution_references(_json_column(row["data_json"])))
        payload = {"schema": PORTABLE_SCHEMA, "exported_at": datetime.now(timezone.utc).isoformat(), "tables": tables}
        plan = validate_portable_document(payload)
        payload["tables"] = plan.tables
        return payload
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise PortableDataError(f"管理JSONを書き出せません: {exc}") from exc


def validate_portable_document(document: object) -> PortableImportPlan:
    """実ファイル・本番DBへ触れず、全参照と意味を検証して計画を返します。"""
    if not isinstance(document, dict) or document.get("schema") not in {PORTABLE_SCHEMA, LEGACY_SCHEMA}:
        raise PortableDataError("未対応の管理データJSONです")
    if set(document) - {"schema", "exported_at", "tables"}:
        raise PortableDataError("管理データJSONに未許可の項目があります")
    _check_structure(document)
    if "exported_at" in document:
        _timestamp(document["exported_at"], "exported_at")
    raw_tables = document.get("tables")
    if not isinstance(raw_tables, dict):
        raise PortableDataError("管理データJSONのテーブル構成が不正です")
    source_schema = str(document["schema"])
    expected = set(LEGACY_TABLES if source_schema == LEGACY_SCHEMA else PORTABLE_TABLES)
    missing = expected - set(raw_tables)
    if missing == {"saved_duel_filters"}:
        missing = set()
    if missing or set(raw_tables) - expected:
        raise PortableDataError("管理データJSONに欠落・未許可のテーブルがあります")
    try:
        with _empty_schema() as connection:
            tables: dict[str, list[dict[str, object]]] = {table: [] for table in PORTABLE_TABLES}
            recording_dates = {
                row.get("recording_id"): row.get("started_at") or row.get("created_at")
                for row in raw_tables.get("recordings", []) if isinstance(row, dict)
            } if isinstance(raw_tables.get("recordings"), list) else {}
            for table in PORTABLE_TABLES:
                rows = raw_tables.get(table, [])
                if not isinstance(rows, list):
                    raise PortableDataError(f"{table}は配列で指定してください")
                columns = {row[1]: row for row in connection.execute(f"PRAGMA table_info({table})")}
                for raw in rows:
                    if not isinstance(raw, dict):
                        raise PortableDataError(f"{table}の行が不正です")
                    item = dict(raw)
                    if table in LEGACY_TABLES:
                        _normalize_legacy_row(table, item, recording_dates)
                    if set(item) != set(columns):
                        raise PortableDataError(f"{table}の列構成が現在のDBと一致しません")
                    _validate_row_values(table, item, columns)
                    tables[table].append(item)
            if source_schema == LEGACY_SCHEMA:
                _add_legacy_video_ownership(tables)
            _quarantine_imported_sources(tables)
            with connection:
                _insert_portable_rows(connection, tables)
                _validate_semantics(connection)
            return PortableImportPlan(source_schema, tables)
    except (sqlite3.Error, OverflowError, TypeError, ValueError, KeyError) as exc:
        raise PortableDataError(f"管理データJSONの型・重複・参照関係が不正です: {exc}") from exc


def materialize_portable_plan(
    plan: PortableImportPlan, database_path: Path, recordings_root: Path,
) -> PortableImportPlan:
    """利用者情報のない隔離DBへ保存します。既存データの置換は行いません。"""
    validated = validate_portable_document({"schema": PORTABLE_SCHEMA, "tables": plan.tables})
    path = database_path.expanduser().resolve()
    root = recordings_root.expanduser().resolve()
    # rootはパスの境界だけに使い、録画の存在確認・作成・削除を行いません。
    for row in validated.tables["recordings"]:
        candidate = (root / str(row["output_path"])).resolve()
        if root not in candidate.parents:
            raise PortableDataError("動画の参照が保存先外です")
    if path.exists() and path.stat().st_size:
        try:
            with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as existing:
                names = {row[0] for row in existing.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in names - {"schema_version", "sqlite_sequence"}:
                    if table not in PORTABLE_TABLES and table not in FORBIDDEN_TABLES and table not in {
                        "tag_templates", "practice_goals", "card_analysis_job_sources",
                    }:
                        raise PortableDataError("材料化先のDBに未対応のテーブルがあります")
                    if existing.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone() is not None:
                        raise PortableDataError("材料化先は利用者情報のない隔離DBに限定します")
        except sqlite3.Error as exc:
            raise PortableDataError(f"材料化先のDBを確認できません: {exc}") from exc
    initialize_history_database(path)
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                _insert_portable_rows(connection, validated.tables)
                _validate_semantics(connection)
    except sqlite3.Error as exc:
        raise PortableDataError(f"管理データを材料化できません: {exc}") from exc
    return PortableImportPlan(plan.source_schema, validated.tables, plan.id_maps)


def remap_portable_card_ids(
    plan: PortableImportPlan, existing_cards: Iterable[Mapping[str, object] | object],
    existing_targets: Iterable[Mapping[str, object] | object] = (),
) -> PortableImportPlan:
    """共有IDの衝突を持込側へ割り当て、全関連を一括で対応させます。"""
    tables = json.loads(_json(plan.tables))
    cards = [_as_record(row) for row in existing_cards]
    targets = [_as_record(row) for row in existing_targets]
    card_map = _identity_map(tables["card_identities"], cards, "card_id")
    target_map = _identity_map(tables["card_targets"], targets, "target_id")
    mappings = {"card_id": card_map, "target_id": target_map}
    for table, rows in tables.items():
        for row in rows:
            for column in row:
                if column in mappings and isinstance(row[column], str):
                    row[column] = mappings[column].get(row[column], row[column])
                elif column.endswith("_json"):
                    row[column] = _json(_remap_json_ids(_json_column(row[column]), mappings))
    validated = validate_portable_document({"schema": PORTABLE_SCHEMA, "tables": tables})
    merged = {key: dict(value) for key, value in plan.id_maps.items()}
    for key, mapping in mappings.items():
        merged.setdefault(key, {}).update(mapping)
    return PortableImportPlan(plan.source_schema, validated.tables, merged)


def _as_record(row: Mapping[str, object] | object) -> dict[str, object]:
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "to_dict"):
        return row.to_dict()
    raise PortableDataError("既存カードの対応情報が不正です")


def _identity_map(imported: list[dict[str, object]], existing: list[dict[str, object]], key: str) -> dict[str, str]:
    by_id = {str(row[key]): row for row in existing}
    by_name = {str(row["normalized_name"]): str(row[key]) for row in existing}
    reserved = set(by_id) | {str(row[key]) for row in imported}
    result: dict[str, str] = {}
    for row in imported:
        identifier, name = str(row[key]), str(row["normalized_name"])
        if name in by_name:
            selected = by_name[name]
        elif identifier in by_id:
            counter = 0
            selected = identifier
            while selected in reserved:
                selected = uuid.uuid5(uuid.NAMESPACE_URL, _json(["mdrl-portable-id", key, identifier, name, counter])).hex
                counter += 1
        else:
            selected = identifier
        reserved.add(selected)
        result[identifier] = selected
    return result


def _remap_json_ids(value: object, maps: dict[str, dict[str, str]]) -> object:
    if isinstance(value, list):
        return [_remap_json_ids(item, maps) for item in value]
    if isinstance(value, dict):
        return {key: (maps[key].get(item, item) if key in maps and isinstance(item, str)
                      else _remap_json_ids(item, maps)) for key, item in value.items()}
    return value


def _normalize_legacy_row(table: str, row: dict[str, object], dates: dict[object, object]) -> None:
    if table == "duel_records":
        row.pop("coin_toss_outcome", None)
        if "duel_id" not in row:
            row["duel_id"] = row.get("recording_id")
            row["entry_origin"] = "recording"
            row["occurred_at"] = dates.get(row.get("recording_id"))
    if table in {"duel_record_tags", "duel_record_changes", "duel_record_tag_links"}:
        if "duel_id" not in row and "recording_id" in row:
            row["duel_id"] = row.pop("recording_id")
    for name, default in _LEGACY_DEFAULTS.get(table, {}).items():
        row.setdefault(name, default)
    json_columns = ("before_json", "after_json") if table == "duel_record_changes" else (
        ("criteria_json",) if table == "saved_duel_filters" else ()
    )
    for column in json_columns:
        parsed = _json_column(row[column])
        if isinstance(parsed, dict):
            parsed.pop("coin_toss_outcome", None)
        row[column] = _json(parsed)


def _validate_row_values(table: str, row: dict[str, object], columns: dict[str, sqlite3.Row]) -> None:
    for name, value in row.items():
        definition = columns[name]
        if value is None:
            if definition[3] or definition[5]:
                raise PortableDataError(f"{table}.{name}を省略できません")
            continue
        kind = definition[2]
        if kind == "INTEGER":
            if name in _FLAG_COLUMNS and isinstance(value, bool):
                row[name] = int(value)
            elif isinstance(value, bool) or not isinstance(value, int) or not -(1 << 63) <= value < 1 << 63:
                raise PortableDataError(f"{table}.{name}は整数で指定してください")
        elif kind == "REAL":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise PortableDataError(f"{table}.{name}は有限の数値で指定してください")
        elif not isinstance(value, str) or "\x00" in value:
            raise PortableDataError(f"{table}.{name}は文字列で指定してください")
        if name.endswith("_json"):
            parsed = _json_column(value)
            _check_structure(parsed)
            array_columns = {"diagnostics_json", "tags_json", "aliases_json", "roles_json"}
            if name in array_columns:
                if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                    raise PortableDataError(f"{table}.{name}は文字列の配列で指定してください")
            elif not isinstance(parsed, dict):
                raise PortableDataError(f"{table}.{name}はobjectで指定してください")
            row[name] = _json(parsed)
        if name.endswith("_at"):
            _timestamp(value, f"{table}.{name}")
    if table == "recordings":
        row["output_path"] = _relative_path(row["output_path"])
    if table == "recording_video_assets":
        row["path"] = _relative_path(row["path"])
        _sha(row["sha256"])
    if table == "video_deletion_history":
        row["original_path"] = _relative_path(row["original_path"])
        _sha(row["sha256"])
        if row["prepare_queue_id"] is not None:
            raise PortableDataError("削除履歴に実行キュー参照を含められません")
    if table == "card_observations" and row["job_id"] is not None:
        raise PortableDataError("カード観測に解析ジョブ参照を含められません")
    if table in {"card_observation_links", "duel_event_sources"}:
        _fingerprint(_json_column(row["input_fingerprint_json"]))
    if table in {"card_identities", "card_targets"}:
        if not str(row["name"]).strip() or normalize_card_name(str(row["name"])) != row["normalized_name"]:
            raise PortableDataError("カード名称と正規化名が一致しません")
    if table == "youtube_uploads":
        if row["state"] != "completed" or row["prepare_queue_id"] is not None:
            raise PortableDataError("JSONには実行キューを持たない完了投稿だけを含められます")
        identifier, url = row["video_id"], row["watch_url"]
        if not isinstance(identifier, str) or not identifier.strip() or not isinstance(url, str):
            raise PortableDataError("完了投稿の動画ID・URLがありません")
        parsed = urlparse(url)
        linked_id = parsed.path.strip("/") if parsed.hostname == "youtu.be" else parse_qs(parsed.query).get("v", [None])[0]
        if (parsed.scheme != "https" or parsed.hostname not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
                or parsed.username is not None or parsed.password is not None
                or set(parse_qs(parsed.query)).intersection(_SECRET_KEYS)
                or (parsed.hostname != "youtu.be" and parsed.path != "/watch") or linked_id != identifier):
            raise PortableDataError("投稿URLと動画IDが一致しません")


def _add_legacy_video_ownership(tables: dict[str, list[dict[str, object]]]) -> None:
    assets: dict[object, str] = {}
    for row in tables["recordings"]:
        asset_id = uuid.uuid5(uuid.NAMESPACE_URL, _json(["mdrl-portable-legacy-asset", row["recording_id"]])).hex
        assets[row["recording_id"]] = asset_id
        tables["recording_video_assets"].append({
            "asset_id": asset_id, "recording_id": row["recording_id"], "generation": 1, "is_current": 1,
            "path": row["output_path"], "size_bytes": row["size_bytes"], "mtime_ns": None, "sha256": "",
            "time_basis": "recording_start", "state": "unverified", "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
        tables["recording_video_protection"].append({
            "recording_id": row["recording_id"], "protected": 0, "revision": 1,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
    for event in tables["duel_events"]:
        tables["duel_event_sources"].append({
            "event_id": event["event_id"], "video_asset_id": assets.get(event["recording_id"]),
            "video_generation": 1, "time_basis": "recording_start", "input_fingerprint_json": "{}", "verified": 0,
        })


def _quarantine_imported_sources(tables: dict[str, list[dict[str, object]]]) -> None:
    for asset in tables["recording_video_assets"]:
        if asset["state"] != "deleted":
            asset["state"] = "unverified"
    for table in ("card_observation_links", "duel_event_sources"):
        for source in tables[table]:
            source["verified"] = 0
            proof = _json_column(source["input_fingerprint_json"])
            if proof.get("schema") == "mdrl-timeline-capture-v1":
                # 持込側がlocalを名乗っても、別DBのinodeを録画中の証拠にしません。
                proof["verification_origin"] = "portable_import"
                source["input_fingerprint_json"] = _json(proof)


def _insert_portable_rows(connection: sqlite3.Connection, tables: dict[str, list[dict[str, object]]]) -> None:
    for table in PORTABLE_TABLES:
        rows = tables[table]
        if rows:
            names = tuple(rows[0])
            placeholders = ",".join("?" for _ in names)
            connection.executemany(f"INSERT INTO {table} ({','.join(names)}) VALUES ({placeholders})",
                                   [tuple(row[name] for name in names) for row in rows])
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise PortableDataError("管理データJSONの参照関係が不正です")


def _validate_semantics(connection: sqlite3.Connection) -> None:
    inconsistent = connection.execute(
        "SELECT 1 FROM duel_card_openers opener LEFT JOIN duel_card_information information "
        "ON information.duel_id=opener.duel_id AND information.actor=opener.actor AND information.card_id=opener.card_id "
        "WHERE opener.status='confirmed' AND (information.state IS NULL OR information.state<>'used') LIMIT 1"
    ).fetchone()
    if inconsistent:
        raise PortableDataError("初動と対戦カード使用情報が矛盾しています")
    for table, owner in (("card_observation_links", "card_observations"), ("duel_event_sources", "duel_events")):
        mismatched = connection.execute(
            f"SELECT 1 FROM {table} source JOIN {owner} marker USING(event_id) "
            "JOIN recording_video_assets asset ON asset.asset_id=source.video_asset_id "
            "WHERE marker.recording_id<>asset.recording_id OR source.video_generation<>asset.generation "
            "OR source.time_basis<>asset.time_basis LIMIT 1"
        ).fetchone()
        if mismatched:
            raise PortableDataError("マーカーの動画・世代・時刻基準が一致しません")
        unbound = connection.execute(f"SELECT 1 FROM {owner} marker LEFT JOIN {table} source USING(event_id) WHERE source.event_id IS NULL LIMIT 1").fetchone()
        if unbound:
            raise PortableDataError("動画実体の参照を持たないマーカーがあります")
    cross_duel = connection.execute(
        "SELECT 1 FROM card_observation_links source JOIN card_observations marker USING(event_id) "
        "JOIN duel_card_information information USING(information_id) JOIN duel_records duel USING(duel_id) "
        "WHERE duel.recording_id IS NULL OR duel.recording_id<>marker.recording_id LIMIT 1"
    ).fetchone()
    if cross_duel:
        raise PortableDataError("カードマーカーが別の対戦情報を参照しています")
    wrong_upload = connection.execute(
        "SELECT 1 FROM video_deletion_history history JOIN youtube_uploads upload USING(upload_id) "
        "WHERE history.recording_id<>upload.recording_id LIMIT 1"
    ).fetchone()
    if wrong_upload:
        raise PortableDataError("削除履歴が別の録画の投稿情報を参照しています")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise PortableDataError("管理データJSONの整合性検査に失敗しました")


def _fingerprint(value: object) -> None:
    if value == {}:
        return
    required = {"path", "size_bytes", "mtime_ns", "sha256"}
    optional = {"schema", "recording_id", "asset_id", "generation", "time_basis", "session_id", "device", "inode",
                "verification_origin"}
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - required - optional:
        raise PortableDataError("動画の内容識別情報が不正です")
    _relative_path(value["path"])
    for key in ("size_bytes", "mtime_ns"):
        if isinstance(value[key], bool) or not isinstance(value[key], int) or value[key] < 0:
            raise PortableDataError("動画のサイズ・更新時刻が不正です")
    _sha(value["sha256"])
    for key in ("generation", "device", "inode"):
        if key in value and (isinstance(value[key], bool) or not isinstance(value[key], int) or value[key] < 0):
            raise PortableDataError("動画の世代・捕捉情報が不正です")
    for key in ("schema", "recording_id", "asset_id", "time_basis", "session_id"):
        if key in value and (not isinstance(value[key], str) or not value[key].strip()):
            raise PortableDataError("動画の時刻基準・捕捉識別が不正です")
    if "verification_origin" in value and value["verification_origin"] not in {"local_capture", "portable_import", "backup_restore"}:
        raise PortableDataError("動画の検証元が不正です")


def _sha(value: object) -> None:
    if not isinstance(value, str) or (value and (len(value) != 64 or any(char not in "0123456789abcdef" for char in value))):
        raise PortableDataError("動画のSHA-256が不正です")


def _portable_path(value: object, root: Path) -> str:
    if not isinstance(value, str):
        raise PortableDataError("動画パスが不正です")
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
        try:
            value = Path(value).resolve().relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise PortableDataError("保存先外の動画パスを持ち出せません") from exc
    return _relative_path(value)


def _relative_path(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise PortableDataError("動画の参照が不正です")
    posix, windows = PurePosixPath(value.replace("\\", "/")), PureWindowsPath(value)
    if (posix.is_absolute() or windows.is_absolute() or windows.drive or windows.root or ".." in posix.parts
            or not posix.parts or any(":" in part for part in posix.parts)):
        raise PortableDataError("動画は保存先内の相対パスで指定してください")
    return posix.as_posix()


def _timestamp(value: object, name: str) -> None:
    if not isinstance(value, str):
        raise PortableDataError(f"{name}の日時が不正です")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PortableDataError(f"{name}の日時が不正です") from exc
    if parsed.tzinfo is None:
        raise PortableDataError(f"{name}にはタイムゾーンが必要です")


def _json_column(value: object) -> object:
    if not isinstance(value, str):
        raise PortableDataError("管理データ内のJSON列が不正です")
    try:
        return json.loads(value)
    except ValueError as exc:
        raise PortableDataError("管理データ内のJSON列が不正です") from exc


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _check_structure(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.casefold() in _SECRET_KEYS:
                raise PortableDataError("管理データJSONに資格情報・実行情報を含められません")
            _check_structure(item)
    elif isinstance(value, list):
        for item in value:
            _check_structure(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise PortableDataError("管理データJSONに有限でない数値があります")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise PortableDataError("管理データJSONの値が不正です")


def _clear_execution_references(value: object) -> object:
    if isinstance(value, dict):
        return {key: (None if key in {"job_id", "prepare_queue_id"} else _clear_execution_references(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_clear_execution_references(item) for item in value]
    return value
