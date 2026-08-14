from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from master_duel_recorder_lite.data_protection import (
    DATABASE_MEMBER,
    MANIFEST_MEMBER,
    DataProtectionError,
    DataProtectionService,
)
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


class DataProtectionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        ensure_runtime_dirs(self.paths)
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.duels = DuelRecordRepository.from_runtime_paths(self.paths)
        self.service = DataProtectionService(self.paths, max_generations=3)
        self.video = self.paths.recordings / "duel.mkv"
        self.video.write_bytes(b"large-video-is-not-backed-up")
        self.history.register_starting(
            recording_id="recording-1",
            output_path=self.video,
            container="mkv",
            source="manual",
            created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        self.duels.save(
            "recording-1",
            DuelRecordValues(status="confirmed", result="win"),
            expected_revision=0,
        )
        self.paths.config.mkdir(parents=True, exist_ok=True)
        (self.paths.config / "app.toml").write_text(
            '[recorder]\nframe_rate = 30\n', encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_backup_is_verified_and_excludes_recordings(self) -> None:
        result = self.service.create_backup("manual")

        with zipfile.ZipFile(result.path) as archive:
            names = set(archive.namelist())

        self.assertEqual(
            names, {"manifest.json", "data/history.sqlite3", "config/app.toml"}
        )
        self.assertNotIn("duel.mkv", names)
        self.assertEqual(self.service.preview_restore(result.path).backup_counts["duels"], 1)

    def test_backup_remains_consistent_while_an_uncommitted_write_exists(self) -> None:
        with closing(sqlite3.connect(self.service.database_path)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute(
                "UPDATE duel_records SET notes = ? WHERE recording_id = ?",
                ("not committed", "recording-1"),
            )

            backup = self.service.create_backup("concurrent-write")
            preview = self.service.preview_restore(backup.path)
            writer.rollback()

        self.assertEqual(preview.backup_counts["duels"], 1)

    def test_tampered_member_is_rejected_before_restore(self) -> None:
        backup = self.service.create_backup("manual").path
        with zipfile.ZipFile(backup) as archive:
            manifest = archive.read(MANIFEST_MEMBER)
            config = archive.read("config/app.toml")
        with zipfile.ZipFile(backup, "w") as archive:
            archive.writestr(MANIFEST_MEMBER, manifest)
            archive.writestr(DATABASE_MEMBER, b"not sqlite")
            archive.writestr("config/app.toml", config)

        with self.assertRaisesRegex(DataProtectionError, "サイズ|ハッシュ"):
            self.service.preview_restore(backup)
        self.assertIsNotNone(self.history.get("recording-1"))

    def test_preview_and_restore_replace_database_but_keep_video(self) -> None:
        backup = self.service.create_backup("manual").path
        self.duels.create_manual(
            DuelRecordValues(status="confirmed", result="loss"),
            occurred_at=datetime(2026, 8, 13, 1, tzinfo=timezone.utc),
        )
        preview = self.service.preview_restore(backup)
        self.assertEqual((preview.current_counts["duels"], preview.backup_counts["duels"]), (2, 1))

        restored = self.service.restore(backup)

        self.assertEqual(restored.backup_counts["duels"], 1)
        self.assertIsNotNone(DuelRecordRepository.from_runtime_paths(self.paths).get("recording-1"))
        self.assertTrue(self.video.is_file())

    def test_restore_failure_rolls_back_current_database(self) -> None:
        backup = self.service.create_backup("manual").path
        self.duels.save(
            "recording-1",
            DuelRecordValues(status="confirmed", result="loss"),
            expected_revision=1,
        )
        original_validate = __import__(
            "master_duel_recorder_lite.data_protection", fromlist=["_validate_database"]
        )._validate_database
        calls = [0]

        def fail_after_replace(path: Path):
            calls[0] += 1
            if calls[0] >= 5 and path.resolve() == self.service.database_path:
                raise DataProtectionError("injected post-replace failure")
            return original_validate(path)

        with patch(
            "master_duel_recorder_lite.data_protection._validate_database",
            side_effect=fail_after_replace,
        ):
            with self.assertRaisesRegex(DataProtectionError, "元データ"):
                self.service.restore(backup)

        current = DuelRecordRepository.from_runtime_paths(self.paths).get("recording-1")
        assert current is not None
        self.assertEqual(current.values.result, "loss")

    def test_rotation_keeps_protected_and_latest_generations(self) -> None:
        protected = self.service.create_backup("protected", protected=True)
        for index in range(5):
            self.service.create_backup(f"manual-{index}")

        backups = self.service.list_backups()

        self.assertLessEqual(len(backups), 3)
        self.assertIn(protected.path, {item.path for item in backups})

    def test_diagnosis_is_read_only_and_reports_missing_recording(self) -> None:
        before = self.service.database_path.read_bytes()
        self.video.unlink()

        report = self.service.diagnose()

        self.assertTrue(report.healthy)
        self.assertIn("missing_recording", {item.code for item in report.findings})
        self.assertEqual(self.service.database_path.read_bytes(), before)

    def test_diagnosis_reports_invalid_config_size_mismatch_and_untracked_video(self) -> None:
        with closing(sqlite3.connect(self.service.database_path)) as connection, connection:
            connection.execute(
                "UPDATE recordings SET size_bytes = ? WHERE recording_id = ?",
                (self.video.stat().st_size, "recording-1"),
            )
        before = self.service.database_path.read_bytes()
        self.video.write_bytes(b"changed")
        (self.paths.recordings / "untracked.mp4").write_bytes(b"video")
        (self.paths.config / "app.toml").write_text("[recorder\n", encoding="utf-8")

        report = self.service.diagnose()

        codes = {item.code for item in report.findings}
        self.assertTrue(
            {"invalid_config", "recording_size_mismatch", "untracked_recording"}.issubset(codes)
        )
        self.assertEqual(self.service.database_path.read_bytes(), before)

    def test_restore_failure_removes_config_created_during_failed_restore(self) -> None:
        backup = self.service.create_backup("manual").path
        self.config_path = self.paths.config / "app.toml"
        self.config_path.unlink()
        original_validate = __import__(
            "master_duel_recorder_lite.data_protection", fromlist=["_validate_database"]
        )._validate_database
        calls = [0]

        def fail_after_replace(path: Path):
            calls[0] += 1
            if calls[0] >= 5 and path.resolve() == self.service.database_path:
                raise DataProtectionError("injected post-replace failure")
            return original_validate(path)

        with patch(
            "master_duel_recorder_lite.data_protection._validate_database",
            side_effect=fail_after_replace,
        ):
            with self.assertRaisesRegex(DataProtectionError, "元データ"):
                self.service.restore(backup)

        self.assertFalse(self.config_path.exists())

    def test_atomic_publish_failure_leaves_no_partial_backup(self) -> None:
        with patch(
            "master_duel_recorder_lite.data_protection.os.replace",
            side_effect=PermissionError("injected permission failure"),
        ):
            with self.assertRaisesRegex(DataProtectionError, "作成できません"):
                self.service.create_backup("manual")

        self.assertEqual(tuple(self.service.backup_directory.glob("*")), ())

    def test_restore_does_not_start_when_safety_backup_fails(self) -> None:
        backup = self.service.create_backup("manual").path
        self.duels.save(
            "recording-1",
            DuelRecordValues(status="confirmed", result="loss"),
            expected_revision=1,
        )
        before = self.service.database_path.read_bytes()

        with patch.object(
            self.service,
            "create_backup",
            side_effect=DataProtectionError("injected disk full"),
        ):
            with self.assertRaisesRegex(DataProtectionError, "disk full"):
                self.service.restore(backup)

        self.assertEqual(self.service.database_path.read_bytes(), before)
        current = DuelRecordRepository.from_runtime_paths(self.paths).get("recording-1")
        assert current is not None
        self.assertEqual(current.values.result, "loss")

    def test_manifest_with_future_schema_is_rejected(self) -> None:
        backup = self.service.create_backup("manual").path
        with tempfile.TemporaryDirectory() as tmp_dir:
            extracted = Path(tmp_dir)
            with zipfile.ZipFile(backup) as archive:
                archive.extractall(extracted)
            database = extracted / DATABASE_MEMBER
            connection = sqlite3.connect(database)
            try:
                connection.execute("UPDATE schema_version SET version = 999")
                connection.commit()
            finally:
                connection.close()
            manifest_path = extracted / MANIFEST_MEMBER
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload = database.read_bytes()
            import hashlib

            manifest["schema_version"] = 999
            manifest["members"][DATABASE_MEMBER] = {
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(database, DATABASE_MEMBER)
                archive.write(extracted / "config/app.toml", "config/app.toml")
                archive.writestr(MANIFEST_MEMBER, json.dumps(manifest))

        with self.assertRaisesRegex(DataProtectionError, "復元できません"):
            self.service.preview_restore(backup)

    def test_runtime_repository_creates_verified_backup_before_schema_migration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir))
            ensure_runtime_dirs(paths)
            database = paths.db / "history.sqlite3"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    "CREATE TABLE schema_version (singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
                )
                connection.execute("INSERT INTO schema_version VALUES (1, 0)")

            RecordingHistoryRepository.from_runtime_paths(paths)
            backups = DataProtectionService(paths).list_backups()

            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].reason, "pre-migration-v0")
            self.assertEqual(backups[0].schema_version, 0)
            self.assertTrue(backups[0].protected)


if __name__ == "__main__":
    unittest.main()
