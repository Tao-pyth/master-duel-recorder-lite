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
from master_duel_recorder_lite.duel_workflow import DuelFilterCriteria, DuelWorkflowService
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
                coin_face="heads",
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
        assert restored.backup_path is not None
        self.assertEqual(restored.backup_path.suffix, ".mdrl-backup")
        self.assertEqual(self.history.get("duel").recording_id, "duel")
        record = DuelRecordRepository.from_runtime_paths(self.paths).get("duel")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.values.own_deck, "テストデッキ")
        self.assertEqual(record.values.tags, ("大会",))
        self.assertEqual(record.values.coin_face, "heads")

    def test_import_accepts_v019_export_without_coin_columns(self) -> None:
        self._seed()
        exported = self.paths.exports / "legacy.json"
        self.service.export_to(exported)
        payload = json.loads(exported.read_text(encoding="utf-8"))
        for row in payload["tables"]["duel_records"]:
            row.pop("coin_face")
        exported.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        self.service.reset("all")
        self.service.import_from(exported)
        record = DuelRecordRepository.from_runtime_paths(self.paths).get("duel")

        assert record is not None
        self.assertEqual(record.values.coin_face, "unknown")

    def test_import_ignores_removed_coin_toss_outcome_column(self) -> None:
        self._seed()
        exported = self.paths.exports / "legacy-outcome.json"
        self.service.export_to(exported)
        payload = json.loads(exported.read_text(encoding="utf-8"))
        for row in payload["tables"]["duel_records"]:
            row["coin_toss_outcome"] = "win"
        exported.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        self.service.import_from(exported)

        record = DuelRecordRepository.from_runtime_paths(self.paths).get("duel")
        assert record is not None
        self.assertEqual(record.values.coin_face, "heads")
        current = self.paths.exports / "current.json"
        self.service.export_to(current)
        payload = json.loads(current.read_text(encoding="utf-8"))
        self.assertNotIn("coin_toss_outcome", payload["tables"]["duel_records"][0])

    def test_export_import_preserves_saved_filters_and_accepts_legacy_without_them(self) -> None:
        workflow = DuelWorkflowService.from_runtime_paths(self.paths)
        workflow.save_filter("手動のみ", DuelFilterCriteria(entry_origin="manual"))
        exported = self.paths.exports / "filters.json"
        self.service.export_to(exported)
        self.service.reset("all")
        self.service.import_from(exported)
        self.assertEqual(workflow.list_filters()[0].name, "手動のみ")

        payload = json.loads(exported.read_text(encoding="utf-8"))
        payload["tables"].pop("saved_duel_filters")
        legacy = self.paths.exports / "legacy-without-filters.json"
        legacy.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.service.reset("all")
        self.service.import_from(legacy)
        self.assertEqual(workflow.list_filters(), ())

    def test_import_accepts_v020_export_with_recording_keyed_relations(self) -> None:
        self._seed()
        exported = self.paths.exports / "v020.json"
        self.service.export_to(exported)
        payload = json.loads(exported.read_text(encoding="utf-8"))
        legacy_ids = {
            row["duel_id"]: row["recording_id"]
            for row in payload["tables"]["duel_records"]
        }
        for row in payload["tables"]["duel_records"]:
            row.pop("duel_id")
            row.pop("entry_origin")
            row.pop("occurred_at")
        for table in (
            "duel_record_tags",
            "duel_record_changes",
            "duel_record_tag_links",
        ):
            for row in payload["tables"][table]:
                row["recording_id"] = legacy_ids[row.pop("duel_id")]
        exported.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        self.service.reset("all")
        self.service.import_from(exported)
        record = DuelRecordRepository.from_runtime_paths(self.paths).get("duel")

        assert record is not None
        self.assertEqual(record.duel_id, "duel")
        self.assertEqual(record.entry_origin, "recording")
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
