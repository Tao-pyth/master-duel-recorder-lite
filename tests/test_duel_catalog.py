import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.duel_catalog import DuelCatalogError, DuelCatalogRepository
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.runtime_paths import default_runtime_paths


class DuelCatalogRepositoryTest(unittest.TestCase):
    def repository(self, root: Path) -> DuelCatalogRepository:
        return DuelCatalogRepository.from_runtime_paths(default_runtime_paths(user_data_dir=root))

    def test_add_rename_and_delete_japanese_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository = self.repository(Path(tmp_dir) / "user_data")

            deck = repository.add("deck", "青眼")
            tag = repository.add("tag", "ランク戦練習")
            renamed = repository.rename(deck.entry_id, "青眼デッキ")
            deleted = repository.delete(tag.entry_id)

            entries = repository.list()

        self.assertEqual(renamed.name, "青眼デッキ")
        self.assertEqual(deleted.name, "ランク戦練習")
        self.assertEqual([(item.kind, item.name) for item in entries], [("deck", "青眼デッキ")])

    def test_duplicate_normalized_name_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository = self.repository(Path(tmp_dir) / "user_data")
            repository.add("deck", "ABC")

            with self.assertRaisesRegex(DuelCatalogError, "既にあります"):
                repository.add("deck", "abc")

    def test_record_values_are_remembered_as_catalog_and_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            repository = self.repository(root)
            values = DuelRecordValues(
                duel_type="ranked",
                own_deck="青眼",
                opponent_deck="烙印",
                tags=("大会", "連勝"),
            )

            saved = repository.remember_record_values(values)
            reloaded = self.repository(root).preferences()
            entries = self.repository(root).list()

        self.assertEqual(saved, reloaded)
        self.assertEqual(reloaded.to_record_values().duel_type, "ranked")
        self.assertEqual(reloaded.to_record_values().tags, ("大会", "連勝"))
        self.assertEqual(
            {(item.kind, item.name) for item in entries},
            {("deck", "青眼"), ("deck", "烙印"), ("tag", "大会"), ("tag", "連勝")},
        )

    def test_deck_description_and_tag_color_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            repository = self.repository(root)
            deck = repository.add_deck("青眼", description="主力デッキ")
            tag = repository.add_tag("大会", description="大会用", color="#00aa88")

            repository.update_deck(deck.entry_id, name="青眼改", description="更新済み")
            repository.update_tag(
                tag.entry_id,
                name="公式大会",
                description="公式イベント",
                color="#112233",
            )
            reloaded = self.repository(root)
            decks = reloaded.list_decks()
            tags = reloaded.list_tags()

        self.assertEqual(decks[0].description, "更新済み")
        self.assertIsNone(decks[0].color)
        self.assertEqual(tags[0].description, "公式イベント")
        self.assertEqual(tags[0].color, "#112233")

    def test_used_tag_is_archived_and_keeps_stable_recording_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            repository = self.repository(root)
            from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
            from master_duel_recorder_lite.duel_records import DuelRecordRepository

            paths = default_runtime_paths(user_data_dir=root)
            recordings = paths.recordings
            recordings.mkdir(parents=True)
            history = RecordingHistoryRepository.from_runtime_paths(paths)
            history.register_starting(
                recording_id="recording-1",
                output_path=recordings / "recording-1.mkv",
                container="mkv",
                source="manual",
            )
            DuelRecordRepository.from_runtime_paths(paths).save(
                "recording-1",
                DuelRecordValues(tags=("大会",)),
                expected_revision=0,
            )
            tag = repository.list_tags()[0]
            renamed = repository.update_tag(
                tag.entry_id,
                name="公式大会",
                description="",
                color="#445566",
            )
            deleted = repository.delete(tag.entry_id)
            linked = repository.recordings_for_tag(tag.entry_id)
            active_tags = repository.list_tags()
            archived_tags = repository.list_tags(include_archived=True)

        self.assertEqual(linked, ("recording-1",))
        self.assertEqual(renamed.entry_id, tag.entry_id)
        self.assertTrue(deleted.is_archived)
        self.assertEqual(active_tags, ())
        self.assertEqual(archived_tags[0].name, "公式大会")


if __name__ == "__main__":
    unittest.main()
