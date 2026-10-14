from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.data_protection import (
    DataProtectionService,
    initialize_protected_history_database,
)
from master_duel_recorder_lite.history_database import (
    CURRENT_SCHEMA_VERSION,
    HistoryDatabaseError,
    _MIGRATIONS,
    initialize_history_database,
)
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.youtube_uploads import YouTubeUploadRepository


STAMP = "2026-10-01T00:00:00+00:00"
STARTED = "2026-10-01T00:01:00+00:00"


def create_legacy(path: Path, version: int = 18) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("CREATE TABLE schema_version (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (1,?)", (version,))
        connection.execute(f"PRAGMA user_version={version}")
        for number in range(1, version + 1):
            _MIGRATIONS[number](connection)


def recording(connection: sqlite3.Connection, identifier: str, *, has_started: bool = True) -> None:
    connection.execute(
        "INSERT INTO recordings "
        "(recording_id,state,source,output_path,container,created_at,started_at,updated_at,size_bytes) "
        "VALUES (?,'completed','manual',?,'mkv',?,?,?,12)",
        (identifier, f"{identifier}.mkv", STAMP, STARTED if has_started else None, STAMP),
    )


def duel(connection: sqlite3.Connection, identifier: str, recording_id: str | None = None) -> None:
    connection.execute(
        "INSERT INTO duel_records "
        "(duel_id,recording_id,entry_origin,occurred_at,status,result,play_order,coin_face,"
        "duel_type,notes,revision,created_at,updated_at) "
        "VALUES (?,?,?,?,'confirmed','win','first','heads','ranked','keep notes',1,?,?)",
        (identifier, recording_id, "recording" if recording_id else "manual", STAMP, STAMP, STAMP),
    )


def target(connection: sqlite3.Connection, identifier: str) -> None:
    connection.execute(
        "INSERT INTO card_targets VALUES (?,?,?,'[]','[]',1,1,?,?)",
        (identifier, f"カード{identifier}", f"card-{identifier}", STAMP, STAMP),
    )


def observation(
    connection: sqlite3.Connection, identifier: str, recording_id: str, *,
    target_id: str | None = "card", actor: str = "self", action: str = "activate",
    status: str = "confirmed", supports_use: bool = True, revision: int = 1,
) -> None:
    original = {"target_id": "before", "actor": "opponent", "action": "unknown"}
    connection.execute(
        "INSERT INTO card_observations VALUES (?,?,NULL,0,?,?,?, ?,1000,NULL,'log_observed',"
        "?,0.9,?,?,?,1,?,?,?)",
        (identifier, recording_id, target_id, "カードcard", actor, action,
         json.dumps({"supports_use": supports_use}), identifier, status, revision,
         json.dumps(original), STAMP, STAMP),
    )


def nonuse(connection: sqlite3.Connection, recording_id: str, target_id: str, *, confirmed: bool = True) -> None:
    connection.execute(
        "INSERT INTO card_nonuse_confirmations VALUES (?,?,'self',?,3,?)",
        (recording_id, target_id, int(confirmed), STAMP),
    )


def opener(connection: sqlite3.Connection, recording_id: str, event_id: str | None) -> None:
    connection.execute(
        "INSERT INTO card_opener_confirmations VALUES (?,'self',?,?,2,?)",
        (recording_id, event_id, "confirmed" if event_id else "unknown", STAMP),
    )


