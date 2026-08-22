import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from master_duel_recorder_lite.duel_records import DuelRecordRepository
from master_duel_recorder_lite.duel_catalog import (
    DuelCatalogError,
    DuelCatalogRepository,
)
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.history_database import connect_history_database
from master_duel_recorder_lite.runtime_paths import default_runtime_paths


class DuelCatalogRepositoryTest(unittest.TestCase):
    def repository(self, root: Path) -> DuelCatalogRepository:
        return DuelCatalogRepository.from_runtime_paths(
            default_runtime_paths(user_data_dir=root)
        )

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
        self.assertEqual(
            [(item.kind, item.name) for item in entries], [("deck", "青眼デッキ")]
        )

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
        self.assertEqual(decks[0].color, "#2F6B5F")
        self.assertEqual(tags[0].description, "公式イベント")
        self.assertEqual(tags[0].color, "#112233")

    def test_deck_color_and_usage_flags_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository = self.repository(Path(tmp_dir) / "user_data")
            deck = repository.add_deck("烙印", color="#123ABC")

            updated = repository.update_deck(
                deck.entry_id,
                name=deck.name,
                description="相手確認用",
                color="#ABC123",
                opponent_only=True,
                hidden_from_history_statistics=True,
            )
            reloaded = self.repository(Path(tmp_dir) / "user_data").list_decks(
                include_hidden=True
            )[0]

        self.assertEqual(updated.color, "#ABC123")
        self.assertTrue(reloaded.opponent_only)
        self.assertTrue(reloaded.hidden_from_history_statistics)

    def test_list_decks_includes_usage_count_and_orders_by_frequency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            catalog = self.repository(root)
            records = DuelRecordRepository.from_runtime_paths(paths)
            catalog.add_deck("未使用")
            catalog.add_deck("同数A")
            catalog.add_deck("同数B")
            catalog.add_deck("最多")

            records.create_manual(
                DuelRecordValues(own_deck="最多", opponent_deck="同数B"),
                occurred_at=datetime.now(timezone.utc),
            )
            records.create_manual(
                DuelRecordValues(own_deck="最多", opponent_deck="同数A"),
                occurred_at=datetime.now(timezone.utc),
            )
            records.create_manual(
                DuelRecordValues(opponent_deck="最多"),
                occurred_at=datetime.now(timezone.utc),
            )

            decks = self.repository(root).list_decks()

        self.assertEqual([item.name for item in decks], ["最多", "同数A", "同数B", "未使用"])
        self.assertEqual(
            {item.name: item.usage_count for item in decks},
            {"最多": 3, "同数A": 1, "同数B": 1, "未使用": 0},
        )

    def test_list_decks_counts_legacy_name_rows_when_deck_id_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            catalog = self.repository(root)
            records = DuelRecordRepository.from_runtime_paths(paths)
            deck = catalog.add_deck("旧データ")
            record = records.create_manual(
                DuelRecordValues(own_deck="旧データ"),
                occurred_at=datetime.now(timezone.utc),
            )
            with (
                closing(connect_history_database(catalog.database_path)) as connection,
                connection,
            ):
                connection.execute(
                    "UPDATE duel_records SET own_deck_id = NULL WHERE duel_id = ?",
                    (record.duel_id,),
                )

            reloaded = self.repository(root).list_decks()

        self.assertEqual(reloaded[0].entry_id, deck.entry_id)
        self.assertEqual(reloaded[0].usage_count, 1)

    def test_deck_tags_and_deck_only_tags_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository = self.repository(Path(tmp_dir) / "user_data")
            deck = repository.add_deck("レジェンドアンソロジー")
            normal_tag = repository.add_tag("イベント")
            deck_only_tag = repository.add_tag("調整中", deck_only=True)

            saved = repository.set_deck_tags(
                deck.entry_id, (normal_tag.entry_id, deck_only_tag.entry_id)
            )
            record_tags = repository.list_tags(include_deck_only=False)
            all_tags = repository.list_tags()
            reloaded = self.repository(Path(tmp_dir) / "user_data")
            reloaded_tags = reloaded.list_deck_tags(deck.entry_id)

        self.assertEqual(
            {tag.name for tag in saved}, {"イベント", "調整中"}
        )
        self.assertEqual({tag.name for tag in reloaded_tags}, {"イベント", "調整中"})
        self.assertEqual([tag.name for tag in record_tags], ["イベント"])
        self.assertEqual({tag.name for tag in all_tags}, {"イベント", "調整中"})
        self.assertTrue(deck_only_tag.deck_only)

    def test_tag_used_by_deck_is_archived_on_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository = self.repository(Path(tmp_dir) / "user_data")
            deck = repository.add_deck("青眼")
            tag = repository.add_tag("テーマ")
            repository.set_deck_tags(deck.entry_id, (tag.entry_id,))

            deleted = repository.delete(tag.entry_id)

        self.assertTrue(deleted.is_archived)

    def test_used_tag_is_archived_and_keeps_stable_recording_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            repository = self.repository(root)
            from master_duel_recorder_lite.recording_history import (
                RecordingHistoryRepository,
            )
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
