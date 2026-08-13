from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.duel_records import (
    DuelRecordConflictError,
    DuelRecordError,
    DuelRecordRepository,
    DuelRecordValues,
    duel_choice_label,
    duel_choice_labels,
    duel_choice_value,
)
from master_duel_recorder_lite.history_database import (
    CURRENT_SCHEMA_VERSION,
    initialize_history_database,
)
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository


class DuelRecordRepositoryTest(unittest.TestCase):
    def test_choice_labels_round_trip_between_internal_and_japanese_values(self) -> None:
        self.assertEqual(duel_choice_label("status", "draft"), "編集中")
        self.assertEqual(duel_choice_label("result", "win"), "勝ち")
        self.assertEqual(duel_choice_label("play_order", "first"), "先攻")
        self.assertEqual(duel_choice_label("coin_face", "heads"), "表")
        self.assertEqual(duel_choice_label("coin_toss_outcome", "loss"), "負け")
        self.assertEqual(duel_choice_label("duel_type", "ranked"), "ランク戦")
        self.assertEqual(duel_choice_value("result", "負け"), "loss")
        self.assertEqual(
            duel_choice_labels("play_order"),
            ("未設定", "先攻", "後攻"),
        )

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

    def test_incomplete_count_includes_only_completed_unconfirmed_recordings(self) -> None:
        history = RecordingHistoryRepository(
            database_path=self.database,
            recordings_root=self.recordings,
        )
        for recording_id in ("draft", "confirmed", "failed", "recording"):
            history.register_starting(
                recording_id=recording_id,
                output_path=self.recordings / f"{recording_id}.mkv",
                container="mkv",
                source="manual",
            )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "UPDATE recordings SET state = 'completed' "
                "WHERE recording_id IN ('recording-1', 'draft', 'confirmed')"
            )
            connection.execute(
                "UPDATE recordings SET state = 'failed' WHERE recording_id = 'failed'"
            )
            connection.execute(
                "UPDATE recordings SET state = 'recording' WHERE recording_id = 'recording'"
            )
        self.repository.save(
            "draft",
            DuelRecordValues(status="draft"),
            expected_revision=0,
        )
        self.repository.save(
            "confirmed",
            DuelRecordValues(status="confirmed"),
            expected_revision=0,
        )

        self.assertEqual(self.repository.count_incomplete_recordings(), 2)

        self.repository.save(
            "recording-1",
            DuelRecordValues(status="confirmed"),
            expected_revision=0,
        )
        self.assertEqual(self.repository.count_incomplete_recordings(), 1)

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

    def test_coin_face_outcome_and_play_order_are_independent_and_audited(self) -> None:
        saved = self.repository.save(
            "recording-1",
            DuelRecordValues(
                status="confirmed",
                play_order="second",
                coin_face="heads",
                coin_toss_outcome="loss",
            ),
            expected_revision=0,
        )

        self.assertEqual(saved.values.play_order, "second")
        self.assertEqual(saved.values.coin_face, "heads")
        self.assertEqual(saved.values.coin_toss_outcome, "loss")
        self.assertEqual(self.repository.changes("recording-1")[0].after["coin_face"], "heads")

    def test_manual_record_has_duel_id_without_recording_row_and_can_change_date(self) -> None:
        occurred_at = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
        saved = self.repository.create_manual(
            DuelRecordValues(status="confirmed", result="win", own_deck="青眼"),
            occurred_at=occurred_at,
        )

        self.assertEqual(saved.entry_origin, "manual")
        self.assertIsNone(saved.recording_id)
        self.assertEqual(saved.occurred_at, occurred_at)
        self.assertEqual(len(saved.duel_id), 32)
        with closing(sqlite3.connect(self.database)) as connection:
            recording_count = connection.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
        self.assertEqual(recording_count, 1)

        changed_at = occurred_at + timedelta(days=1)
        updated = self.repository.update(
            saved.duel_id,
            DuelRecordValues(**{**saved.values.__dict__, "result": "loss"}),
            expected_revision=saved.revision,
            occurred_at=changed_at,
        )
        self.assertEqual(updated.occurred_at, changed_at)
        self.assertEqual(updated.values.result, "loss")
        self.assertEqual(self.repository.get(saved.duel_id), updated)

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

        self.assertEqual(info.version, CURRENT_SCHEMA_VERSION)
        self.assertTrue(
            {"duel_records", "duel_record_tags", "duel_record_changes"}.issubset(tables)
        )


if __name__ == "__main__":
    unittest.main()
