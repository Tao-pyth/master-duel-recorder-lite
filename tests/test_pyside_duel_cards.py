import importlib.util
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.duel_card_repository import DuelCardRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class DuelCardWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtWidgets import QMessageBox
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = RecorderApplicationService(user_data_dir=Path(self.temp.name) / "user_data")
        self.addCleanup(self.service.close)
        self.database = self.service._get_card_repository().database_path
        self.cards = DuelCardRepository(self.database)
        self.duel = DuelRecordRepository(self.database).create_manual(
            DuelRecordValues(), occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc)
        )
        self.identity = self.cards.save_identity(name="確認カード")
        self.warning = self.enterContext(patch.object(QMessageBox, "warning"))
        self.question = self.enterContext(patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel))

    def panel(self):
        from master_duel_recorder_lite.pyside_duel_cards import CardInformationPanel
        panel = CardInformationPanel(self.service, self.duel.duel_id, on_changed=Mock())
        panel.resize(400, 480)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.close)
        return panel

    def test_no_video_save_use_opener_and_reopen(self):
        panel = self.panel()
        panel.card.setCurrentIndex(panel.card.findData(self.identity.card_id))
        panel.state.setCurrentIndex(panel.state.findData("used"))
        panel.opener.setChecked(True)
        panel.save_button.click()
        self.app.processEvents()
        self.warning.assert_not_called()
        saved = self.cards.get_review(self.duel.duel_id)
        self.assertEqual(saved.information[0].state, "used")
        self.assertEqual(saved.openers[0].card_id, self.identity.card_id)
        reopened = self.panel()
        reopened.table.selectRow(0)
        self.app.processEvents()
        self.assertEqual(reopened.card.currentText(), self.identity.name)
        self.assertEqual(reopened.state.currentData(), "used")
        self.assertTrue(reopened.opener.isChecked())
        self.assertIn("自分:確認カード", reopened.summary.text())
        from PySide6.QtWidgets import QScrollArea
        reopened.resize(400, 300)
        self.app.processEvents()
        scroll = reopened.findChild(QScrollArea, "duel_cards_scroll")
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(scroll.widget().minimumSizeHint().width(), scroll.viewport().width())

    def test_nonuse_unknown_and_new_name_need_no_analysis_target(self):
        panel = self.panel()
        panel.card.setEditText("手入力したカード")
        panel.actor.setCurrentIndex(panel.actor.findData("opponent"))
        panel.state.setCurrentIndex(panel.state.findData("not_used"))
        panel.save_button.click()
        saved = self.cards.get_review(self.duel.duel_id).information[0]
        self.assertEqual((saved.card_name, saved.actor, saved.state), ("手入力したカード", "opponent", "not_used"))
        panel.table.selectRow(0)
        panel.state.setCurrentIndex(panel.state.findData("unknown"))
        panel.save_button.click()
        self.assertEqual(self.cards.get_review(self.duel.duel_id).information[0].state, "unknown")
        self.assertFalse(panel.opener.isEnabled())

    def test_revision_conflict_and_reload_cancellation_keep_edits(self):
        saved = self.cards.save_information(self.duel.duel_id, self.identity.card_id, "self", "unknown", expected_revision=0)
        panel = self.panel()
        panel.table.selectRow(0)
        panel.state.setCurrentIndex(panel.state.findData("used"))
        current = self.cards.save_information(self.duel.duel_id, self.identity.card_id, "self", "not_used", expected_revision=saved.revision)
        panel.save_button.click()
        self.warning.assert_called_once()
        self.assertEqual(panel.state.currentData(), "used")
        self.assertEqual(self.cards.get_review(self.duel.duel_id).information, (current,))
        panel.reload_button.click()
        self.question.assert_called_once()
        self.assertEqual(panel.state.currentData(), "used")


if __name__ == "__main__":
    unittest.main()
