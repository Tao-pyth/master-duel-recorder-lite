from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.history_database import (
    CURRENT_SCHEMA_VERSION,
    HistoryDatabaseError,
    _migrate_to_v1,
    _migrate_to_v2,
    _migrate_to_v3,
    _migrate_to_v4,
    _migrate_to_v5,
    _migrate_to_v6,
    _migrate_to_v7,
    _migrate_to_v8,
    initialize_history_database,
)


def create_version_zero_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE schema_version "
            "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_version(singleton, version) VALUES (1, 0)"
        )
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker(value) VALUES ('preserve-me')")


def create_version_six_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE schema_version "
            "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
        )
        connection.execute("INSERT INTO schema_version VALUES (1, 6)")
        for migration in (
            _migrate_to_v1,
            _migrate_to_v2,
            _migrate_to_v3,
            _migrate_to_v4,
            _migrate_to_v5,
            _migrate_to_v6,
        ):
            migration(connection)
        timestamp = "2026-08-12T00:00:00+00:00"
        connection.execute(
            "INSERT INTO recordings (recording_id, state, source, output_path, container, "
            "created_at, diagnostics_json, updated_at) VALUES "
            "('recording', 'failed', 'automatic', 'original.mkv', 'mkv', ?, '[]', ?)",
            (timestamp, timestamp),
        )
        connection.execute(
            "INSERT INTO recovery_artifacts "
            "(artifact_id, recording_id, kind, status, output_path, created_at, updated_at) "
            "VALUES ('artifact', 'recording', 'recovered', 'valid', "
            "'recovered/recovered.mkv', ?, ?)",
            (timestamp, timestamp),
        )