class RetentionSchemaMigrationTest(unittest.TestCase):
    def test_schema18_and_new_database_preserve_multiple_explicit_completed_uploads(self) -> None:
        for legacy in (True, False):
            with self.subTest(legacy=legacy), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "history.sqlite3"
                if legacy:
                    create_legacy(path)
                else:
                    initialize_history_database(path)
                with closing(sqlite3.connect(path)) as connection, connection:
                    recording(connection, "record")
                    connection.execute(
                        "INSERT INTO youtube_uploads "
                        "(upload_id,recording_id,state,privacy,title,video_id,watch_url,created_at,updated_at) "
                        "VALUES ('first','record','completed','private','first upload','id-first',"
                        "'https://www.youtube.com/watch?v=id-first',?,?)", (STAMP, STAMP),
                    )
                if legacy:
                    initialize_history_database(path)
                latest_stamp = "2026-10-02T00:00:00+00:00"
                with closing(sqlite3.connect(path)) as connection, connection:
                    connection.execute(
                        "INSERT INTO youtube_uploads "
                        "(upload_id,recording_id,state,privacy,title,video_id,watch_url,created_at,updated_at) "
                        "VALUES ('second','record','completed','private','explicit reupload','id-second',"
                        "'https://www.youtube.com/watch?v=id-second',?,?)", (latest_stamp, latest_stamp),
                    )
                    self.assertEqual(connection.execute("SELECT count(*) FROM youtube_uploads WHERE state='completed'").fetchone()[0], 2)
                    self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                repository = YouTubeUploadRepository(path)
                self.assertEqual(repository.latest_for_recording("record").upload_id, "second")

    def test_schema17_preserves_existing_data_and_creates_schema19_in_one_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path, 17)
            with closing(sqlite3.connect(path)) as connection, connection:
                recording(connection, "record")
                duel(connection, "duel", "record")
                before = connection.execute("SELECT * FROM duel_records").fetchall()
            info = initialize_history_database(path)
            self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
            self.assertTrue(info.backup_path.is_file())
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT * FROM duel_records").fetchall(), before)
                self.assertEqual(connection.execute("SELECT state,generation FROM recording_video_assets").fetchall(),
                                 [("unverified", 1)])
                self.assertEqual(connection.execute("SELECT protected FROM recording_video_protection").fetchall(), [(0,)])
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            with closing(sqlite3.connect(info.backup_path)) as connection:
                self.assertEqual(connection.execute("SELECT version FROM schema_version").fetchone()[0], 17)

    def test_schema18_keeps_usage_nonuse_opener_and_human_correction_without_marker_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                recording(connection, "record")
                duel(connection, "duel", "record")
                target(connection, "card")
                target(connection, "absent")
                observation(connection, "use1", "record", revision=4)
                observation(connection, "use2", "record")
                nonuse(connection, "record", "absent")
                opener(connection, "record", "use1")
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                self.assertEqual(connection.execute(
                    "SELECT duel_id,card_id,actor,state FROM duel_card_information ORDER BY card_id"
                ).fetchall(), [("duel", "absent", "self", "not_used"), ("duel", "card", "self", "used")])
                information = connection.execute(
                    "SELECT information_id,revision FROM duel_card_information WHERE state='used'"
                ).fetchone()
                self.assertEqual(information[1], 4)
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM card_observation_links WHERE information_id=?", (information[0],)
                ).fetchone()[0], 2)
                changes = json.loads(connection.execute(
                    "SELECT before_json FROM duel_card_information_changes WHERE information_id=?", (information[0],)
                ).fetchone()[0])
                self.assertEqual(changes["legacy"][0]["original"]["actor"], "opponent")
                self.assertEqual(connection.execute(
                    "SELECT card_id,status,revision FROM duel_card_openers"
                ).fetchall(), [("card", "confirmed", 2)])
                # 元の観測を全部消しても使用・初動の正本へ影響しません。
                connection.execute("DELETE FROM card_observations")
                connection.execute("DELETE FROM card_analysis_jobs")
                self.assertEqual(connection.execute("SELECT count(*) FROM card_observation_links").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM duel_card_information").fetchone()[0], 2)
                self.assertEqual(connection.execute("SELECT card_id FROM duel_card_openers").fetchone()[0], "card")
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertNotIn("card_nonuse_confirmations", tables)
                self.assertNotIn("card_opener_confirmations", tables)

    def test_confirmed_display_candidates_rejected_and_unknown_action_never_become_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                target(connection, "card")
                for identifier, values in (
                    ("display", {"supports_use": False}),
                    ("candidate", {"status": "candidate"}),
                    ("rejected", {"status": "rejected"}),
                    ("unknown-action", {"action": "unknown"}),
                ):
                    recording(connection, identifier)
                    observation(connection, identifier, identifier, **values)
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM duel_card_information").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM duel_records").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT count(*) FROM card_observations").fetchone()[0], 4)
                self.assertEqual(connection.execute(
                    "SELECT source_id,reason FROM card_migration_review_items ORDER BY source_id"
                ).fetchall(), [("display", "confirmed_observation_without_usage"),
                              ("unknown-action", "confirmed_observation_without_usage")])

    def test_unknown_actor_and_unmapped_card_are_retained_for_review_without_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                target(connection, "card")
                recording(connection, "unknown")
                observation(connection, "unknown", "unknown", actor="unknown")
                recording(connection, "unmapped")
                observation(connection, "unmapped", "unmapped", target_id=None)
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT actor,state FROM duel_card_information").fetchall(),
                                 [("unknown", "used")])
                reviews = connection.execute(
                    "SELECT reason,data_json,duel_id FROM card_migration_review_items ORDER BY reason"
                ).fetchall()
                self.assertEqual([row[0] for row in reviews], ["unknown_actor", "unmapped_card"])
                self.assertTrue(all(row[2] for row in reviews))
                self.assertIsNone(json.loads(reviews[1][1])["target_id"])
                self.assertEqual(connection.execute("SELECT count(*) FROM card_identities").fetchone()[0], 1)

    def test_missing_battles_receive_only_one_draft_with_unknown_fields_and_timestamp_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                target(connection, "card")
                recording(connection, "used")
                observation(connection, "use1", "used")
                observation(connection, "use2", "used")
                opener(connection, "used", "use1")
                recording(connection, "nonuse", has_started=False)
                nonuse(connection, "nonuse", "card")
                recording(connection, "candidate")
                observation(connection, "candidate", "candidate", status="candidate")
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                before = connection.execute("SELECT * FROM duel_records ORDER BY recording_id").fetchall()
                self.assertEqual(connection.execute(
                    "SELECT recording_id,status,result,play_order,coin_face,own_deck,opponent_deck,notes,occurred_at "
                    "FROM duel_records ORDER BY recording_id"
                ).fetchall(), [("nonuse", "draft", "unknown", "unknown", "unknown", "", "", "", STAMP),
                              ("used", "draft", "unknown", "unknown", "unknown", "", "", "", STARTED)])
            info = initialize_history_database(path)
            self.assertIsNone(info.backup_path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT * FROM duel_records ORDER BY recording_id").fetchall(), before)

    def test_invalid_opener_and_contradictory_nonuse_keep_original_information_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                target(connection, "card")
                recording(connection, "record")
                duel(connection, "duel", "record")
                observation(connection, "used", "record")
                observation(connection, "display", "record", supports_use=False)
                nonuse(connection, "record", "card")
                opener(connection, "record", "display")
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT state FROM duel_card_information").fetchall(), [("used",)])
                self.assertEqual(connection.execute("SELECT status,card_id FROM duel_card_openers").fetchall(),
                                 [("unknown", None)])
                reasons = {row[0] for row in connection.execute("SELECT reason FROM card_migration_review_items")}
                self.assertIn("usage_nonuse_conflict", reasons)
                self.assertIn("invalid_opener_reference", reasons)

    def test_explicit_unknown_nonuse_and_opener_remain_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                target(connection, "card")
                recording(connection, "record")
                duel(connection, "duel", "record")
                nonuse(connection, "record", "card", confirmed=False)
                opener(connection, "record", None)
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT state,confirmed_at FROM duel_card_information").fetchall(),
                                 [("unknown", None)])
                self.assertEqual(connection.execute("SELECT status,card_id FROM duel_card_openers").fetchall(),
                                 [("unknown", None)])

    def test_original_job_fingerprint_is_unverified_snapshot_and_not_asset_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            fingerprint = json.dumps({"path": "old-path.mkv", "size_bytes": 14, "mtime_ns": 99, "sha256": "a" * 64})
            with closing(sqlite3.connect(path)) as connection, connection:
                recording(connection, "record")
                connection.execute(
                    "INSERT INTO card_analysis_jobs VALUES ('job','record',1,'completed',?,'[]','test',2,1,'','{}',?,?)",
                    (fingerprint, STAMP, STAMP),
                )
                target(connection, "card")
                observation(connection, "use", "record")
                connection.execute("UPDATE card_observations SET job_id='job'")
                connection.execute(
                    "INSERT INTO duel_events "
                    "(event_id,recording_id,elapsed_ms,event_type,label,source,status,created_at,updated_at) "
                    "VALUES ('general','record',900,'marker','keep marker','manual','confirmed',?,?)", (STAMP, STAMP),
                )
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT sha256,mtime_ns,state FROM recording_video_assets").fetchone(),
                                 ("", None, "unverified"))
                for table in ("card_analysis_job_sources", "card_observation_links"):
                    self.assertEqual(connection.execute(f"SELECT input_fingerprint_json,verified FROM {table}").fetchone(),
                                     (fingerprint, 0))
                self.assertEqual(connection.execute("SELECT video_generation,verified FROM duel_event_sources").fetchone(), (1, 0))

    def test_backup_failure_and_nonexistent_backup_never_start_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(HistoryDatabaseError, "保持"):
                initialize_history_database(path, migration_backup_factory=lambda _: Path(directory) / "missing.backup")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            with self.assertRaisesRegex(HistoryDatabaseError, "backup failure"):
                initialize_history_database(path, migration_backup_factory=lambda _: self._fail_backup())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            corrupt = Path(directory) / "bad.backup"
            corrupt.write_bytes(b"not a usable SQLite backup")
            with self.assertRaisesRegex(HistoryDatabaseError, "バックアップ"):
                initialize_history_database(path, migration_backup_factory=lambda _: corrupt)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_protected_schema18_backup_keeps_config_and_legacy_confirmations_before_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = default_runtime_paths(user_data_dir=Path(directory))
            ensure_runtime_dirs(paths)
            path = paths.db / "history.sqlite3"
            create_legacy(path)
            config_path = paths.config / "app.toml"
            config_path.write_bytes(b"[recorder]\nframe_rate=30\n")
            with closing(sqlite3.connect(path)) as connection, connection:
                recording(connection, "record")
                target(connection, "card")
                observation(connection, "used", "record")
                opener(connection, "record", "used")
            info = initialize_protected_history_database(paths)
            protection = DataProtectionService(paths)
            backup = protection.inspect_backup(info.backup_path)
            self.assertTrue(backup.protected)
            self.assertEqual(backup.schema_version, 18)
            self.assertEqual(protection.preview_restore(backup.path).backup_counts["card_opener_confirmations"], 1)
            self.assertEqual(config_path.read_bytes(), b"[recorder]\nframe_rate=30\n")
            self.assertEqual(info.version, 19)

    def test_confirmed_statistical_membership_and_opener_groups_are_identical_after_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.row_factory = sqlite3.Row
                target(connection, "card")
                for identifier in ("used", "unused", "display", "candidate", "unknown-actor"):
                    recording(connection, identifier)
                    duel(connection, f"duel-{identifier}", identifier)
                observation(connection, "used1", "used")
                observation(connection, "used2", "used")
                observation(connection, "display", "display", supports_use=False)
                observation(connection, "candidate", "candidate", status="candidate")
                observation(connection, "unknown-actor", "unknown-actor", actor="unknown")
                nonuse(connection, "unused", "card")
                opener(connection, "used", "used1")
                before_used = {
                    row["duel_id"] for row in connection.execute(
                        "SELECT duels.duel_id,observations.action,observations.evidence_json "
                        "FROM card_observations observations JOIN duel_records duels "
                        "ON duels.recording_id=observations.recording_id "
                        "WHERE observations.status='confirmed' AND observations.actor='self' "
                        "AND observations.target_id='card'"
                    ) if row["action"] != "unknown" and json.loads(row["evidence_json"]).get("supports_use") is True
                }
                before_nonuse = {row[0] for row in connection.execute(
                    "SELECT duels.duel_id FROM card_nonuse_confirmations nonuses JOIN duel_records duels "
                    "ON duels.recording_id=nonuses.recording_id WHERE nonuses.confirmed=1 "
                    "AND nonuses.actor='self' AND nonuses.target_id='card'"
                )} - before_used
                before_openers = {tuple(row) for row in connection.execute(
                    "SELECT duels.duel_id,observations.target_id FROM card_opener_confirmations openers "
                    "JOIN duel_records duels ON duels.recording_id=openers.recording_id "
                    "JOIN card_observations observations ON observations.event_id=openers.event_id "
                    "WHERE openers.status='confirmed' AND openers.actor='self'"
                )}
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                after_used = {row[0] for row in connection.execute(
                    "SELECT duel_id FROM duel_card_information WHERE card_id='card' AND actor='self' AND state='used'"
                )}
                after_nonuse = {row[0] for row in connection.execute(
                    "SELECT duel_id FROM duel_card_information WHERE card_id='card' AND actor='self' AND state='not_used'"
                )}
                after_openers = {tuple(row) for row in connection.execute(
                    "SELECT duel_id,card_id FROM duel_card_openers WHERE actor='self' AND status='confirmed'"
                )}
                self.assertEqual((before_used, before_nonuse, before_openers),
                                 (after_used, after_nonuse, after_openers))
                self.assertEqual(len(after_used), 1)
                self.assertEqual(len(after_nonuse), 1)

    @staticmethod
    def _fail_backup() -> Path:
        raise RuntimeError("injected backup failure")

    def test_every_failure_boundary_restores_legacy_ddl_data_and_schema_version(self) -> None:
        for boundary in ("ddl", "data", "validation"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "history.sqlite3"
                create_legacy(path)
                with closing(sqlite3.connect(path)) as connection, connection:
                    recording(connection, "record")
                    target(connection, "card")
                    observation(connection, "used", "record")
                before = hashlib.sha256(path.read_bytes()).hexdigest()

                def fail_ddl(connection):
                    connection.execute("CREATE TABLE partial_ddl (value TEXT)")
                    raise RuntimeError("injected ddl failure")

                def fail_data(connection):
                    _MIGRATIONS[19](connection)
                    connection.execute("UPDATE recordings SET source='must rollback'")
                    raise RuntimeError("injected data failure")

                replacement = fail_ddl if boundary == "ddl" else fail_data
                if boundary == "validation":
                    with patch("master_duel_recorder_lite.history_database._validate_current_schema",
                               side_effect=HistoryDatabaseError("injected validation failure")):
                        with self.assertRaises(HistoryDatabaseError):
                            initialize_history_database(path)
                else:
                    with self.assertRaisesRegex(HistoryDatabaseError, f"{boundary} failure"):
                        initialize_history_database(path, migrations={**_MIGRATIONS, 19: replacement})
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(connection.execute("SELECT version FROM schema_version").fetchone()[0], 18)
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 18)
                    self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='duel_card_information'").fetchone())
                    self.assertEqual(connection.execute("SELECT count(*) FROM duel_records").fetchone()[0], 0)
                    self.assertIsNotNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='card_opener_confirmations'").fetchone())

    def test_restoring_legacy_backup_and_migrating_again_produces_identical_draft_and_information_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                recording(connection, "record")
                target(connection, "card")
                observation(connection, "used", "record")
            info = initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                before = (connection.execute("SELECT duel_id FROM duel_records").fetchall(),
                          connection.execute("SELECT information_id FROM duel_card_information").fetchall())
            shutil.copyfile(info.backup_path, path)
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual((connection.execute("SELECT duel_id FROM duel_records").fetchall(),
                                  connection.execute("SELECT information_id FROM duel_card_information").fetchall()), before)

    def test_cards_and_opener_can_exist_for_manual_battle_with_zero_videos_jobs_and_markers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                duel(connection, "manual")
                connection.execute("INSERT INTO card_identities VALUES ('card','カード','card',?,?)", (STAMP, STAMP))
                connection.execute(
                    "INSERT INTO duel_card_information VALUES ('info','manual','card','self','used','manual',1,?,?,?)",
                    (STAMP, STAMP, STAMP),
                )
                connection.execute(
                    "INSERT INTO duel_card_openers VALUES ('manual','self','card','confirmed','manual',1,?,?,?)",
                    (STAMP, STAMP, STAMP),
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                for table in ("recordings", "recording_video_assets", "card_targets", "card_observations", "card_analysis_jobs"):
                    self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)

    def test_analysis_target_deletion_keeps_independent_card_identity_and_duel_information(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                recording(connection, "record")
                target(connection, "card")
                observation(connection, "used", "record")
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("DELETE FROM card_observations")
                connection.execute("DELETE FROM card_targets")
                self.assertEqual(connection.execute("SELECT count(*) FROM card_target_identity_links").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT card_id FROM card_identities").fetchone()[0], "card")
                self.assertEqual(connection.execute("SELECT state FROM duel_card_information").fetchone()[0], "used")

    def test_relinked_video_keeps_old_asset_audit_and_enforces_single_current_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_legacy(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                recording(connection, "record")
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("UPDATE recording_video_assets SET is_current=0")
                connection.execute(
                    "INSERT INTO recording_video_assets "
                    "(asset_id,recording_id,generation,is_current,path,created_at,updated_at) "
                    "VALUES ('new','record',2,1,'new.mkv',?,?)", (STAMP, STAMP),
                )
                self.assertEqual(connection.execute("SELECT generation,is_current FROM recording_video_assets ORDER BY generation").fetchall(),
                                 [(1, 0), (2, 1)])
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO recording_video_assets "
                        "(asset_id,recording_id,generation,is_current,path,created_at,updated_at) "
                        "VALUES ('bad','record',3,1,'bad.mkv',?,?)", (STAMP, STAMP),
                    )

    def test_schema_validation_rejects_orphan_foreign_keys_in_existing_v19(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("INSERT INTO card_target_identity_links VALUES ('missing-target','missing-card')")
            with self.assertRaisesRegex(HistoryDatabaseError, "外部キー"):
                initialize_history_database(path)


if __name__ == "__main__":
    unittest.main()
