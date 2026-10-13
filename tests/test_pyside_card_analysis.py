"""隔離SQLiteと実widgetで使用カードの保存・再表示・導線を確認する。"""
from __future__ import annotations

from datetime import date, timedelta
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.card_analysis_models import CardInputFingerprint, CardObservationValues
from master_duel_recorder_lite.card_analysis_repository import CardAnalysisRepository
from master_duel_recorder_lite.duel_records import DuelRecordValues
from test_gui_refresh_and_review_lifecycle import register_video


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class CardAnalysisWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QFont, QFontDatabase
        from PySide6.QtWidgets import QApplication
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])
        cls.original_font = QFont(cls.app.font())
        cls.application_font_id = -1
        font = Path("C:/Windows/Fonts/meiryo.ttc")
        if font.exists():
            cls.application_font_id = QFontDatabase.addApplicationFont(str(font))
            cls.app.setFont(QFont("Meiryo UI", 9))

    @classmethod
    def tearDownClass(cls):
        from PySide6.QtGui import QFontDatabase
        cls.app.setFont(cls.original_font)
        if cls.application_font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.application_font_id)
        cls.app.processEvents()

    def setUp(self):
        from PySide6.QtWidgets import QMessageBox
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.application = RecorderApplicationService(user_data_dir=Path(self.temporary.name))
        self.addCleanup(self.application.close)
        self.repository = CardAnalysisRepository.from_runtime_paths(self.application.paths)
        self.service = self.application
        self.first = self.service.save_card_target(name="検証カードＡ", aliases=("検証の別表記",), purpose="妨害")
        self.second = self.service.save_card_target(name="検証カードＢ", purpose="ドロー")
        self.warning = self.enterContext(patch.object(QMessageBox, "warning"))
        self.question = self.enterContext(patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel))

    def panel(self, cls, *args, **kwargs):
        panel = cls(self.service, *args, **kwargs)
        panel.resize(400, 640)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.close)
        return panel

    def recording(self, identifier, *, result="win", kind="ranked"):
        source = register_video(self.application, identifier)
        self.application.save_duel_record(identifier, DuelRecordValues(status="confirmed", result=result, duel_type=kind), expected_revision=0)
        return source

    def observations(self, identifier="used", *, count=1):
        source = self.recording(identifier)
        stat = source.stat()
        fingerprint = CardInputFingerprint(str(source), stat.st_size, stat.st_mtime_ns, hashlib.sha256(source.read_bytes()).hexdigest())
        job = self.repository.create_job(identifier, fingerprint=fingerprint, analyzer_version="widget-fixture")
        self.repository.claim_job(job.job_id)
        values = tuple(CardObservationValues(
            self.first.target_id, "誤認識の名称", "opponent", "special_summon", 500 + index * 300,
            evidence={"supports_use": True, "reason": "ログの行動を確認"}, source_key=f"fixture-{index}", confidence=.8,
        ) for index in range(count))
        self.repository.complete_job(job.job_id, values, fingerprint=fingerprint)
        return self.repository.list_observations(identifier)

    def test_target_add_alias_edit_disable_reload_and_invalid_name(self):
        from master_duel_recorder_lite.pyside_card_analysis import CardTargetPanel
        panel = self.panel(CardTargetPanel)
        panel.new()
        panel.name.setText("独自の対象")
        panel.aliases.setPlainText("表示の別名\n別表記")
        panel.purpose.setText("初動")
        panel.save()
        target = panel.current
        self.assertEqual(target.aliases, ("表示の別名", "別表記"))
        panel.enabled.setChecked(False)
        panel.save()
        panel.reload()
        self.assertFalse(self.repository.get_target(target.target_id).enabled)
        self.assertIn("無効", [panel.table.item(row, 2).text() for row in range(panel.table.rowCount())])
        previous_revision = panel.current.revision
        panel.name.setText("")
        panel.save()
        self.warning.assert_called_once()
        self.assertEqual(self.repository.get_target(target.target_id).revision, previous_revision)

    def test_correct_confirm_actual_time_and_opener_survive_reload(self):
        from master_duel_recorder_lite.pyside_card_analysis import CardReviewPanel
        event = self.observations()[0]
        changed, seek = Mock(), Mock()
        panel = self.panel(CardReviewPanel, "used", seek=seek, on_changed=changed, duration_seconds=2)
        panel.table.selectRow(0)
        panel.name.setCurrentIndex(panel.name.findData(self.second.target_id))
        panel.actor.setCurrentIndex(panel.actor.findData("self"))
        panel.action.setCurrentIndex(panel.action.findData("activate"))
        panel.actual_known.setChecked(True)
        panel.actual_seconds.setValue(.4)
        panel.opener.setChecked(True)
        panel.save("confirmed")
        persisted = self.repository.get_observation(event.event_id)
        self.assertEqual((persisted.card_name, persisted.actor, persisted.action, persisted.actual_ms, persisted.status),
                         (self.second.name, "self", "activate", 400, "confirmed"))
        self.assertTrue(persisted.is_opener)
        panel.reload(force=True)
        self.assertTrue(panel.opener.isChecked())
        self.assertTrue(panel.actual_known.isChecked())
        self.assertIn("使用は利用者が確認済み", panel.evidence.text())
        self.assertNotIn("時刻は未確認", panel.evidence.text())
        self.assertEqual(panel.actual_seconds.value(), .4)
        panel.seek_current()
        seek.assert_called_once_with(500)
        self.assertIn(self.second.name, panel.confirmation_summary.text())
        panel.clear_opener()
        self.assertFalse(self.repository.get_observation(event.event_id).is_opener)
        self.assertFalse(panel.opener.isChecked())
        self.warning.assert_not_called()

    def test_explicit_nonuse_save_reload_and_clear(self):
        from master_duel_recorder_lite.pyside_card_analysis import CardReviewPanel
        self.recording("nonuse")
        panel = self.panel(CardReviewPanel, "nonuse", seek=Mock(), on_changed=Mock(), duration_seconds=2)
        self.assertIn("未解析", panel.status.text())
        self.assertIn("未確認", panel.confirmation_summary.text())
        panel.confirm_target.setCurrentIndex(panel.confirm_target.findData(self.first.target_id))
        panel.set_nonuse(True)
        self.assertTrue(self.repository.get_review("nonuse").nonuses[0].confirmed)
        panel.reload(force=True)
        self.assertIn(self.first.name, panel.confirmation_summary.text())
        panel.set_nonuse(False)
        self.assertFalse(self.repository.get_review("nonuse").nonuses[0].confirmed)
        self.assertIn("未確認", panel.confirmation_summary.text())
        self.warning.assert_not_called()

    def test_conflict_preserves_saved_data_and_unsaved_editor(self):
        from master_duel_recorder_lite.pyside_card_analysis import CardReviewPanel
        event = self.observations()[0]
        panel = self.panel(CardReviewPanel, "used", seek=Mock(), on_changed=Mock(), duration_seconds=2)
        panel.table.selectRow(0)
        external = self.repository.update_observation(event.event_id, expected_revision=event.revision, card_name="他画面の訂正")
        panel.actor.setCurrentIndex(panel.actor.findData("self"))
        panel.save(None)
        self.warning.assert_called_once()
        self.assertEqual(self.repository.get_observation(event.event_id).revision, external.revision)
        self.assertEqual(self.repository.get_observation(event.event_id).card_name, "他画面の訂正")
        self.assertTrue(panel.dirty())
        source = self.application.resolve_recording("used").path
        stat = source.stat()
        fingerprint = CardInputFingerprint(str(source), stat.st_size, stat.st_mtime_ns, hashlib.sha256(source.read_bytes()).hexdigest())
        job = self.repository.create_job("used", fingerprint=fingerprint, analyzer_version="widget-fixture")
        self.repository.claim_job(job.job_id)
        self.repository.update_progress(job.job_id, .5)
        panel.poll()
        self.assertEqual(panel.actor.currentData(), "self")
        self.assertIn("解析中 50%", panel.status.text())
        self.assertEqual(panel.current.revision, event.revision)

    def test_dirty_cancel_restores_selected_row_without_discard(self):
        from master_duel_recorder_lite.pyside_card_analysis import CardReviewPanel
        events = self.observations(count=2)
        panel = self.panel(CardReviewPanel, "used", seek=Mock(), on_changed=Mock(), duration_seconds=2)
        panel.table.selectRow(0)
        panel.actor.setCurrentIndex(panel.actor.findData("self"))
        panel.table.selectRow(1)
        self.question.assert_called_once()
        self.assertEqual(panel.current.event_id, events[0].event_id)
        self.assertEqual(panel.table.currentRow(), 0)
        self.assertTrue(panel.dirty())

    def test_card_marker_selects_card_editor_and_seeks_observed_position(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QWidget
        from master_duel_recorder_lite.pyside_review import create_review_window
        event = self.observations()[0]
        with patch.object(self.application, "play_recording"):
            window = create_review_window(service=self.service, recording_id="used")
            window.show()
            self.app.processEvents()
            self.addCleanup(window.close)
            table = window.findChild(QWidget, "review_timeline_table")
            self.assertEqual(table.item(0, 0).data(Qt.ItemDataRole.UserRole), 500)
            with patch.object(window.player, "setPosition") as seek:
                table.cellClicked.emit(0, 0)
                seek.assert_called_with(500)
            self.assertEqual(window.card_panel.current.event_id, event.event_id)
            self.assertEqual(window.findChild(QWidget, "review_editor_tabs").currentWidget(), window.card_panel)
            self.assertFalse(window.findChild(QWidget, "review_timeline_confirm").isEnabled())
            model = self.service.get_review_view_model("used")
            self.assertEqual(model.visual_timeline[0].kind, "card_usage")
            self.assertIn("実使用時刻未確認", model.timeline[0].label)

    def test_statistics_confirmed_reference_opener_conditions_and_dates(self):
        from master_duel_recorder_lite.duel_statistics import StatisticsFilter
        from master_duel_recorder_lite.pyside_card_analysis import CardStatisticsPanel
        event = self.observations()[0]
        self.repository.update_observation(event.event_id, expected_revision=event.revision,
                                          actor="self", status="confirmed", is_opener=True,
                                          opener_expected_revision=0)
        candidate = self.observations("candidate")[0]
        self.repository.update_observation(candidate.event_id, expected_revision=candidate.revision, actor="self")
        self.recording("nonuse", result="loss")
        self.repository.set_nonuse("nonuse", self.first.target_id, "self", expected_revision=0)
        self.recording("unknown", result="draw")
        self.recording("outside", kind="room")
        selected = [StatisticsFilter(duel_type="ranked")]
        panel = self.panel(CardStatisticsPanel, filters=lambda: selected[0])
        panel.target.setCurrentIndex(panel.target.findData(self.first.target_id))
        panel.refresh()
        self.assertIn("確認済み比較", panel.status.text())
        self.assertEqual([panel.table.item(row, 1).text() for row in range(3)], ["1", "1", "2"])
        self.assertEqual(panel.opener_table.rowCount(), 1)
        panel.candidates.setChecked(True)
        self.assertIn("候補を含む参考比較", panel.status.text())
        self.assertEqual([panel.table.item(row, 1).text() for row in range(3)], ["2", "1", "1"])
        panel.opener.setCurrentIndex(panel.opener.findData(self.first.target_id))
        self.assertEqual([panel.table.item(row, 1).text() for row in range(3)], ["1", "0", "0"])
        self.assertIn("共通条件の母集団 4戦", panel.status.text())
        self.assertIn("初動未確認 3戦", panel.status.text())
        selected[0] = StatisticsFilter(duel_type="ranked", date_from=date.today() + timedelta(days=2))
        panel.refresh()
        self.assertEqual([panel.table.item(row, 1).text() for row in range(3)], ["0", "0", "0"])
        self.assertEqual(panel.table.item(0, 5).text(), "—")
        self.warning.assert_not_called()

    def test_small_card_review_scroll_reaches_every_operation(self):
        from PySide6.QtWidgets import QPushButton, QScrollArea
        from master_duel_recorder_lite.pyside_card_analysis import CardReviewPanel
        self.observations()
        panel = self.panel(CardReviewPanel, "used", seek=Mock(), on_changed=Mock(), duration_seconds=2)
        panel.resize(400, 480)
        panel.table.selectRow(0)
        self.app.processEvents()
        scroll = panel.findChild(QScrollArea, "review_cards_scroll")
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        for name in ("review_cards_reload", "review_card_save", "review_card_confirm",
                     "review_card_reject", "review_card_nonuse_confirm", "review_card_opener_clear"):
            button = panel.findChild(QPushButton, name)
            scroll.ensureWidgetVisible(button)
            self.app.processEvents()
            self.assertTrue(scroll.viewport().rect().contains(button.mapTo(scroll.viewport(), button.rect().center())), name)
        if capture := os.environ.get("MDRL_CARD_CAPTURE"):
            root = Path(capture)
            root.mkdir(parents=True, exist_ok=True)
            scroll.verticalScrollBar().setValue(0)
            self.app.processEvents()
            self.assertTrue(panel.grab().save(str(root / "cards-top.png")))
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            self.app.processEvents()
            self.assertTrue(panel.grab().save(str(root / "cards-bottom.png")))

    def test_main_settings_save_targets_dirty_guard_and_statistics_route(self):
        from PySide6.QtWidgets import QApplication
        from master_duel_recorder_lite import pyside_gui

        def inspect(app):
            window = next(widget for widget in app.topLevelWidgets() if hasattr(widget, "card_target_panel") and widget.isVisible())
            try:
                window.show_page("settings")
                window.widgets["settings_card_analysis_enabled"].setChecked(True)
                window.widgets["settings_card_analysis_fps"].setText("1.5")
                self.assertTrue(window.save_settings())
                config = self.service.load_config().config
                self.assertTrue(config.card_analysis_enabled)
                self.assertEqual(config.card_analysis_fps, 1.5)
                window.widgets["settings_card_analysis_enabled"].setChecked(False)
                self.assertTrue(window.save_settings())
                target_panel = window.card_target_panel
                target_panel.new()
                target_panel.name.setText("通常画面で追加した対象")
                window.show_page("statistics")
                self.assertEqual(window.stack.currentWidget(), window.pages["settings"])
                target_panel.save()
                self.assertIn("通常画面で追加した対象", [target.name for target in self.service.list_card_targets()])
                window.show_page("statistics")
                tabs = window.widgets["statistics_tab_panel"]
                self.assertEqual(tabs.count(), 9)
                tabs.setCurrentWidget(window.card_statistics_panel)
                window.resize(980, 640)
                app.processEvents()
                self.assertEqual((window.width(), window.height()), (980, 640))
                panel = window.card_statistics_panel
                for control in (panel.target, panel.actor, panel.opener, panel.candidates):
                    self.assertTrue(window.rect().contains(control.mapTo(window, control.rect().center())))
                self.assertIn("確認済み比較", panel.status.text())
                if capture := os.environ.get("MDRL_CARD_CAPTURE"):
                    root = Path(capture)
                    root.mkdir(parents=True, exist_ok=True)
                    self.assertTrue(window.grab().save(str(root / "main-card-statistics.png")))
            finally:
                window.close()
            return 0

        with patch.object(QApplication, "exec", lambda app: inspect(app)):
            self.assertEqual(pyside_gui.main(["--user-data-dir", str(Path(self.temporary.name))]), 0)
        self.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
