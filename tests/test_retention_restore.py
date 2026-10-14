"""隔離DBのバックアップ復元で、動画と実行指示を再生しないことを検証します。"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.card_analysis_models import CardInputFingerprint
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisRepository
from master_duel_recorder_lite.config import AppConfig, load_app_config, save_app_config
from master_duel_recorder_lite.data_protection import DataProtectionError, DataProtectionService
from master_duel_recorder_lite.history_database import HISTORY_DATABASE_NAME, _MIGRATIONS
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.youtube_uploads import YouTubeUploadState


class RetentionRestoreTest(unittest.TestCase):
    def setUp(self):
        import test_post_upload_cleanup as fixtures

        self.fixture = fixtures.PostUploadCleanupTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.paths = self.fixture.paths
        self.handler = self.fixture.real_handler()
        self.lifecycle = self.handler.lifecycle
        self.asset = self.lifecycle.ensure_current_asset("rec")
        self.protection = DataProtectionService(self.paths)

    def backup(self):
        return self.protection.create_backup("restore-fixture").path

    def database_rows(self, table):
        with closing(sqlite3.connect(self.protection.database_path)) as connection:
            return connection.execute(f"SELECT * FROM {table}").fetchall()

    def make_intent(self):
        stat = self.fixture.source.stat()
        digest = hashlib.sha256(self.fixture.source.read_bytes()).hexdigest()
        self.lifecycle.set_verified_asset(self.asset.asset_id, size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=digest)
        return self.lifecycle.create_intent(
            recording_id="rec", asset_id=self.asset.asset_id, generation=1, reason="manual",
            original_path="original.mkv", staged_path=".mdrl-video-staging/" + "b" * 32 + ".pending",
            size_bytes=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=digest,
            backup_path=str(self.paths.data / "fixture-original-backup"),
        )

    def test_current_pending_intent_rejects_restore_before_backup_or_replacement(self):
        backup = self.backup()
        self.make_intent()
        before = self.protection.database_path.read_bytes()
        with patch.object(self.protection, "create_backup") as safety:
            with self.assertRaisesRegex(DataProtectionError, "通常起動"):
                self.protection.restore(backup)
            safety.assert_not_called()
        self.assertEqual(self.protection.database_path.read_bytes(), before)
        self.assertTrue(self.fixture.source.exists())

    def test_old_backup_after_video_deletion_records_restored_deleted_not_later_cause(self):
        backup = self.backup()
        self.assertEqual(self.handler(self.fixture.upload, self.fixture.prepared, self.fixture.sent_result).status, "deleted")
        self.assertEqual(self.lifecycle.deletion_for_asset(self.asset.asset_id).reason, "youtube_uploaded")
        self.protection.restore(backup)
        self.assertFalse(self.fixture.source.exists())
        deletion = self.lifecycle.deletion_for_asset(self.asset.asset_id)
        self.assertEqual(deletion.reason, "restored_deleted")
        self.assertFalse(deletion.cause_known)
        self.assertFalse(deletion.metadata["original_deletion_time_known"])
        self.assertEqual(self.lifecycle.get_recording_video_state("rec").message, "復元時削除済み")
        before = self.protection.database_path.read_bytes()
        codes = {item.code for item in self.protection.diagnose().findings}
        self.assertIn("restored_deleted_recording", codes)
        self.assertNotIn("missing_recording", codes)
        self.assertEqual(self.protection.database_path.read_bytes(), before)

    def test_snapshot_known_cause_is_kept_with_original_timestamp(self):
        self.assertEqual(self.handler(self.fixture.upload, self.fixture.prepared, self.fixture.sent_result).status, "deleted")
        deletion = self.lifecycle.deletion_for_asset(self.asset.asset_id)
        backup = self.backup()
        self.protection.restore(backup)
        restored = self.lifecycle.deletion_for_asset(self.asset.asset_id)
        self.assertEqual((restored.reason, restored.deleted_at, restored.cause_known),
                         (deletion.reason, deletion.deleted_at, True))

    def test_unavailable_recordings_root_is_not_marked_deleted(self):
        backup = self.backup()
        # 同じ隔離runtime内の生成フォルダだけを移動して、保存先未接続を再現します。
        offline = self.paths.data / "offline-recordings"
        self.assertTrue(self.paths.recordings.resolve().is_relative_to(self.paths.root.resolve()))
        self.assertTrue(offline.resolve().is_relative_to(self.paths.root.resolve()))
        self.paths.recordings.rename(offline)
        self.protection.restore(backup)
        self.assertEqual(self.lifecycle.get_current_asset("rec").state, "unavailable")
        self.assertIsNone(self.lifecycle.deletion_for_asset(self.asset.asset_id))
        self.assertEqual((offline / "original.mkv").read_bytes(), b"original-video-data")

    def test_permission_unknown_is_not_deleted_and_video_is_unchanged(self):
        backup = self.backup()
        before = self.fixture.source.read_bytes()
        original_scandir = os.scandir

        def denied(path):
            if Path(path) == self.paths.recordings:
                raise PermissionError("fixture root inaccessible")
            return original_scandir(path)

        with patch("master_duel_recorder_lite.data_protection.os.scandir", side_effect=denied):
            self.protection.restore(backup)
        self.assertEqual(self.lifecycle.get_current_asset("rec").state, "unavailable")
        self.assertIsNone(self.lifecycle.deletion_for_asset(self.asset.asset_id))
        self.assertEqual(self.fixture.source.read_bytes(), before)

    def test_restored_on_settings_does_not_run_new_retention_or_post_upload_delete(self):
        save_app_config(paths=self.paths, config=replace(AppConfig(), recording_retention_days=1, upload_delete_source_after_upload=True))
        backup = self.backup()
        self.fixture.save_setting(False)
        with patch("master_duel_recorder_lite.startup_maintenance.run_startup_maintenance") as startup, patch(
            "master_duel_recorder_lite.recording_retention.RecordingDeletionService.delete_video"
        ) as delete:
            self.protection.restore(backup)
        self.assertTrue(load_app_config(user_data_dir=self.paths.root).config.upload_delete_source_after_upload)
        startup.assert_not_called()
        delete.assert_not_called()
        self.assertTrue(self.fixture.source.exists())
        self.assertEqual(self.lifecycle.pending_cleanup_requests(), ())
        self.assertEqual(self.lifecycle.get_current_asset("rec").state, "unverified")

    def test_snapshot_active_jobs_and_leases_are_quarantined_and_completed_url_is_kept(self):
        repository = CardAnalysisRepository.from_runtime_paths(self.paths)
        repository.save_target(name="fixture-card")
        stat = self.fixture.source.stat()
        fingerprint = CardInputFingerprint(str(self.fixture.source), stat.st_size, stat.st_mtime_ns,
                                           hashlib.sha256(self.fixture.source.read_bytes()).hexdigest())
        job = repository.create_job("rec", fingerprint=fingerprint, analyzer_version="fixture")
        repository.claim_job(job.job_id)
        use = self.lifecycle.register_use(asset_id=self.asset.asset_id, owner_id="snapshot-reader", purpose="review")
        active_upload = self.fixture.uploads.create(recording_id="rec", metadata=self.fixture.metadata, force_new=True)
        self.fixture.uploads.update(active_upload, state=YouTubeUploadState.UPLOADING)
        backup = self.backup()
        repository.cancel_job(job.job_id)
        self.lifecycle.release_use(use.use_id)
        self.fixture.uploads.update(active_upload, state=YouTubeUploadState.FAILED)
        self.protection.restore(backup)
        with closing(sqlite3.connect(self.protection.database_path)) as connection:
            self.assertEqual(connection.execute("SELECT state FROM card_analysis_jobs WHERE job_id=?", (job.job_id,)).fetchone()[0], "waiting")
            self.assertEqual(connection.execute("SELECT verified FROM card_analysis_job_sources WHERE job_id=?", (job.job_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM video_asset_uses").fetchone()[0], 0)
        self.assertEqual(self.fixture.uploads.get(active_upload.upload_id).state, YouTubeUploadState.FAILED)
        self.assertEqual(self.fixture.uploads.get(self.fixture.upload.upload_id).watch_url, "https://youtu.be/uploaded")
        self.assertTrue(self.fixture.source.exists())

    def test_snapshot_pending_intent_is_tagged_without_executing_files(self):
        intent = self.make_intent()
        backup = self.backup()
        self.lifecycle.update_intent(intent.intent_id, "cancelled")
        before = self.fixture.source.read_bytes()
        self.protection.restore(backup)
        restored = self.lifecycle.get_intent(intent.intent_id)
        self.assertEqual(restored.state, "intent")
        self.assertTrue(restored.metadata["restored_from_backup"])
        self.assertEqual(self.fixture.source.read_bytes(), before)
        self.assertEqual(self.lifecycle.deletion_for_asset(self.asset.asset_id), None)

    def test_restored_staged_snapshot_without_both_files_does_not_claim_original_cause(self):
        intent = self.make_intent()
        (self.paths.recordings / ".mdrl-video-staging").mkdir()
        self.lifecycle.update_intent(intent.intent_id, "staged")
        backup = self.backup()
        self.lifecycle.update_intent(intent.intent_id, "cancelled")
        self.fixture.source.unlink()
        self.protection.restore(backup)
        self.assertIsNone(self.lifecycle.deletion_for_asset(self.asset.asset_id))
        self.assertEqual(self.lifecycle.get_intent(intent.intent_id).state, "staged")
        recovered = self.handler.deletion.recover_pending()
        self.assertEqual(recovered[0].status, "skipped", recovered[0].message)
        deletion = self.lifecycle.deletion_for_asset(self.asset.asset_id)
        self.assertEqual(deletion.reason, "restored_deleted")
        self.assertFalse(deletion.cause_known)
        self.assertEqual(self.lifecycle.get_intent(intent.intent_id).state, "cancelled")

    def test_snapshot_pending_request_is_restored_without_consuming_it(self):
        use = self.lifecycle.register_use(asset_id=self.asset.asset_id, owner_id="reader", purpose="review")
        outcome = self.handler(self.fixture.upload, self.fixture.prepared, self.fixture.sent_result)
        self.assertEqual(outcome.status, "retry_pending")
        self.lifecycle.release_use(use.use_id)
        backup = self.backup()
        self.lifecycle.update_cleanup_request(outcome.retry_request_id, state="cancelled")
        self.protection.restore(backup)
        self.assertEqual(len(self.lifecycle.pending_cleanup_requests()), 1)
        self.assertTrue(self.fixture.source.exists())
        self.assertEqual(self.lifecycle.deletion_for_asset(self.asset.asset_id), None)

    def test_source_changed_since_snapshot_is_quarantined_not_deleted_or_reassigned(self):
        backup = self.backup()
        self.fixture.source.write_bytes(b"replacement-source-data")
        self.protection.restore(backup)
        self.assertEqual(self.lifecycle.get_current_asset("rec").state, "unverified")
        self.assertTrue(self.fixture.source.exists())
        self.assertIsNone(self.lifecycle.deletion_for_asset(self.asset.asset_id))

    def legacy_backup(self, version):
        import test_retention_schema_migration as legacy

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        paths = default_runtime_paths(user_data_dir=Path(temporary.name))
        ensure_runtime_dirs(paths)
        database = paths.db / HISTORY_DATABASE_NAME
        legacy.create_legacy(database, version)
        with closing(sqlite3.connect(database)) as connection, connection:
            legacy.recording(connection, "legacy")
            legacy.duel(connection, "legacy-duel", "legacy")
            if version == 18:
                legacy.target(connection, "card")
                legacy.observation(connection, "legacy-event", "legacy")
                legacy.opener(connection, "legacy", "legacy-event")
        return DataProtectionService(paths).create_backup("legacy-input").path

    def test_real_schema17_and_18_backups_are_migrated_protected_without_video_operations(self):
        for version in (17, 18):
            with self.subTest(version=version):
                backup = self.legacy_backup(version)
                (self.paths.recordings / "legacy.mkv").write_bytes(b"legacy-video")
                source_before = self.fixture.source.read_bytes()
                self.protection.restore(backup)
                with closing(sqlite3.connect(self.protection.database_path)) as connection:
                    self.assertEqual(connection.execute("SELECT version FROM schema_version").fetchone()[0], 19)
                    self.assertEqual(connection.execute("SELECT notes FROM duel_records WHERE duel_id='legacy-duel'").fetchone()[0], "keep notes")
                    if version == 18:
                        self.assertEqual(connection.execute("SELECT state FROM duel_card_information").fetchall(), [("used",)])
                        self.assertEqual(connection.execute("SELECT status FROM duel_card_openers").fetchone()[0], "confirmed")
                        self.assertEqual(connection.execute("SELECT verified FROM card_observation_links").fetchone()[0], 0)
                old_backups = [item for item in self.protection.list_backups()
                               if item.reason == f"pre-restore-migration-v{version}"]
                self.assertEqual(len(old_backups), 1)
                self.assertTrue(old_backups[0].protected)
                self.assertEqual(old_backups[0].schema_version, version)
                self.assertEqual(self.fixture.source.read_bytes(), source_before)

    def test_candidate_migration_failure_does_not_replace_current_database_or_config(self):
        backup = self.legacy_backup(18)
        original = _MIGRATIONS[19]

        def failed(connection):
            original(connection)
            raise RuntimeError("fixture restore migration failure")

        before_db = self.protection.database_path.read_bytes()
        before_config = self.protection.config_path.read_bytes()
        with patch.dict(_MIGRATIONS, {19: failed}):
            with self.assertRaisesRegex(DataProtectionError, "元データ"):
                self.protection.restore(backup)
        self.assertEqual(self.protection.database_path.read_bytes(), before_db)
        self.assertEqual(self.protection.config_path.read_bytes(), before_config)
        self.assertTrue(any(item.protected and item.schema_version == 18 for item in self.protection.list_backups()))

    def test_invalid_snapshot_config_is_rejected_before_replacing_current_values(self):
        self.protection.config_path.write_text('[upload]\ndelete_source_after_upload = "true"\n', encoding="utf-8")
        backup = self.backup()
        self.fixture.save_setting(False)
        before_db = self.protection.database_path.read_bytes()
        before_config = self.protection.config_path.read_bytes()
        with self.assertRaisesRegex(DataProtectionError, "元データ"):
            self.protection.restore(backup)
        self.assertEqual(self.protection.database_path.read_bytes(), before_db)
        self.assertEqual(self.protection.config_path.read_bytes(), before_config)

    def test_post_replace_failure_restores_current_upload_rows_and_settings_bytes(self):
        backup = self.backup()
        self.fixture.save_setting(False)
        with closing(sqlite3.connect(self.protection.database_path)) as connection, connection:
            connection.execute("UPDATE youtube_uploads SET title='current-title'")
        before_rows = self.database_rows("youtube_uploads")
        before_config = self.protection.config_path.read_bytes()
        original_replace = os.replace
        replaced = [False]

        def replace_file(source, target):
            result = original_replace(source, target)
            if Path(target) == self.protection.database_path and str(source).endswith(".restore"):
                replaced[0] = True
            return result

        from master_duel_recorder_lite import data_protection
        original_validate = data_protection._validate_database

        def validate(path):
            if replaced[0] and Path(path) == self.protection.database_path:
                raise DataProtectionError("fixture post-replace failure")
            return original_validate(path)

        with patch("master_duel_recorder_lite.data_protection.os.replace", side_effect=replace_file), patch(
            "master_duel_recorder_lite.data_protection._validate_database", side_effect=validate,
        ):
            with self.assertRaisesRegex(DataProtectionError, "元データ"):
                self.protection.restore(backup)
        self.assertEqual(self.database_rows("youtube_uploads"), before_rows)
        self.assertEqual(self.protection.config_path.read_bytes(), before_config)
        self.assertEqual(self.fixture.source.read_bytes(), b"original-video-data")


if __name__ == "__main__":
    unittest.main()
