"""通常GUIの値・操作・表示領域を隔離DBと実widgetで照合する。"""

from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService, DuelManagementQuery
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.duel_statistics import StatisticsFilter


class AuditStatisticsTest(unittest.TestCase):
    def test_result_filter_keeps_overall_and_filters_before_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = RecorderApplicationService(user_data_dir=Path(temporary))
            for index in range(3):
                service.create_manual_duel_record(
                    DuelRecordValues(status="draft" if index == 0 else "confirmed",
                                     result="win" if index == 1 else "loss"),
                    occurred_at=datetime(2026, 9, index + 1, tzinfo=timezone.utc),
                )
            dashboard = service.get_statistics_dashboard(StatisticsFilter(result="loss"))
            self.assertEqual((dashboard.overall.matches, dashboard.filtered.matches), (2, 1))
            self.assertEqual(dashboard.filtered.win_rate, 0)
            views = service.list_history_views(query=DuelManagementQuery(limit=1, incomplete_only=True))
            self.assertEqual(views[0].duel_record.values.status, "draft")
        with self.assertRaises(ValueError):
            StatisticsFilter(result="invalid")


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class AuditWidgetTest(unittest.TestCase):
    def test_normal_gui_statistics_settings_and_narrow_layout(self):
        from PySide6.QtCore import QDate, QPoint, QRect
        from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
        from master_duel_recorder_lite import pyside_gui

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = RecorderApplicationService(project_root=root, user_data_dir=root / "data")
            for index in range(12):
                service.create_manual_duel_record(
                    DuelRecordValues(status="confirmed" if index < 10 else "draft",
                                     result="win" if index < 8 else "loss",
                                     play_order="first" if index % 2 else "second"),
                    occurred_at=datetime(2026, 9, 1 + index // 2, tzinfo=timezone.utc),
                )

            def inspect(_app):
                app = QApplication.instance()
                window = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible())
                try:
                    widgets = window.widgets
                    window.show_page("statistics")
                    self.assertEqual(window.statistics_cards[0][0].text(), "80.0%")
                    self.assertEqual(window.statistics_cards[1][1].text(), "8勝 / 10戦")
                    widgets["statistics_filters"].setCurrentIndex(2)
                    self.assertEqual(window.statistics_cards[1][1].text(), "0勝 / 2戦")
                    widgets["statistics_granularity"].setCurrentText("月")
                    self.assertEqual(len(widgets["statistics_chart"].points), 1)
                    widgets["statistics_date_from_picker"].setDate(QDate(2030, 1, 1))
                    widgets["statistics_date_to_picker"].setDate(QDate(2030, 1, 31))
                    widgets["statistics_period_enabled"].setChecked(True)
                    self.assertEqual(len(widgets["statistics_chart"].points), 0)
                    self.assertEqual(window.statistics_cards[1][1].text(), "0勝 / 0戦")
                    widgets["statistics_date_to_picker"].setDate(QDate(2029, 1, 1))
                    self.assertIn("条件を適用できません", window.statistics_condition_status.text())

                    window.show_page("settings")
                    window.settings_tabs.setCurrentIndex(1)
                    widgets["settings_preroll_enabled"].click()
                    self.assertTrue(window._settings_dirty())
                    self.assertTrue(widgets["settings_save"].isVisible())
                    window.show_page("record")
                    window.show_page("settings")
                    self.assertTrue(widgets["settings_preroll_enabled"].isChecked())
                    with patch.object(window.service, "save_settings", side_effect=OSError("test")), patch.object(window, "_show_warning"):
                        self.assertFalse(window.save_settings())
                        self.assertTrue(window._settings_dirty())
                    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
                        self.assertFalse(window.close())
                    self.assertTrue(window.save_settings())
                    self.assertFalse(window._settings_dirty())
                    self.assertTrue(service.load_config().config.preroll_enabled)

                    window.show_page("history")
                    widgets["history_incomplete"].click()
                    self.assertEqual(widgets["history_table"].rowCount(), 2)
                    self.assertEqual(widgets["history_table"].item(0, 10).text(), "編集中")
                    widgets["history_filter_clear"].click()
                    self.assertEqual(widgets["history_table"].rowCount(), 12)
                    self.assertEqual(widgets["history_table"].horizontalHeader().visualIndex(11), 0)
                    self.assertEqual(widgets["history_table"].horizontalHeader().visualIndex(10), 11)

                    for width in (980, 1180):
                        window.resize(width, 760)
                        window.show_page("record")
                        widgets["target_selector"].addItem("長いウィンドウ名" * 80)
                        widgets["target_selector"].setCurrentIndex(widgets["target_selector"].count() - 1)
                        page = window.pages["record"]
                        page.verticalScrollBar().setValue(0)
                        app.processEvents()
                        self.assertEqual(page.horizontalScrollBar().maximum(), 0)
                        for key in ("record_start", "record_stop", "watch_toggle", "record_status_band"):
                            w = widgets[key]
                            self.assertTrue(page.viewport().rect().contains(QRect(w.mapTo(page.viewport(), QPoint()), w.size())), key)
                        window.show_page("history")
                        app.processEvents()
                        self.assertEqual(window.pages["history"].horizontalScrollBar().maximum(), 0)
                        self.assertGreater(widgets["history_table"].height(), 310)
                finally:
                    window.settings_baseline = None
                    window.close()
                return 0

            with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}), patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(root / "data")]), 0)
