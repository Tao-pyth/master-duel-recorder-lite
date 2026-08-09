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


if __name__ == "__main__":
    unittest.main()
