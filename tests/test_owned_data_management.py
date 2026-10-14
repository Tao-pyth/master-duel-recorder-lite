from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.data_management import ManagedDataError, ManagedDataService
from master_duel_recorder_lite.data_ownership import (
    DataOwnershipError, delete_duel_related, delete_recording_related, scoped_runtime_paths,
)
from master_duel_recorder_lite.data_protection import DataProtectionError, DataProtectionService
from master_duel_recorder_lite.duel_catalog import DuelCatalogRepository
from master_duel_recorder_lite.history_database import connect_history_database
from master_duel_recorder_lite.portable_data import FORBIDDEN_TABLES, LEGACY_SCHEMA, LEGACY_TABLES
from master_duel_recorder_lite.recording_history import RecordingHistoryError
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_queue import UploadQueueStore
import tests.test_portable_data as portable_fixture


STAMP = portable_fixture.STAMP


class OwnedDataManagementTest(unittest.TestCase):
    def setUp(self) -> None:
        # 元動画・DB・資格情報は全てこのfixtureのTemporaryDirectory配下だけです。
        self.fixture = portable_fixture.PortableDataTest("test_round_trip_preserves_semantics_urls_markers_reason_time_and_protection")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.paths = self.fixture.paths
        self.database = self.fixture.database
        self.service = ManagedDataService.from_runtime_paths(self.paths)
        self.export_path = self.paths.exports / "full.json"
        self.mp4 = self.paths.exports / "prepared.mp4"
        self.mp4.write_bytes(b"preserve prepared upload")
        self.clip = self.paths.exports / "clip.mp4"
        self.clip.write_bytes(b"preserve clip")
        self.config_path = self.paths.config / "app.toml"
        self.config_path.write_text("[storage]\nmax_gib=0\nretention_days=0\n", encoding="utf-8")
        self.queue = UploadQueueStore(self.paths)
        self.queue.enqueue(recording_id="video", metadata=UploadMetadata("keep queue"))
        self.queue_path = self.queue.path

    def snapshot(self) -> dict[str, list[tuple]]:
        with closing(connect_history_database(self.database)) as connection:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables}

    def assert_external_data_unchanged(self, video: bool = True) -> None:
        self.assertEqual(self.mp4.read_bytes(), b"preserve prepared upload")
        self.assertEqual(self.clip.read_bytes(), b"preserve clip")
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), "[storage]\nmax_gib=0\nretention_days=0\n")
        self.assertEqual(self.queue.list()[0].metadata.title, "keep queue")
        if video:
            self.assertEqual(self.fixture.video.read_bytes(), b"portable fixture video")

    def add_retry(self) -> None:
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute(
                "INSERT INTO upload_cleanup_requests "
                "(request_id,recording_id,asset_id,generation,upload_id,state,provenance_json,created_at,updated_at) "
                "VALUES ('retry','video',?,1,'uploaded','pending','{}',?,?)", (self.fixture.asset.asset_id, STAMP, STAMP),
            )

    def test_same_database_import_keeps_shared_configuration_and_clears_execution_requests(self) -> None:
        self.service.export_to(self.export_path)
        self.add_retry()
        before_shared = self.snapshot()
        before_review = self.fixture.cards.get_review(self.fixture.duel_id)
        result = self.service.import_from(self.export_path)
        after = self.snapshot()
        for table in ("card_identities", "card_targets", "card_target_identity_links"):
            self.assertEqual(after[table], before_shared[table])
        self.assertEqual(self.fixture.cards.get_review(self.fixture.duel_id), before_review)
        for table in FORBIDDEN_TABLES & after.keys():
            self.assertEqual(after[table], [])
        with closing(connect_history_database(self.database)) as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT verified FROM card_observation_links").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT watch_url FROM youtube_uploads WHERE upload_id='uploaded'").fetchone()[0],
                             "https://www.youtube.com/watch?v=portable1")
        self.assertIsNotNone(result.backup_path)
        self.assert_external_data_unchanged()

    def test_empty_database_import_and_legacy_import_keep_only_their_portable_information(self) -> None:
        self.service.export_to(self.export_path)
        self.service.reset("all")
        self.service.import_from(self.export_path)
        self.assertEqual(self.fixture.cards.get_review(self.fixture.duel_id).openers[0].status, "confirmed")
        payload = json.loads(self.export_path.read_text(encoding="utf-8"))
        legacy = {"schema": LEGACY_SCHEMA, "tables": {table: payload["tables"][table] for table in LEGACY_TABLES}}
        self.export_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        self.service.import_from(self.export_path)
        with closing(connect_history_database(self.database)) as connection:
            for table in ("duel_card_information", "duel_card_openers", "youtube_uploads", "video_deletion_history"):
                self.assertEqual(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM card_identities").fetchone()[0], 2)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assert_external_data_unchanged()

    def test_history_and_all_reset_clear_owned_data_and_keep_common_cards_and_files(self) -> None:
        self.service.export_to(self.export_path)
        for scope in ("history", "all"):
            with self.subTest(scope=scope):
                self.add_retry()
                shared = self.snapshot()
                self.service.reset(scope)
                after = self.snapshot()
                for table in ("recordings", "duel_records", "duel_card_information", "duel_card_openers",
                              "duel_card_information_changes", "duel_card_opener_changes", "duel_events", "duel_event_sources",
                              "card_observations", "card_observation_links", "card_analysis_jobs", "card_analysis_job_sources",
                              "youtube_uploads", "recording_video_assets", "recording_video_protection", "video_deletion_history",
                              "video_deletion_journal", "upload_cleanup_requests", "video_asset_uses"):
                    self.assertEqual(after[table], [])
                for table in ("card_identities", "card_targets", "card_target_identity_links"):
                    self.assertEqual(after[table], shared[table])
                self.assert_external_data_unchanged()
                self.service.import_from(self.export_path)

    def test_tag_and_deck_reset_handle_deck_tag_links_without_erasing_duel_cards(self) -> None:
        catalog = DuelCatalogRepository.from_runtime_paths(self.paths)
        deck = catalog.add_deck("デッキ")
        tag = catalog.add_tag("自由タグ")
        catalog.set_deck_tags(deck.entry_id, (tag.entry_id,))
        original = self.fixture.cards.get_review(self.fixture.duel_id)
        self.service.reset("tags")
        self.assertEqual(self.fixture.cards.get_review(self.fixture.duel_id), original)
        self.assertEqual(catalog.list_tags(), ())
        self.assertEqual(catalog.list_deck_tags(deck.entry_id), ())
        tag = catalog.add_tag("別タグ")
        catalog.set_deck_tags(deck.entry_id, (tag.entry_id,))
        self.service.reset("decks")
        self.assertEqual(self.fixture.cards.get_review(self.fixture.duel_id), original)
        self.assertEqual(catalog.list_decks(), ())
        self.assertEqual(len(catalog.list_tags()), 1)
        self.assert_external_data_unchanged()

    def test_manual_duel_delete_removes_only_its_canonical_children(self) -> None:
        recording_review = self.fixture.cards.get_review(self.fixture.duel_id)
        shared = self.snapshot()["card_identities"]
        deleted = self.fixture.duels.delete_manual(self.fixture.manual.duel_id)
        self.assertEqual(deleted.duel_id, self.fixture.manual.duel_id)
        self.assertIsNone(self.fixture.duels.get(self.fixture.manual.duel_id))
        self.assertEqual(self.fixture.cards.get_review(self.fixture.duel_id), recording_review)
        with closing(connect_history_database(self.database)) as connection:
            self.assertEqual([tuple(row) for row in connection.execute("SELECT * FROM card_identities")], shared)
            self.assertEqual(connection.execute("SELECT count(*) FROM duel_card_information WHERE duel_id=?", (deleted.duel_id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assert_external_data_unchanged()

    def test_production_import_remaps_colliding_card_and_target_ids_and_preserves_shared_settings(self) -> None:
        self.service.export_to(self.export_path)
        payload = json.loads(self.export_path.read_text(encoding="utf-8"))
        for table, key, identifier in (
            ("card_identities", "card_id", self.fixture.target.card_id),
            ("card_targets", "target_id", self.fixture.target.target_id),
        ):
            row = next(item for item in payload["tables"][table] if item[key] == identifier)
            row["name"] = "別の持込カード"
            row["normalized_name"] = "別の持込カード"
        self.export_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        existing = self.snapshot()
        result = self.service.import_from(self.export_path)
        card_id = result.id_maps["card_id"][self.fixture.target.card_id]
        target_id = result.id_maps["target_id"][self.fixture.target.target_id]
        self.assertNotEqual(card_id, self.fixture.target.card_id)
        self.assertNotEqual(target_id, self.fixture.target.target_id)
        with closing(connect_history_database(self.database)) as connection:
            for table, key in (("card_identities", "card_id"), ("card_targets", "target_id")):
                for row in existing[table]:
                    self.assertEqual(tuple(connection.execute(f"SELECT * FROM {table} WHERE {key}=?", (row[0],)).fetchone()), row)
            self.assertEqual(connection.execute("SELECT target_id FROM card_observations").fetchone()[0], target_id)
            self.assertEqual(connection.execute("SELECT card_id FROM duel_card_openers WHERE duel_id=?", (self.fixture.duel_id,)).fetchone()[0], card_id)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assert_external_data_unchanged()

    def test_import_does_not_overwrite_current_enabled_setting_or_unexported_shared_cards(self) -> None:
        self.service.export_to(self.export_path)
        extra = self.fixture.cards.save_identity(name="未持出しの共通カード")
        self.fixture.analysis.save_target(target_id=self.fixture.target.target_id, name=self.fixture.target.name,
                                          enabled=False, expected_revision=1)
        current_target = self.fixture.analysis.get_target(self.fixture.target.target_id)
        self.service.import_from(self.export_path)
        self.assertEqual(self.fixture.analysis.get_target(self.fixture.target.target_id), current_target)
        self.assertTrue(any(card.card_id == extra.card_id for card in self.fixture.cards.list_identities()))
        self.assertEqual(self.fixture.cards.get_review(self.fixture.duel_id).openers[0].status, "confirmed")

    def test_manual_duel_delete_transaction_failure_rolls_back_all_canonical_children(self) -> None:
        before = self.snapshot()

        def fail_after_delete(connection, identifier):
            delete_duel_related(connection, identifier)
            raise sqlite3.OperationalError("injected manual delete failure")

        with patch("master_duel_recorder_lite.data_ownership.delete_duel_related", side_effect=fail_after_delete):
            with self.assertRaisesRegex(RuntimeError, "manual delete failure"):
                self.fixture.duels.delete_manual(self.fixture.manual.duel_id)
        self.assertEqual(self.snapshot(), before)
        self.assert_external_data_unchanged()

    def test_protected_complete_delete_requires_explicit_allow_and_preserves_other_battles(self) -> None:
        before = self.snapshot()
        with self.assertRaisesRegex(RecordingHistoryError, "追加の削除確認"):
            self.fixture.history.delete("video")
        self.assertEqual(self.snapshot(), before)
        other = self.fixture.cards.get_review(self.fixture.manual.duel_id)
        self.add_retry()
        result = self.fixture.history.delete("video", allow_protected=True)
        self.assertEqual(result.deleted_files, (self.fixture.video,))
        self.assertFalse(self.fixture.video.exists())
        self.assertIsNone(self.fixture.history.get("video"))
        self.assertEqual(self.fixture.cards.get_review(self.fixture.manual.duel_id), other)
        self.assertIsNotNone(self.fixture.history.get("deleted"))
        with closing(connect_history_database(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM upload_cleanup_requests").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM card_analysis_jobs").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM youtube_uploads WHERE recording_id='video'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM card_targets").fetchone()[0], 2)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assert_external_data_unchanged(video=False)

    def test_complete_delete_holds_one_rotating_backup_until_database_commit(self) -> None:
        protection = DataProtectionService(self.paths)
        original = delete_recording_related
        def check_retention(connection, identifier):
            backups = protection.list_backups()
            self.assertEqual(len(backups), 1)
            self.assertFalse(backups[0].protected)
            with self.assertRaises(DataProtectionError):
                protection.create_backup("concurrent-rotation-attempt")
            self.assertTrue(backups[0].path.exists())
            return original(connection, identifier)
        with patch("master_duel_recorder_lite.data_ownership.delete_recording_related", side_effect=check_retention):
            self.fixture.history.delete("video", allow_protected=True)
        self.assertEqual(len(protection.list_backups()), 1)
        self.assertFalse(protection.list_backups()[0].protected)

    def test_complete_delete_when_backup_budget_is_full_keeps_video_and_information(self) -> None:
        protection = DataProtectionService(self.paths, max_generations=2)
        for identifier in ("one", "two"):
            protection.create_backup(f"protected-{identifier}", protected=True)
        before = self.snapshot()
        with patch("master_duel_recorder_lite.data_ownership.scoped_data_protection", return_value=protection):
            with self.assertRaisesRegex(RecordingHistoryError, "保持"):
                self.fixture.history.delete("video", allow_protected=True)
        self.assertEqual(self.snapshot(), before)
        self.assert_external_data_unchanged()

    def test_complete_delete_unlink_failure_restores_file_and_keeps_canonical_information(self) -> None:
        original_review = self.fixture.cards.get_review(self.fixture.duel_id)
        original_unlink = Path.unlink

        def fail_staged_unlink(path, *args, **kwargs):
            if path.parent.name == ".mdrl-video-staging" and path.suffix == ".pending":
                raise PermissionError("injected unlink failure")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", new=fail_staged_unlink):
            with self.assertRaisesRegex(RecordingHistoryError, "削除できません"):
                self.fixture.history.delete("video", allow_protected=True)
        self.assertIsNotNone(self.fixture.history.get("video"))
        self.assertEqual(self.fixture.cards.get_review(self.fixture.duel_id), original_review)
        with closing(connect_history_database(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM video_deletion_history WHERE recording_id='video'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM card_observations").fetchone()[0], 1)
        self.assert_external_data_unchanged()

    def test_complete_delete_backup_failure_or_shared_reference_keeps_file_and_all_data(self) -> None:
        before = self.snapshot()
        with patch("master_duel_recorder_lite.data_protection.DataProtectionService._create_backup",
                   side_effect=DataProtectionError("injected full delete backup failure")):
            with self.assertRaisesRegex(RecordingHistoryError, "backup failure"):
                self.fixture.history.delete("video", allow_protected=True)
        self.assertEqual(self.snapshot(), before)
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute("UPDATE recordings SET output_path='./duel.mkv' WHERE recording_id='deleted'")
        before = self.snapshot()
        with self.assertRaisesRegex(RecordingHistoryError, "同じ元動画"):
            self.fixture.history.delete("video", allow_protected=True)
        self.assertEqual(self.snapshot(), before)
        self.assert_external_data_unchanged()

    def test_complete_delete_database_failure_keeps_metadata_and_completed_manual_reason_for_retry(self) -> None:
        before_review = self.fixture.cards.get_review(self.fixture.duel_id)

        def fail_after_related(connection, identifier):
            delete_recording_related(connection, identifier)
            raise sqlite3.OperationalError("injected full delete transaction failure")

        with patch("master_duel_recorder_lite.data_ownership.delete_recording_related", side_effect=fail_after_related):
            with self.assertRaisesRegex(RecordingHistoryError, "元動画の削除は完了"):
                self.fixture.history.delete("video", allow_protected=True)
        self.assertFalse(self.fixture.video.exists())
        self.assertIsNotNone(self.fixture.history.get("video"))
        self.assertEqual(self.fixture.cards.get_review(self.fixture.duel_id), before_review)
        with closing(connect_history_database(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT reason FROM video_deletion_history WHERE recording_id='video'").fetchone()[0], "manual")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        result = self.fixture.history.delete("video", allow_protected=True)
        self.assertEqual(result.missing_files, (self.fixture.video,))
        self.assertIsNone(self.fixture.history.get("video"))
        self.assert_external_data_unchanged(video=False)

    def test_invalid_json_and_backup_failure_leave_database_and_files_unchanged(self) -> None:
        self.service.export_to(self.export_path)
        payload = json.loads(self.export_path.read_text(encoding="utf-8"))
        payload["tables"]["video_deletion_journal"] = []
        self.export_path.write_text(json.dumps(payload), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(ManagedDataError):
            self.service.import_from(self.export_path)
        self.assertEqual(self.snapshot(), before)
        with patch.object(self.service.protection, "_create_backup", side_effect=DataProtectionError("injected backup failure")):
            with self.assertRaisesRegex(ManagedDataError, "backup failure"):
                self.service.reset("history")
        self.assertEqual(self.snapshot(), before)
        self.assert_external_data_unchanged()

    def test_import_and_reset_database_failures_roll_back_all_related_changes(self) -> None:
        self.service.export_to(self.export_path)
        before = self.snapshot()
        insert = self.service._insert_rows

        def fail_insert(connection, table, rows):
            insert(connection, table, rows)
            if table == "duel_card_information":
                raise sqlite3.OperationalError("injected import failure")

        with patch.object(self.service, "_insert_rows", side_effect=fail_insert):
            with self.assertRaisesRegex(ManagedDataError, "import failure"):
                self.service.import_from(self.export_path)
        self.assertEqual(self.snapshot(), before)
        from master_duel_recorder_lite.data_ownership import clear_history_owned

        def fail_reset(connection):
            clear_history_owned(connection)
            raise sqlite3.OperationalError("injected reset failure")

        with patch("master_duel_recorder_lite.data_management.clear_history_owned", side_effect=fail_reset):
            with self.assertRaisesRegex(ManagedDataError, "reset failure"):
                self.service.reset("history")
        self.assertEqual(self.snapshot(), before)
        self.assert_external_data_unchanged()

    def test_helper_requires_transaction_and_pending_journal_cannot_be_discarded(self) -> None:
        with closing(connect_history_database(self.database)) as connection:
            with self.assertRaises(DataOwnershipError):
                delete_duel_related(connection, self.fixture.manual.duel_id)
            with connection:
                connection.execute(
                    "INSERT INTO video_deletion_journal "
                    "(intent_id,recording_id,asset_id,generation,reason,state,original_path,staged_path,size_bytes,"
                    "mtime_ns,sha256,backup_path,requested_at,updated_at) "
                    "VALUES ('pending','video',?,1,'manual','intent','duel.mkv','.mdrl-video-staging/pending.pending',"
                    "1,1,?,'backup',?,?)", (self.fixture.asset.asset_id, "a" * 64, STAMP, STAMP),
                )
            before = self.snapshot()
            with self.assertRaises(DataOwnershipError), connection:
                connection.execute("BEGIN IMMEDIATE")
                delete_recording_related(connection, "video")
        with self.assertRaisesRegex(ManagedDataError, "回復"):
            self.service.reset("all")
        self.assertEqual(self.snapshot(), before)
        self.assert_external_data_unchanged()

    def test_explicit_runtime_path_derivation_never_falls_back_to_real_default(self) -> None:
        paths = scoped_runtime_paths(self.database, recordings_root=self.paths.recordings)
        self.assertEqual(paths.root, self.paths.root)
        self.assertEqual(paths.recordings, self.paths.recordings)
        with self.assertRaises(DataOwnershipError):
            scoped_runtime_paths(self.database, recordings_root=Path(self.fixture.temp.name) / "outside-recordings")
        self.assertEqual(hashlib.sha256(self.mp4.read_bytes()).hexdigest(), hashlib.sha256(b"preserve prepared upload").hexdigest())


if __name__ == "__main__":
    unittest.main()
