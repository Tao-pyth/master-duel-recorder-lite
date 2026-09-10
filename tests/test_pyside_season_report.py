from datetime import date, datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.pyside_season_report import create_season_report_dialog


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class SeasonReportWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.service = RecorderApplicationService(project_root=self.root, user_data_dir=self.root / "data")
        self.season = self.service.add_season(
            name="検証シーズン", season_type="ranked", duel_type="ranked",
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 7), report_notes="<b>日本語のメモ</b>",
        )
        self.add_record("win", 1)
        self.add_record("loss", 2)
        self.dialog = create_season_report_dialog(None, self.service, self.service.get_season_report(self.season.season_id))
        self.addCleanup(self.dialog.close)
        self.addCleanup(self.dialog.deleteLater)

    def add_record(self, result, day):
        self.service.create_manual_duel_record(
            DuelRecordValues(status="confirmed", result=result, own_deck="検証デッキ", season_id=self.season.season_id),
            occurred_at=datetime(2026, 9, day, 12, tzinfo=timezone.utc),
        )

    def test_details_empty_comparison_and_read_only_layout(self):
        from PySide6.QtWidgets import QAbstractItemView
        self.dialog.show()
        self.app.processEvents()
        text = self.dialog.summary.toPlainText()
        for value in ("2戦 1勝 1敗", "50.0%", "比較なし", "少数標本", "<b>日本語のメモ</b>"):
            self.assertIn(value, text)
        self.assertTrue(self.dialog.summary.isReadOnly())
        self.assertEqual(self.dialog.tables["daily"].rowCount(), 7)
        self.assertEqual(self.dialog.tables["daily"].item(1, 6).text(), "50.0%")
        for table in self.dialog.tables.values():
            self.assertEqual(table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
        self.dialog.resize(740, 480)
        self.app.processEvents()
        for button in (self.dialog.export_button, self.dialog.close_button):
            self.assertTrue(button.isVisible())
            self.assertTrue(self.dialog.rect().contains(button.geometry()))
        empty = self.service.add_season(name="空シーズン", season_type="ranked", duel_type="ranked", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3))
        report = self.service.get_season_report(empty.season_id)
        other = create_season_report_dialog(None, self.service, report)
        try:
            self.assertIn("0戦 0勝 0敗", other.summary.toPlainText())
            self.assertIn("勝率 未算出", other.summary.toPlainText())
            self.assertIn("既定の比較対象: 検証シーズン", other.summary.toPlainText())
            self.assertIn("勝率差: 未算出", other.summary.toPlainText())
            self.assertEqual(other.tables["daily"].rowCount(), 3)
        finally:
            other.close()
            other.deleteLater()
        with self.assertRaises(ValueError):
            self.dialog.set_report(report)
        saved_season = next(item for item in self.service.list_seasons() if item.season_id == self.season.season_id)
        self.assertEqual(saved_season.report_notes, self.season.report_notes)

    def test_export_refresh_cancel_overwrite_and_failures(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        destination = self.root / "report.html"
        self.add_record("win", 3)
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
            self.dialog.export_button.click()
        self.assertIn("HTMLを保存しました", self.dialog.status.text())
        self.assertIn("3戦 2勝", self.dialog.summary.toPlainText())
        self.assertIn("66.7%", destination.read_text(encoding="utf-8"))
        before = destination.read_bytes()
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")), patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.dialog.export_html()
        self.assertEqual(destination.read_bytes(), before)
        self.assertIn("取り消し", self.dialog.status.text())
        self.add_record("loss", 4)
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")), patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.dialog.export_html()
        self.assertIn("4戦 2勝", self.dialog.summary.toPlainText())
        before = destination.read_bytes()
        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")), patch.object(self.service, "export_season_report") as export:
            self.dialog.export_html()
            export.assert_not_called()
        for failure in ("get_season_report", "export_season_report"):
            with self.subTest(failure=failure), patch.object(QFileDialog, "getSaveFileName", return_value=(str(destination), "")), patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, "warning"), patch.object(self.service, failure, side_effect=OSError("test failure")):
                self.dialog.export_html()
                self.assertNotIn("HTMLを保存しました", self.dialog.status.text())
                self.assertEqual(destination.read_bytes(), before)
        invalid = self.root / "report.txt"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(invalid), "")), patch.object(QMessageBox, "warning") as warning:
            self.dialog.export_html()
            warning.assert_called_once()
        self.assertFalse(invalid.exists())

    def test_main_season_button_uses_selected_report_and_handles_missing_selection(self):
        from PySide6.QtWidgets import QApplication, QMainWindow
        from master_duel_recorder_lite import pyside_gui

        def inspect(_app):
            window = next(w for w in self.app.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible())
            try:
                window.show_page("seasons")
                with patch.object(window, "_show_information") as info:
                    window.selected_season_id = None
                    window.widgets["season_report"].click()
                    info.assert_called_once()
                window.selected_season_id = self.season.season_id
                with patch("master_duel_recorder_lite.pyside_season_report.create_season_report_dialog") as create:
                    window.widgets["season_report"].click()
                    self.assertEqual(create.call_args.args[2].season.season_id, self.season.season_id)
                    create.return_value.exec.assert_called_once()
                with patch.object(window.service, "get_season_report", side_effect=OSError("missing")), patch.object(window, "_show_warning") as warning:
                    window.widgets["season_report"].click()
                    warning.assert_called_once()
            finally:
                window.close()
            return 0

        with patch.object(QApplication, "exec", inspect):
            self.assertEqual(pyside_gui.main(["--project-root", str(self.root), "--user-data-dir", str(self.root / "data")]), 0)
