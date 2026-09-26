"""通常GUIの値・操作・表示領域を隔離DBと実widgetで照合する。"""

from datetime import date, datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService, DuelManagementQuery
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.duel_statistics import StatisticsFilter
from master_duel_recorder_lite.seasons import SeasonRepository


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
    def test_statistics_duel_type_selection_all_tabs_and_season_order(self):
        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import QApplication, QMainWindow
        from master_duel_recorder_lite import pyside_gui

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = RecorderApplicationService(project_root=root, user_data_dir=root / "data")
            seasons = SeasonRepository.from_runtime_paths(service.paths)
            old = seasons.add(name="A 前シーズン", season_type="ranked", duel_type="ranked",
                              start_date=date(2026, 8, 1), end_date=date(2026, 8, 31))
            new = seasons.add(name="Z 今シーズン", season_type="ranked", duel_type="ranked",
                              start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))
            for kind, result, day, season_id in (
                ("ranked", "win", 1, old.season_id),
                ("ranked", "loss", 2, new.season_id),
                ("event", "win", 2, None),
                ("room", "draw", 2, None),
                ("solo", "loss", 2, None),
                ("other", "loss", 2, None),
            ):
                service.create_manual_duel_record(
                    DuelRecordValues(status="confirmed", result=result, duel_type=kind,
                                     own_deck="青眼", play_order="first", season_id=season_id),
                    occurred_at=datetime(2026, 9, day, 12, tzinfo=timezone.utc),
                )

            def inspect(_app):
                app = QApplication.instance()
                window = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible())
                try:
                    window.show_page("statistics")
                    widgets = window.widgets
                    selector = widgets["statistics_filters"]
                    self.assertEqual([selector.itemText(i) for i in range(selector.count())],
                                     ["すべての対戦種別", "ランク戦", "イベント", "ルーム戦", "ソロモード", "その他"])
                    types = widgets["statistics_duel_type_table"]
                    self.assertEqual(types.rowCount(), 5)
                    self.assertEqual([types.item(0, col).text() for col in range(4)], ["ランク戦", "2", "1", "50.0%"])
                    season_table = widgets["statistics_season_table"]
                    self.assertEqual([season_table.item(i, 0).text() for i in range(3)],
                                     ["Z 今シーズン", "A 前シーズン", "シーズン未設定"])
                    tabs = widgets["statistics_tab_panel"]
                    capture = os.environ.get("MDRL_STATISTICS_CAPTURE_DIR")
                    for title in ("対戦種別別", "シーズン別"):
                        tabs.setCurrentIndex(next(i for i in range(tabs.count()) if tabs.tabText(i) == title))
                        app.processEvents()
                        if capture:
                            self.assertTrue(window.grab().save(str(Path(capture) / f"{title}.png")))
                    selector.setCurrentIndex(selector.findData("ranked"))
                    self.assertEqual(window.statistics_cards[0][1].text(), "2勝 / 6戦")
                    self.assertEqual(window.statistics_cards[1][0].text(), "50.0%")
                    for key in ("statistics_deck_table", "statistics_order_table", "statistics_coin_table",
                                "statistics_duel_type_table", "statistics_season_table", "statistics_trend_table"):
                        table = widgets[key]
                        self.assertEqual(sum(int(table.item(i, 1).text()) for i in range(table.rowCount())), 2, key)
                    widgets["statistics_date_from_picker"].setDate(QDate(2026, 9, 2))
                    widgets["statistics_date_to_picker"].setDate(QDate(2026, 9, 2))
                    widgets["statistics_period_enabled"].setChecked(True)
                    self.assertEqual(window.statistics_cards[1][1].text(), "0勝 / 1戦")
                    self.assertEqual(types.rowCount(), 1)
                    self.assertEqual(types.item(0, 1).text(), "1")
                    self.assertEqual(season_table.item(0, 0).text(), "Z 今シーズン")
                    widgets["statistics_date_to_picker"].setDate(QDate(2030, 1, 1))
                    widgets["statistics_date_from_picker"].setDate(QDate(2030, 1, 1))
                    self.assertEqual(types.rowCount(), 0)
                    self.assertEqual(season_table.rowCount(), 0)
                    self.assertEqual(len(widgets["statistics_chart"].points), 0)
                    selector.setCurrentIndex(0)
                    widgets["statistics_period_enabled"].setChecked(False)
                    self.assertEqual(types.rowCount(), 5)
                    self.assertEqual(window.statistics_cards[1][1].text(), "2勝 / 6戦")
                finally:
                    window.close()
                return 0

            platform = os.environ.get("MDRL_TEST_QPA", "offscreen")
            with patch.dict(os.environ, {"QT_QPA_PLATFORM": platform}), patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(root / "data")]), 0)

    def test_statistics_deck_play_order_rows_and_filters(self):
        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget
        from master_duel_recorder_lite import pyside_gui

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = RecorderApplicationService(project_root=root, user_data_dir=root / "data")
            fixtures = (
                ("青眼", "first", "win", 1, "confirmed"),
                ("青眼", "first", "loss", 1, "confirmed"),
                ("青眼", "second", "loss", 2, "confirmed"),
                ("妖精と魔女", "first", "win", 2, "confirmed"),
                ("妖精と魔女", "second", "win", 2, "confirmed"),
                ("", "unknown", "loss", 2, "confirmed"),
                ("青眼", "first", "win", 1, "draft"),
            )
            for deck, order, result, day, status in fixtures:
                service.create_manual_duel_record(
                    DuelRecordValues(own_deck=deck, play_order=order, result=result, status=status,
                                     duel_type="ranked" if result == "loss" else "event"),
                    occurred_at=datetime(2026, 9, day, 12, tzinfo=timezone.utc),
                )

            def inspect(_app):
                app = QApplication.instance()
                window = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible())
                try:
                    window.show_page("statistics")
                    widgets = window.widgets
                    table = widgets["statistics_order_table"]

                    def rows():
                        return {
                            table.item(row, 0).text(): tuple(table.item(row, col).text() for col in range(1, 4))
                            for row in range(table.rowCount())
                        }

                    self.assertEqual(table.horizontalHeaderItem(0).text(), "自分デッキ・先後")
                    expected = {
                        "青眼 先攻時": ("2", "1", "50.0%"),
                        "青眼 後攻時": ("1", "0", "0.0%"),
                        "妖精と魔女 先攻時": ("1", "1", "100.0%"),
                        "妖精と魔女 後攻時": ("1", "1", "100.0%"),
                        "未設定 未設定": ("1", "0", "0.0%"),
                    }
                    self.assertEqual(rows(), expected)
                    self.assertEqual(window.statistics_cards[2][0].text(), "先攻 66.7% / 後攻 50.0%")
                    tabs = next(t for t in window.findChildren(QTabWidget)
                                if any(t.tabText(i) == "デッキ別・先攻／後攻" for i in range(t.count())))
                    tabs.setCurrentIndex(next(i for i in range(tabs.count())
                                              if tabs.tabText(i) == "デッキ別・先攻／後攻"))
                    app.processEvents()
                    capture = os.environ.get("MDRL_STATISTICS_CAPTURE")
                    if capture:
                        self.assertTrue(window.grab().save(capture))
                    widgets["statistics_filters"].setCurrentIndex(widgets["statistics_filters"].findData("ranked"))
                    self.assertEqual(rows(), {
                        "青眼 先攻時": ("1", "0", "0.0%"),
                        "青眼 後攻時": ("1", "0", "0.0%"),
                        "未設定 未設定": ("1", "0", "0.0%"),
                    })
                    widgets["statistics_filters"].setCurrentIndex(0)
                    widgets["statistics_date_from_picker"].setDate(QDate(2026, 9, 2))
                    widgets["statistics_date_to_picker"].setDate(QDate(2026, 9, 2))
                    widgets["statistics_period_enabled"].setChecked(True)
                    self.assertEqual(rows(), {key: value for key, value in expected.items() if key != "青眼 先攻時"})
                    widgets["statistics_date_to_picker"].setDate(QDate(2030, 1, 1))
                    widgets["statistics_date_from_picker"].setDate(QDate(2030, 1, 1))
                    self.assertEqual(table.rowCount(), 0)
                    widgets["statistics_period_enabled"].setChecked(False)
                    self.assertEqual(rows(), expected)
                finally:
                    window.close()
                return 0

            platform = os.environ.get("MDRL_TEST_QPA", "offscreen")
            with patch.dict(os.environ, {"QT_QPA_PLATFORM": platform}), patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(root / "data")]), 0)

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
                                     duel_type="ranked" if index < 8 else "event",
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
