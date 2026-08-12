from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from master_duel_recorder_lite.data_management import (
    EXPORT_SCHEMA,
    ManagedDataError,
    ManagedDataService,
)
from master_duel_recorder_lite.duel_catalog import DuelCatalogRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.seasons import SeasonRepository


class ManagedDataServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        ensure_runtime_dirs(self.paths)
        self.history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        self.catalog = DuelCatalogRepository.from_runtime_paths(self.paths)
        self.seasons = SeasonRepository.from_runtime_paths(self.paths)
        self.service = ManagedDataService.from_runtime_paths(self.paths)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed(self) -> Path:
        deck = self.catalog.add_deck("テストデッキ", color="#123456")
        tag = self.catalog.add_tag("大会", color="#ABCDEF")
        season = self.seasons.add(
            name="ランク 2026-08",
            season_type="ranked",
            duel_type="ranked",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )
        video = self.paths.recordings / "duel.mkv"
        video.write_bytes(b"video")
        self.history.register_starting(
            recording_id="duel",
            output_path=video,
            container="mkv",
            source="manual",
            created_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        )
        DuelRecordRepository.from_runtime_paths(self.paths).save(
            "duel",
            DuelRecordValues(
                status="confirmed",
                result="win",
                own_deck=deck.name,
                tags=(tag.name,),
                season_id=season.season_id,
            ),
            expected_revision=0,
        )
        return video

    def test_export_reset_and_import_round_trip(self) -> None:
        video = self._seed()
        exported = self.paths.exports / "managed.json"
        result = self.service.export_to(exported)
        self.assertEqual(result.path, exported)
        self.assertGreater(result.row_count, 0)
        self.service.reset("all")
        self.assertTrue(video.exists())
        self.assertEqual(self.history.query(), ())
        restored = self.service.import_from(exported)
        self.assertIsNotNone(restored.backup_path)
        self.assertEqual(self.history.get("duel").recording_id, "duel")
        record = DuelRecordRepository.from_runtime_paths(self.paths).get("duel")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.values.own_deck, "テストデッキ")
        self.assertEqual(record.values.tags, ("大会",))

    def test_scope_resets_preserve_video_and_unrelated_data(self) -> None:
        video = self._seed()
        self.service.reset("decks")
        self.assertTrue(video.exists())
        self.assertEqual(self.catalog.list_decks(), ())
        self.assertEqual(len(self.catalog.list_tags()), 1)
        self.assertEqual(len(self.seasons.list()), 1)
        record = DuelRecordRepository.from_runtime_paths(self.paths).get("duel")
        assert record is not None
        self.assertEqual(record.values.own_deck, "")

    def test_invalid_json_does_not_change_database(self) -> None:
        self._seed()
        malformed = self.paths.exports / "invalid.json"
        malformed.write_text(
            json.dumps({"schema": EXPORT_SCHEMA, "tables": {}}), encoding="utf-8"
        )
        with self.assertRaises(ManagedDataError):
            self.service.import_from(malformed)
        self.assertIsNotNone(self.history.get("duel"))


if __name__ == "__main__":
    unittest.main()