class HistoryDatabaseTest(unittest.TestCase):
    def test_version_six_removes_only_recovery_artifacts_after_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            recordings = root / "recordings"
            recovered = recordings / "recovered" / "recovered.mkv"
            original = recordings / "original.mkv"
            recovered.parent.mkdir(parents=True)
            recovered.write_bytes(b"recovered")
            original.write_bytes(b"original")
            path = root / "history.sqlite3"
            create_version_six_database(path)

            info = initialize_history_database(path, recordings_root=recordings)
            with closing(sqlite3.connect(path)) as connection:
                recovery_table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'recovery_artifacts'"
                ).fetchone()
                recording = connection.execute(
                    "SELECT output_path, failure_code FROM recordings"
                ).fetchone()
            original_exists = original.exists()
            recovered_exists = recovered.exists()

        self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
        self.assertIsNone(recovery_table)
        self.assertEqual(recording, ("original.mkv", None))
        self.assertTrue(original_exists)
        self.assertFalse(recovered_exists)

    def test_recovery_artifact_is_restored_when_migration_fails(self) -> None:
        def fail_v8(_connection: sqlite3.Connection) -> None:
            raise RuntimeError("injected failure")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            recordings = root / "recordings"
            recovered = recordings / "recovered" / "recovered.mkv"
            recovered.parent.mkdir(parents=True)
            recovered.write_bytes(b"recovered")
            path = root / "history.sqlite3"
            create_version_six_database(path)
            migrations = {
                1: _migrate_to_v1,
                2: _migrate_to_v2,
                3: _migrate_to_v3,
                4: _migrate_to_v4,
                5: _migrate_to_v5,
                6: _migrate_to_v6,
                7: _migrate_to_v7,
                8: fail_v8,
            }

            with self.assertRaises(HistoryDatabaseError):
                initialize_history_database(
                    path,
                    recordings_root=recordings,
                    migrations=migrations,
                )
            with closing(sqlite3.connect(path)) as connection:
                version = connection.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()[0]
                artifact = connection.execute(
                    "SELECT output_path FROM recovery_artifacts"
                ).fetchone()
            recovered_exists = recovered.exists()

        self.assertEqual(version, 6)
        self.assertEqual(artifact, ("recovered/recovered.mkv",))
        self.assertTrue(recovered_exists)

    def test_new_database_is_initialized_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "db" / "history.sqlite3"
            first = initialize_history_database(path)
            second = initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                version = connection.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()[0]
                recording_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'recordings'"
                ).fetchone()

        self.assertEqual(first.version, CURRENT_SCHEMA_VERSION)
        self.assertIsNone(first.backup_path)
        self.assertIsNone(second.backup_path)
        self.assertEqual(version, CURRENT_SCHEMA_VERSION)
        self.assertIsNotNone(recording_table)

    def test_existing_old_database_is_backed_up_before_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            create_version_zero_database(path)

            info = initialize_history_database(path)
            assert info.backup_path is not None
            with closing(sqlite3.connect(info.backup_path)) as backup:
                backup_version = backup.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()[0]
                marker = backup.execute("SELECT value FROM legacy_marker").fetchone()[0]
            backup_exists = info.backup_path.exists()

        self.assertTrue(backup_exists)
        self.assertEqual(backup_version, 0)
        self.assertEqual(marker, "preserve-me")

    def test_failed_migration_rolls_back_original_database(self) -> None:
        def fail_after_change(connection: sqlite3.Connection) -> None:
            connection.execute("CREATE TABLE must_rollback (value TEXT)")
            raise RuntimeError("injected failure")

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            create_version_zero_database(path)

            with self.assertRaises(HistoryDatabaseError):
                initialize_history_database(
                    path,
                    migrations={
                        1: lambda _connection: None,
                        2: lambda _connection: None,
                        3: lambda _connection: None,
                        4: fail_after_change,
                        5: lambda _connection: None,
                        6: lambda _connection: None,
                        7: lambda _connection: None,
                        8: lambda _connection: None,
                        9: lambda _connection: None,
                        10: lambda _connection: None,
                        11: lambda _connection: None,
                        12: lambda _connection: None,
                        13: lambda _connection: None,
                        14: lambda _connection: None,
                        15: lambda _connection: None,
                        16: lambda _connection: None,
                    },
                )

            with closing(sqlite3.connect(path)) as connection:
                version = connection.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()[0]
                rolled_back = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'must_rollback'"
                ).fetchone()
                marker = connection.execute(
                    "SELECT value FROM legacy_marker"
                ).fetchone()[0]
            backups = tuple(path.parent.glob("*.backup.sqlite3"))

        self.assertEqual(version, 0)
        self.assertIsNone(rolled_back)
        self.assertEqual(marker, "preserve-me")
        self.assertEqual(len(backups), 1)

    def test_newer_schema_is_rejected_without_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_version "
                    "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO schema_version(singleton, version) VALUES (1, ?)",
                    (CURRENT_SCHEMA_VERSION + 1,),
                )

            with self.assertRaisesRegex(HistoryDatabaseError, "未対応"):
                initialize_history_database(path)

            self.assertEqual(tuple(path.parent.glob("*.backup.sqlite3")), ())

    def test_current_version_without_required_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_version "
                    "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO schema_version(singleton, version) VALUES (1, ?)",
                    (CURRENT_SCHEMA_VERSION,),
                )

            with self.assertRaisesRegex(HistoryDatabaseError, "recordings"):
                initialize_history_database(path)

    def test_version_one_failed_record_keeps_failure_code_without_recovery_columns(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_version "
                    "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
                )
                connection.execute("INSERT INTO schema_version VALUES (1, 1)")
                _migrate_to_v1(connection)
                connection.execute(
                    """
                    INSERT INTO recordings (
                        recording_id, state, source, output_path, container,
                        created_at, error, diagnostics_json, updated_at
                    ) VALUES ('legacy', 'failed', 'manual', 'legacy.mkv', 'mkv',
                              '2026-08-08T00:00:00+00:00', 'old failure', '[]',
                              '2026-08-08T00:00:00+00:00')
                    """
                )

            info = initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    "SELECT failure_code FROM recordings"
                ).fetchone()

        self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(row, ("legacy_failure",))

    def test_version_two_is_backed_up_before_duel_record_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_version "
                    "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
                )
                connection.execute("INSERT INTO schema_version VALUES (1, 2)")
                _migrate_to_v1(connection)
                _migrate_to_v2(connection)

            info = initialize_history_database(path)
            assert info.backup_path is not None
            with closing(sqlite3.connect(info.backup_path)) as backup:
                backup_version = backup.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()[0]
                duel_table = backup.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'duel_records'"
                ).fetchone()

        self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(backup_version, 2)
        self.assertIsNone(duel_table)

    def test_version_three_preserves_duel_records_when_adding_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_version "
                    "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
                )
                connection.execute("INSERT INTO schema_version VALUES (1, 3)")
                _migrate_to_v1(connection)
                _migrate_to_v2(connection)
                _migrate_to_v3(connection)
                connection.execute(
                    """
                    INSERT INTO recordings (
                        recording_id, state, source, output_path, container,
                        created_at, diagnostics_json, updated_at
                    ) VALUES ('recording', 'completed', 'manual', 'recording.mkv', 'mkv',
                              '2026-08-09T00:00:00+00:00', '[]',
                              '2026-08-09T00:00:00+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO duel_records (
                        recording_id, status, result, play_order, own_deck,
                        opponent_deck, duel_type, notes, revision, created_at, updated_at
                    ) VALUES ('recording', 'draft', 'win', 'first', '', '', 'ranked', '', 1,
                              '2026-08-09T00:00:00+00:00',
                              '2026-08-09T00:00:00+00:00')
                    """
                )

            info = initialize_history_database(path)
            assert info.backup_path is not None
            with closing(sqlite3.connect(path)) as connection:
                result = connection.execute(
                    "SELECT result FROM duel_records WHERE recording_id = 'recording'"
                ).fetchone()[0]
                event_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'duel_events'"
                ).fetchone()
            with closing(sqlite3.connect(info.backup_path)) as backup:
                backup_version = backup.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()[0]
                backup_event_table = backup.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'duel_events'"
                ).fetchone()

        self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(result, "win")
        self.assertIsNotNone(event_table)
        self.assertEqual(backup_version, 3)
        self.assertIsNone(backup_event_table)

    def test_version_four_imports_existing_decks_and_tags_into_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_version "
                    "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
                )
                connection.execute("INSERT INTO schema_version VALUES (1, 4)")
                _migrate_to_v1(connection)
                _migrate_to_v2(connection)
                _migrate_to_v3(connection)
                _migrate_to_v4(connection)
                connection.execute(
                    """
                    INSERT INTO recordings (
                        recording_id, state, source, output_path, container,
                        created_at, diagnostics_json, updated_at
                    ) VALUES ('recording', 'completed', 'manual', 'recording.mkv', 'mkv',
                              '2026-08-09T00:00:00+00:00', '[]',
                              '2026-08-09T00:00:00+00:00')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO duel_records (
                        recording_id, status, result, play_order, own_deck,
                        opponent_deck, duel_type, notes, revision, created_at, updated_at
                    ) VALUES ('recording', 'draft', 'unknown', 'unknown', '青眼',
                              '烙印', 'ranked', '', 1,
                              '2026-08-09T00:00:00+00:00',
                              '2026-08-09T00:00:00+00:00')
                    """
                )
                connection.execute(
                    "INSERT INTO duel_record_tags VALUES ('recording', '大会', '大会')"
                )

            info = initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                entries = connection.execute(
                    "SELECT kind, name FROM duel_catalog_entries ORDER BY kind, name"
                ).fetchall()

        self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(entries, [("deck", "烙印"), ("deck", "青眼"), ("tag", "大会")])

    def test_version_eight_adds_unknown_coin_face_without_changing_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_version "
                    "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
                )
                connection.execute("INSERT INTO schema_version VALUES (1, 8)")
                for migration in (
                    _migrate_to_v1,
                    _migrate_to_v2,
                    _migrate_to_v3,
                    _migrate_to_v4,
                    _migrate_to_v5,
                    _migrate_to_v6,
                    _migrate_to_v7,
                    _migrate_to_v8,
                ):
                    migration(connection)
                connection.execute("PRAGMA user_version = 8")
                connection.execute(
                    "INSERT INTO recordings (recording_id, state, source, output_path, container, "
                    "created_at, diagnostics_json, updated_at) VALUES "
                    "('legacy', 'completed', 'manual', 'legacy.mkv', 'mkv', ?, '[]', ?)",
                    ("2026-08-13T00:00:00+00:00", "2026-08-13T00:00:00+00:00"),
                )
                connection.execute(
                    "INSERT INTO duel_records (recording_id, status, result, play_order, own_deck, "
                    "opponent_deck, duel_type, notes, revision, created_at, updated_at) VALUES "
                    "('legacy', 'confirmed', 'win', 'first', '', '', 'ranked', '', 1, ?, ?)",
                    ("2026-08-13T00:00:00+00:00", "2026-08-13T00:00:00+00:00"),
                )

            info = initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(duel_records)")}
                migrated = connection.execute(
                    "SELECT result, play_order, coin_face "
                    "FROM duel_records WHERE recording_id = 'legacy'"
                ).fetchone()
            assert info.backup_path is not None
            with closing(sqlite3.connect(info.backup_path)) as backup:
                backup_version = backup.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()[0]

        self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
        self.assertTrue({"duel_id", "coin_face"}.issubset(columns))
        self.assertNotIn("coin_toss_outcome", columns)
        self.assertEqual(migrated, ("win", "first", "unknown"))
        self.assertEqual(backup_version, 8)

    def test_version_five_adds_catalog_attributes_and_stable_tag_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_version "
                    "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), version INTEGER NOT NULL)"
                )
                connection.execute("INSERT INTO schema_version VALUES (1, 5)")
                _migrate_to_v1(connection)
                _migrate_to_v2(connection)
                _migrate_to_v3(connection)
                _migrate_to_v4(connection)
                _migrate_to_v5(connection)
                connection.execute(
                    "INSERT INTO recordings (recording_id, state, source, output_path, container, "
                    "created_at, diagnostics_json, updated_at) VALUES "
                    "('recording', 'completed', 'manual', 'recording.mkv', 'mkv', ?, '[]', ?)",
                    ("2026-08-11T00:00:00+00:00", "2026-08-11T00:00:00+00:00"),
                )
                connection.execute(
                    "INSERT INTO duel_records (recording_id, status, result, play_order, own_deck, "
                    "opponent_deck, duel_type, notes, revision, created_at, updated_at) VALUES "
                    "('recording', 'confirmed', 'win', 'first', '', '', 'ranked', '', 1, ?, ?)",
                    ("2026-08-11T00:00:00+00:00", "2026-08-11T00:00:00+00:00"),
                )
                connection.execute(
                    "INSERT INTO duel_record_tags VALUES ('recording', '大会', '大会')"
                )
                connection.execute(
                    "INSERT INTO duel_catalog_entries "
                    "(kind, name, normalized_name, created_at, updated_at) "
                    "VALUES ('tag', '大会', '大会', ?, ?)",
                    ("2026-08-11T00:00:00+00:00", "2026-08-11T00:00:00+00:00"),
                )

            info = initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                catalog = connection.execute(
                    "SELECT description, color, is_archived FROM duel_catalog_entries"
                ).fetchone()
                links = connection.execute(
                    "SELECT duel_id FROM duel_record_tag_links"
                ).fetchall()

        self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
        self.assertEqual(catalog, ("", None, 0))
        self.assertEqual(links, [("recording",)])


