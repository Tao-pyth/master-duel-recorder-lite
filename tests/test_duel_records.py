from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.duel_records import (
    DuelRecordConflictError,
    DuelRecordError,
    DuelRecordRepository,
    DuelRecordValues,
)
from master_duel_recorder_lite.history_database import initialize_history_database
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository


class DuelRecordRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.recordings = root / "recordings"
        self.recordings.mkdir()
        self.database = root / "history.sqlite3"
        history = RecordingHistoryRepository(
            database_path=self.database,
            recordings_root=self.recordings,
        )
        history.register_starting(
            recording_id="recording-1",
            output_path=self.recordings / "recording-1.mkv",
            container="mkv",
            source="manual",
        )
        self.repository = DuelRecordRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_draft_links_recording_and_writes_audit(self) -> None:
        record = self.repository.create_draft("recording-1")

        self.assertEqual(record.recording_id, "recording-1")
        self.assertEqual(record.values.status, "draft")
        self.assertEqual(record.revision, 1)
        changes = self.repository.changes("recording-1")
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].source, "system")
        self.assertNotIn("output_path", changes[0].after)

    def test_confirmed_record_can_be_edited_again(self) -> None:
        draft = self.repository.create_draft("recording-1")
        confirmed = self.repository.save(
            "recording-1",
            DuelRecordValues(
                status="confirmed",
                result="win",
                play_order="first",
                own_deck="青眼",
                opponent_deck="御巫",
                duel_type="ranked",
                tags=("昇格戦",),
                notes="後から修正可能",
            ),
            expected_revision=draft.revision,
        )
        edited = self.repository.save(
            "recording-1",
            DuelRecordValues(**{**confirmed.values.__dict__, "notes": "修正済み"}),
            expected_revision=confirmed.revision,
        )

        self.assertEqual(edited.values.status, "confirmed")
        self.assertEqual(edited.values.notes, "修正済み")
        self.assertEqual(edited.revision, 3)

    def test_stale_revision_is_rejected_without_overwrite(self) -> None:
        draft = self.repository.create_draft("recording-1")
        updated = self.repository.save(
            "recording-1",
            DuelRecordValues(result="win"),
            expected_revision=draft.revision,
        )

        with self.assertRaises(DuelRecordConflictError):
            self.repository.save(
                "recording-1",
                DuelRecordValues(result="loss"),
                expected_revision=draft.revision,
            )

        self.assertEqual(self.repository.get("recording-1"), updated)

    def test_tags_use_nfc_casefold_and_reject_duplicates(self) -> None:
        with self.assertRaisesRegex(ValueError, "重複"):
            self.repository.save(
                "recording-1",
                DuelRecordValues(tags=("TEST", "test")),
                expected_revision=0,
            )

        self.assertIsNone(self.repository.get("recording-1"))

    def test_audit_failure_rolls_back_record_and_tags(self) -> None:
        draft = self.repository.create_draft("recording-1")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER fail_audit BEFORE INSERT ON duel_record_changes "
                "BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
            )

        with self.assertRaises(DuelRecordError):
            self.repository.save(
                "recording-1",
                DuelRecordValues(result="loss", tags=("rollback",)),
                expected_revision=draft.revision,
            )

        unchanged = self.repository.get("recording-1")
        assert unchanged is not None
        self.assertEqual(unchanged.values.result, "unknown")
        self.assertEqual(unchanged.values.tags, ())
        self.assertEqual(unchanged.revision, 1)

    def test_unknown_recording_is_rejected(self) -> None:
        with self.assertRaisesRegex(DuelRecordError, "録画履歴"):
            self.repository.create_draft("missing")

    def test_detected_source_cannot_overwrite_existing_user_record(self) -> None:
        draft = self.repository.create_draft("recording-1")
        user_record = self.repository.save(
            "recording-1",
            DuelRecordValues(result="win"),
            expected_revision=draft.revision,
            source="user",
        )

        with self.assertRaisesRegex(DuelRecordConflictError, "自動判定"):
            self.repository.save(
                "recording-1",
                DuelRecordValues(result="loss"),
                expected_revision=user_record.revision,
                source="detected",
            )

        self.assertEqual(self.repository.get("recording-1"), user_record)


class DuelRecordMigrationTest(unittest.TestCase):
    def test_schema_three_contains_duel_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "history.sqlite3"
            info = initialize_history_database(path)
            with closing(sqlite3.connect(path)) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }

        self.assertEqual(info.version, 3)
        self.assertTrue(
            {"duel_records", "duel_record_tags", "duel_record_changes"}.issubset(tables)
        )


if __name__ == "__main__":
    unittest.main()
