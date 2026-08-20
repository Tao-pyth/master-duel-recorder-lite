from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from master_duel_recorder_lite.duel_catalog import DuelCatalogRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.duel_workflow import (
    BulkDuelUpdate,
    DuelFilterCriteria,
    DuelWorkflowError,
    DuelWorkflowService,
)
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.runtime_paths import default_runtime_paths
from master_duel_recorder_lite.seasons import SeasonRepository


class DuelWorkflowServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.records = DuelRecordRepository.from_runtime_paths(self.paths)
        self.catalog = DuelCatalogRepository.from_runtime_paths(self.paths)
        self.seasons = SeasonRepository.from_runtime_paths(self.paths)
        self.service = DuelWorkflowService.from_runtime_paths(self.paths)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _recording(self, identifier: str, *, state: str = "completed") -> None:
        self.history.register_starting(
            recording_id=identifier,
            output_path=self.paths.recordings / f"{identifier}.mkv",
            container="mkv",
            source="manual",
            created_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        with closing(sqlite3.connect(self.paths.db / "history.sqlite3")) as connection, connection:
            connection.execute(
                "UPDATE recordings SET state = ? WHERE recording_id = ?", (state, identifier)
            )

    def test_suggestion_uses_latest_confirmed_record_active_season_and_frequency(self) -> None:
        frequent = self.catalog.add_deck("頻出")
        self.catalog.add_deck("未使用")
        opponent_only = self.catalog.add_deck("相手専用")
        self.catalog.update_deck(
            opponent_only.entry_id,
            name=opponent_only.name,
            opponent_only=True,
        )
        season = self.seasons.add(
            name="開催中",
            season_type="ranked",
            duel_type="ranked",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )
        for index in range(2):
            record = self.records.create_manual(
                DuelRecordValues(status="confirmed", own_deck=frequent.name, tags=("大会",)),
                occurred_at=datetime(2026, 8, 10 + index, tzinfo=timezone.utc),
            )
            self.assertIsNotNone(record)

        suggestion = self.service.input_suggestion(occurred_on=date(2026, 8, 13))

        self.assertEqual(suggestion.values.own_deck, "頻出")
        self.assertEqual(suggestion.values.season_id, season.season_id)
        self.assertEqual(suggestion.decks[0].name, "頻出")
        self.assertNotIn("相手専用", {item.name for item in suggestion.decks})
        self.assertTrue(suggestion.reasons)

    def test_incomplete_queue_distinguishes_missing_and_draft(self) -> None:
        self._recording("missing")
        self._recording("draft")
        self.records.save("draft", DuelRecordValues(status="draft"), expected_revision=0)
        self.records.create_manual(
            DuelRecordValues(status="draft"),
            occurred_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        )

        items = self.service.list_incomplete()

        self.assertEqual({item.kind for item in items}, {"missing", "draft"})
        self.assertEqual(len(items), 3)

    def test_bulk_update_changes_all_records_and_writes_each_audit(self) -> None:
        records = tuple(
            self.records.create_manual(
                DuelRecordValues(status="confirmed", own_deck="旧", tags=("維持",)),
                occurred_at=datetime(2026, 8, 10 + index, tzinfo=timezone.utc),
            )
            for index in range(2)
        )

        saved = self.service.bulk_update(
            tuple(item.duel_id for item in records),
            BulkDuelUpdate(own_deck="新", add_tags=("追加",), remove_tags=("維持",)),
        )

        self.assertEqual(len(saved), 2)
        self.assertTrue(all(item.values.own_deck == "新" for item in saved))
        self.assertTrue(all(item.values.tags == ("追加",) for item in saved))
        self.assertTrue(all(len(self.records.changes(item.duel_id)) == 2 for item in saved))

    def test_bulk_update_changes_coin_face_and_can_clear_to_unknown(self) -> None:
        heads, tails = (
            self.records.create_manual(
                DuelRecordValues(status="confirmed", coin_face=coin_face),
                occurred_at=datetime(2026, 8, 10 + index, tzinfo=timezone.utc),
            )
            for index, coin_face in enumerate(("heads", "tails"))
        )

        saved = self.service.bulk_update(
            (heads.duel_id, tails.duel_id),
            BulkDuelUpdate(coin_face="unknown"),
        )

        self.assertEqual([item.values.coin_face for item in saved], ["unknown", "unknown"])
        self.assertEqual(self.records.get(heads.duel_id).values.coin_face, "unknown")

    def test_bulk_update_validates_every_identifier_before_writing(self) -> None:
        record = self.records.create_manual(
            DuelRecordValues(status="confirmed", own_deck="変更前"),
            occurred_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        with self.assertRaises(DuelWorkflowError):
            self.service.bulk_update(
                (record.duel_id, "missing"), BulkDuelUpdate(own_deck="変更後")
            )
        self.assertEqual(self.records.get(record.duel_id).values.own_deck, "変更前")

    def test_saved_filter_round_trip_overwrite_delete(self) -> None:
        created = self.service.save_filter(
            "ランク用",
            DuelFilterCriteria(season_id=3, tag_entry_ids=(2, 2, 4), entry_origin="manual"),
        )
        self.assertEqual(created.criteria.tag_entry_ids, (2, 4))
        updated = self.service.save_filter(
            "ランク用更新",
            DuelFilterCriteria(coin_face="heads"),
            filter_id=created.filter_id,
        )
        self.assertEqual(self.service.list_filters(), (updated,))
        self.assertEqual(self.service.delete_filter(created.filter_id), updated)
        self.assertEqual(self.service.list_filters(), ())


if __name__ == "__main__":
    unittest.main()
