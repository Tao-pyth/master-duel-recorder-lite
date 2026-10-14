"""旧情報の救済保管を、動画なしの既存カード画面から確認できる。"""
from __future__ import annotations

from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.data_protection import initialize_protected_history_database
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.history_database import HISTORY_DATABASE_NAME, connect_history_database
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
import test_retention_schema_migration as legacy


def review_legacy(paths):
    ensure_runtime_dirs(paths)
    database = paths.db / HISTORY_DATABASE_NAME
    legacy.create_legacy(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        legacy.target(connection, "card")
        legacy.recording(connection, "review")
        legacy.observation(connection, "unknown", "review", actor="unknown")
        legacy.observation(connection, "unmapped", "review", target_id=None)
        legacy.observation(connection, "display", "review", supports_use=False)
        legacy.observation(connection, "used", "review")
        legacy.nonuse(connection, "review", "card")
        legacy.opener(connection, "review", "display")
        legacy.recording(connection, "other")
        legacy.observation(connection, "other-unmapped", "other", target_id=None)
        # 確認解除だけの録画へ、移行が戦績を自動生成することはありません。
        legacy.recording(connection, "orphan")
        legacy.nonuse(connection, "orphan", "card", confirmed=False)
    return database


class MigrationReviewFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name) / "runtime")
        self.database = review_legacy(self.paths)
        initialize_protected_history_database(self.paths)
        self.service = RecorderApplicationService(user_data_dir=self.paths.root)
        self.addCleanup(self.service.close)
        self.records = DuelRecordRepository.from_runtime_paths(self.paths)
        self.duel = self.records.get("review")

    def rows(self, table):
        with closing(connect_history_database(self.database)) as connection:
            return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


class MigrationReviewApiTest(MigrationReviewFixture):
    def test_migrated_details_include_original_card_actor_action_opener_and_only_owned_reviews(self):
        before = self.rows("duel_card_information")
        review = self.service.get_duel_card_review(self.duel.duel_id)
        self.assertEqual(len(review.migration_reviews), 5)
        details = {item.reason: item for item in review.migration_reviews}
        self.assertEqual(details["unmapped_card"].card_name, "カードcard")
        self.assertEqual((details["unknown_actor"].actor, details["unknown_actor"].action), ("unknown", "activate"))
        self.assertFalse(details["confirmed_observation_without_usage"].supports_use)
        self.assertTrue(details["usage_nonuse_conflict"].nonuse_confirmed)
        self.assertEqual((details["invalid_opener_reference"].card_name, details["invalid_opener_reference"].opener_status),
                         ("カードcard", "confirmed"))
        self.assertEqual(self.rows("duel_card_information"), before)
        json.dumps(review.to_dict(), ensure_ascii=False)
        self.assertFalse(any(self.paths.recordings.iterdir()))

    def test_resolved_history_is_not_presented_as_needing_review(self):
        with closing(connect_history_database(self.database)) as connection, connection:
            connection.execute("UPDATE card_migration_review_items SET status='resolved' WHERE source_id='unmapped'")
        review = self.service.get_duel_card_review(self.duel.duel_id)
        self.assertEqual(len(review.migration_reviews), 4)
        self.assertNotIn("unmapped_card", {item.reason for item in review.migration_reviews})

    def test_recording_only_review_is_visible_after_explicit_creation_of_unknown_battle(self):
        self.assertIsNone(self.records.get("orphan"))
        duel = self.service.save_duel_record("orphan", DuelRecordValues(), expected_revision=0)
        review = self.service.get_duel_card_review(duel.duel_id)
        self.assertEqual(len(review.migration_reviews), 1)
        self.assertEqual(review.migration_reviews[0].reason, "orphan_confirmation_history")
        self.assertEqual(review.migration_reviews[0].card_name, "カードcard")
        self.assertFalse(review.migration_reviews[0].nonuse_confirmed)
        self.assertEqual((duel.values.status, duel.values.result, duel.values.play_order, duel.values.coin_face),
                         ("draft", "unknown", "unknown", "unknown"))
        self.assertEqual(review.information, ())


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class MigrationReviewWidgetTest(MigrationReviewFixture):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_video_free_panel_shows_readonly_japanese_original_information_without_inferring_usage(self):
        from master_duel_recorder_lite.pyside_duel_cards import CardInformationPanel
        before = self.service.get_duel_card_review(self.duel.duel_id)
        with patch("PySide6.QtWidgets.QMessageBox.warning") as warning:
            panel = CardInformationPanel(self.service, self.duel.duel_id)
            self.addCleanup(panel.close)
            panel.resize(400, 480)
            panel.show()
            self.app.processEvents()
            self.assertIn("要確認情報 5件", panel.migration_summary.text())
            self.assertIn("手動登録・訂正", panel.migration_summary.text())
            text = panel.migration_details.toPlainText()
            for label in ("カードcard", "使用者: 不明", "行動: 発動", "初動確認: 確認済み", "使用の根拠: なし"):
                self.assertIn(label, text)
            for technical in ("card_migration_review_items", "supports_use", "target_id", "{", "unmapped_card"):
                self.assertNotIn(technical, text)
            self.assertTrue(panel.migration_details.isReadOnly())
            self.assertTrue(panel.migration_details.isVisible())
            warning.assert_not_called()
        self.assertEqual(self.service.get_duel_card_review(self.duel.duel_id), before)

    def test_existing_history_card_entry_creates_only_unknown_battle_for_orphan_review(self):
        import test_retention_gui as gui_fixture

        def setup(service):
            review_legacy(service.paths)

        def scenario(window, service, app, pump):
            records = DuelRecordRepository.from_runtime_paths(service.paths)
            self.assertIsNone(records.get("orphan"))
            window.show_page("history")
            gui_fixture.RetentionGuiTest.select_recording(window, "orphan")
            self.assertTrue(window.widgets["history_cards"].isEnabled())
            window._show_selected_duel_cards()
            app.processEvents()
            panel = window.review_windows[-1].card_information_panel
            self.assertIn("要確認情報 1件", panel.migration_summary.text())
            self.assertIn("未使用確認: 解除済み", panel.migration_details.toPlainText())
            saved = records.get("orphan")
            self.assertEqual((saved.values.status, saved.values.result, saved.values.play_order, saved.values.coin_face),
                             ("draft", "unknown", "unknown", "unknown"))
            self.assertEqual(service.get_duel_card_review(saved.duel_id).information, ())

        gui_fixture.RetentionGuiTest().run_gui(scenario, setup=setup)