if __name__ == "__main__":
    unittest.main()
    def test_version_thirteen_removes_redundant_outcome_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "ALTER TABLE duel_records ADD COLUMN coin_toss_outcome TEXT NOT NULL DEFAULT 'unknown'"
                )
                connection.execute(
                    "CREATE INDEX duel_records_coin_toss_outcome_idx "
                    "ON duel_records(coin_toss_outcome)"
                )
                connection.execute(
                    "INSERT INTO saved_duel_filters "
                    "(filter_id, name, normalized_name, criteria_json, created_at, updated_at) "
                    "VALUES ('legacy', 'legacy', 'legacy', ?, 'now', 'now')",
                    (json.dumps({"coin_toss_outcome": "win", "coin_face": "heads"}),),
                )
                connection.execute(
                    "UPDATE schema_version SET version = 12 WHERE singleton = 1"
                )
                connection.execute("PRAGMA user_version = 12")

            info = initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(duel_records)")
                }
                indexes = {
                    row[1]
                    for row in connection.execute("PRAGMA index_list(duel_records)")
                }
                criteria = json.loads(
                    connection.execute(
                        "SELECT criteria_json FROM saved_duel_filters WHERE filter_id = 'legacy'"
                    ).fetchone()[0]
                )

        self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
        self.assertNotIn("coin_toss_outcome", columns)
        self.assertNotIn("duel_records_coin_toss_outcome_idx", indexes)
        self.assertEqual(criteria, {"coin_face": "heads"})
