from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from master_duel_recorder_lite.card_analysis_models import CardInputFingerprint, CardObservationValues
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisRepository
from master_duel_recorder_lite.data_protection import DataProtectionService
from master_duel_recorder_lite.history_database import (
    CURRENT_SCHEMA_VERSION, HISTORY_DATABASE_NAME, HistoryDatabaseError, _MIGRATIONS, initialize_history_database,
)
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


def create_schema17(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("CREATE TABLE schema_version (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version VALUES (1,17)")
        connection.execute("PRAGMA user_version=17")
        for version in range(1, 18):
            _MIGRATIONS[version](connection)
        connection.execute(
            "INSERT INTO recordings (recording_id,state,source,output_path,container,created_at,updated_at,duration_seconds) "
            "VALUES ('video','completed','manual','original.mkv','mkv',?,?,60)", (STAMP, STAMP),
        )
        connection.execute(
            "INSERT INTO duel_records (duel_id,recording_id,entry_origin,occurred_at,status,result,play_order,coin_face,"
            "duel_type,notes,revision,created_at,updated_at) "
            "VALUES ('duel','video','recording',?,'confirmed','win','first','heads','ranked','preserve notes',1,?,?)",
            (STAMP, STAMP, STAMP),
        )
        connection.execute(
            "INSERT INTO duel_events (event_id,recording_id,elapsed_ms,event_type,label,source,status,created_at,updated_at) "
            "VALUES ('marker','video',1000,'marker','preserve timeline','manual','confirmed',?,?)", (STAMP, STAMP),
        )


STAMP = "2026-10-01T00:00:00+00:00"


class CardAnalysisMigrationTest(unittest.TestCase):
    def test_migration_keeps_existing_rows_and_creates_verified_schema17_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_schema17(path)
            with closing(sqlite3.connect(path)) as connection:
                before = {table: connection.execute(f"SELECT * FROM {table}").fetchall()
                          for table in ("recordings", "duel_records", "duel_events")}
            migrated = initialize_history_database(path)
            self.assertEqual(migrated.version, CURRENT_SCHEMA_VERSION)
            self.assertTrue(migrated.backup_path.is_file())
            with closing(sqlite3.connect(path)) as connection:
                for table, rows in before.items():
                    self.assertEqual(connection.execute(f"SELECT * FROM {table}").fetchall(), rows)
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            with closing(sqlite3.connect(migrated.backup_path)) as connection:
                self.assertEqual(connection.execute("SELECT version FROM schema_version").fetchone()[0], 17)
                self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='card_targets'").fetchone())

    def test_backup_failure_does_not_begin_migration_or_modify_database_bytes(self) -> None:
        def fail_backup(_version):
            raise RuntimeError("injected backup failure")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_schema17(path)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(HistoryDatabaseError, "backup failure"):
                initialize_history_database(path, migration_backup_factory=fail_backup)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_partial_migration_and_legacy_edit_are_rolled_back_on_failure(self) -> None:
        def fail_migration(connection):
            _MIGRATIONS[18](connection)
            connection.execute("UPDATE duel_records SET notes='must roll back'")
            raise RuntimeError("injected migration failure")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_schema17(path)
            with self.assertRaisesRegex(HistoryDatabaseError, "migration failure"):
                initialize_history_database(path, migrations={**_MIGRATIONS, 18: fail_migration})
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT version FROM schema_version").fetchone()[0], 17)
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 17)
                self.assertEqual(connection.execute("SELECT notes FROM duel_records").fetchone()[0], "preserve notes")
                self.assertIsNone(connection.execute("SELECT 1 FROM sqlite_master WHERE name='card_targets'").fetchone())

    def test_protected_migration_backup_contains_original_config_and_legacy_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = default_runtime_paths(user_data_dir=Path(directory))
            ensure_runtime_dirs(paths)
            create_schema17(paths.db / HISTORY_DATABASE_NAME)
            config = paths.config / "app.toml"
            config.write_bytes(b"[recorder]\nframe_rate = 30\n")
            CardAnalysisRepository.from_runtime_paths(paths)
            protection = DataProtectionService(paths)
            backups = protection.list_backups()
            self.assertEqual(len(backups), 1)
            self.assertTrue(backups[0].protected)
            self.assertEqual(backups[0].schema_version, 17)
            self.assertEqual(protection.preview_restore(backups[0].path).backup_counts["duels"], 1)
            self.assertEqual(config.read_bytes(), b"[recorder]\nframe_rate = 30\n")

    def test_backup_restore_preserves_new_tables_confirmations_and_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = default_runtime_paths(user_data_dir=Path(directory))
            ensure_runtime_dirs(paths)
            history = RecordingHistoryRepository.from_runtime_paths(paths)
            video = paths.recordings / "original.mkv"
            video.write_bytes(b"preserve original video")
            history.register_starting(recording_id="video", output_path=video, container="mkv", source="manual",
                                      created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
            with closing(sqlite3.connect(history.database_path)) as connection, connection:
                connection.execute("UPDATE recordings SET state='completed',duration_seconds=60")
            repository = CardAnalysisRepository.from_runtime_paths(paths)
            target = repository.save_target(name="対象1")
            absent = repository.save_target(name="対象2")
            fingerprint = CardInputFingerprint(str(video), video.stat().st_size, video.stat().st_mtime_ns,
                                                hashlib.sha256(video.read_bytes()).hexdigest())
            job = repository.create_job("video", fingerprint=fingerprint, analyzer_version="test")
            repository.claim_job(job.job_id)
            values = CardObservationValues(target.target_id, target.name, "self", "normal_summon", 1000,
                                           evidence={"supports_use": True}, source_key="stable")
            repository.complete_job(job.job_id, (values,))
            row = repository.list_observations("video")[0]
            row = repository.review_observation(row.event_id, expected_revision=1, status="confirmed",
                                                 values=replace(values, actual_ms=750), is_opener=True)
            repository.set_nonuse("video", absent.target_id, "self")
            protection = DataProtectionService(paths)
            backup = protection.create_backup("card-analysis-test")
            counts = protection.preview_restore(backup.path).backup_counts
            self.assertEqual({key: counts[key] for key in (
                "card_targets", "card_analysis_jobs", "card_observations", "duel_card_information", "duel_card_openers"
            )}, {"card_targets": 2, "card_analysis_jobs": 1, "card_observations": 1,
                  "duel_card_information": 2, "duel_card_openers": 1})
            repository.reject_observation(row.event_id, expected_revision=row.revision)
            repository.save_target(target_id=target.target_id, name="changed after backup")
            protection.restore(backup.path)
            restored = CardAnalysisRepository.from_runtime_paths(paths).get_review("video")
            self.assertEqual(restored.observations, (replace(row, video_verified=False),))
            self.assertEqual(restored.openers[0].status, "confirmed")
            self.assertTrue(restored.nonuses[0].confirmed)
            self.assertEqual(video.read_bytes(), b"preserve original video")
            self.assertGreater(repository.verify_video_sources("video", fingerprint), 0)
            self.assertEqual(repository.get_review("video").observations, (row,))


if __name__ == "__main__":
    unittest.main()
