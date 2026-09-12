"""3管理ページの実widgetと隔離DBで、編集と遷移を検証する。"""

from datetime import date
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class CatalogRedesignTest(unittest.TestCase):
    def run_gui(self, scenario):
        from PySide6.QtWidgets import QApplication, QMainWindow
        from master_duel_recorder_lite import pyside_gui
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = RecorderApplicationService(project_root=root, user_data_dir=root / "data")
            for name in ("白き森エルフェンノーツ", "青眼"):
                service.add_deck(name)
                service.add_tag(name)
                service.add_season(name=name, season_type="ranked", duel_type="ranked",
                    start_date=date(2026, 9, 1), end_date=date(2026, 9, 30), report_notes="維持するメモ")

            def inspect(_app):
                app = QApplication.instance()
                w = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible())
                w.record_state_timer.stop()
                try:
                    scenario(w, service, app)
                finally:
                    for page in w.catalog_pages.values():
                        page.baseline = None
                    w.history_editor.baseline = None
                    w.settings_baseline = None
                    w.close()
                    w.deleteLater()
                    app.processEvents()
                return 0
            with patch.dict(os.environ, {"QT_QPA_PLATFORM": os.environ.get("MDRL_TEST_QPA", "offscreen")}), patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(root / "data")]), 0)

    def test_three_pages_create_update_filter_and_field_roundtrip(self):
        def scenario(w, service, app):
            for key, page in w.catalog_pages.items():
                w.show_page(key)
                page.new()
                self.assertTrue(page.creating)
                page.fields["name_input"].setText("追加確認")
                page.fields["description_input"].setText("日本語説明")
                if key == "decks":
                    page.fields["opponent_only"].setChecked(True)
                    page.fields["hidden_from_history"].setChecked(True)
                elif key == "tags":
                    page.fields["deck_only"].setChecked(True)
                if key != "seasons":
                    w._set_color_button(page.color, "#123456")
                self.assertTrue(page.save())
                saved_id = page.selected_id
                self.assertIsNotNone(saved_id)
                self.assertFalse(page.dirty())
                page.fields["name_input"].setText("更新確認")
                self.assertTrue(page.save())
                self.assertEqual(page.selected_id, saved_id)
                entry = page.entries[saved_id]
                self.assertEqual(entry.name, "更新確認")
                self.assertEqual(entry.description, "日本語説明")
                if key != "seasons":
                    self.assertEqual(entry.color.upper(), "#123456")
                if key == "decks":
                    self.assertTrue(entry.opponent_only)
                    self.assertTrue(entry.hidden_from_history_statistics)
                if key == "tags":
                    self.assertTrue(entry.deck_only)
                page.search.setText("存在しない")
                self.assertEqual(page.table.rowCount(), 0)
                page.search.clear()
                self.assertGreaterEqual(page.table.rowCount(), 3)
                page.filter.setCurrentIndex(2)
                self.assertEqual(page.table.rowCount(), 0 if key == "seasons" else 1)
                page.filter.setCurrentIndex(0)
        self.run_gui(scenario)

    def test_cancel_and_failed_save_protect_all_pages(self):
        from PySide6.QtWidgets import QMessageBox
        def scenario(w, service, app):
            for key, page in w.catalog_pages.items():
                w.show_page(key)
                page.table.selectRow(0)
                before = page.selected_id
                page.fields["name_input"].setText("保存前の入力")
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
                    page.table.selectRow(1)
                    page.search.setText("青眼")
                    page.filter.setCurrentIndex(1)
                    page.new()
                    w.show_page("record")
                    self.assertFalse(w.close())
                self.assertEqual(page.selected_id, before)
                self.assertEqual(page.search.text(), "")
                self.assertEqual(page.filter.currentIndex(), 0)
                self.assertEqual(w.stack.currentWidget(), w.pages[key])
                page.load()
                self.assertEqual(page.fields["name_input"].text(), "保存前の入力")
                method = {"decks": "update_deck", "tags": "update_tag", "seasons": "update_season"}[key]
                with patch.object(w.service, method, side_effect=RuntimeError("書込失敗")), patch.object(w, "_show_warning") as warning:
                    self.assertFalse(page.save())
                    warning.assert_called_once()
                self.assertTrue(page.dirty())
                self.assertEqual(page.fields["name_input"].text(), "保存前の入力")
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
                    self.assertTrue(page.allow_leave())
                self.assertFalse(page.dirty())
        self.run_gui(scenario)

    def test_layout_and_native_capture(self):
        from PySide6.QtCore import QPoint, QRect
        def scenario(w, service, app):
            for key, page in w.catalog_pages.items():
                w.show_page(key)
                page.table.selectRow(0)
                for width, height in ((980, 640), (1180, 760), (1440, 1024)):
                    w.resize(width, height)
                    app.processEvents()
                    self.assertGreater(page.splitter.widget(1).width(), 300)
                    self.assertGreater(page.table.width(), 200)
                    self.assertTrue(page.save_button.isVisible())
                    scroll = page.splitter.widget(1)
                    scroll.ensureWidgetVisible(page.save_button)
                    app.processEvents()
                    rect = QRect(page.save_button.mapTo(scroll.viewport(), QPoint()), page.save_button.size())
                    self.assertTrue(scroll.viewport().rect().contains(rect))
                    capture = os.environ.get("MDRL_CATALOG_CAPTURE")
                    if capture:
                        dest = Path(capture)
                        dest.mkdir(parents=True, exist_ok=True)
                        self.assertTrue(w.grab().save(str(dest / f"{key}-{width}x{height}.png")))
                capture = os.environ.get("MDRL_CATALOG_CAPTURE")
                if capture:
                    dest = Path(capture)
                    dest.mkdir(parents=True, exist_ok=True)
                    self.assertTrue(w.grab().save(str(dest / f"{key}.png")))
        self.run_gui(scenario)

    def test_save_on_move_delete_and_season_report_actions(self):
        from PySide6.QtWidgets import QMessageBox
        def scenario(w, service, app):
            for key, page in w.catalog_pages.items():
                w.show_page(key)
                page.table.selectRow(0)
                identifier = page.selected_id
                page.fields["description_input"].setText("移動時保存")
                with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
                    page.table.selectRow(1)
                self.assertNotEqual(page.selected_id, identifier)
                self.assertEqual(page.entries[identifier].description, "移動時保存")
                if key == "seasons":
                    self.assertEqual(page.entries[identifier].report_notes, "維持するメモ")
                    from master_duel_recorder_lite import pyside_season_report
                    with patch.object(pyside_season_report, "create_season_report_dialog") as factory:
                        page.report()
                        factory.return_value.exec.assert_called_once()
                    archived = page.selected_id
                    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                        page.remove()
                    self.assertTrue(page.entries[archived].is_archived)
                    self.assertFalse(page.remove_button.isEnabled())
                else:
                    count = len(page.entries)
                    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
                        page.remove()
                    self.assertEqual(len(page.entries), count)
                    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                        page.remove()
                    self.assertEqual(len(page.entries), count - 1)
        self.run_gui(scenario)

    def test_partial_deck_creation_retries_same_entry_and_invalid_dates(self):
        def scenario(w, service, app):
            page = w.catalog_pages["decks"]
            w.show_page("decks")
            count = len(page.entries)
            page.new()
            page.fields["name_input"].setText("部分追加")
            page.fields["opponent_only"].setChecked(True)
            with patch.object(w.service, "update_deck", side_effect=RuntimeError("設定書込失敗")), patch.object(w, "_show_warning"):
                self.assertFalse(page.save())
            self.assertIsNotNone(page.selected_id)
            self.assertTrue(page.dirty())
            self.assertTrue(page.save())
            self.assertEqual(len(page.entries), count + 1)
            self.assertTrue(page.entries[page.selected_id].opponent_only)
            page = w.catalog_pages["seasons"]
            w.show_page("seasons")
            page.table.selectRow(0)
            before = page.entries[page.selected_id]
            page.fields["start_date_picker"].setDate(page.fields["end_date_picker"].date().addDays(1))
            with patch.object(w, "_show_warning") as warning:
                self.assertFalse(page.save())
                warning.assert_called_once()
            self.assertTrue(page.dirty())
            self.assertEqual(next(s for s in service.list_seasons() if s.season_id == before.season_id).start_date, before.start_date)
        self.run_gui(scenario)

