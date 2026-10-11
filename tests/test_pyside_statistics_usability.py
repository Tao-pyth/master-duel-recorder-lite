"""相性の局所条件と、統計画面の比較しやすさを隔離DBと実widgetで確認する。"""

from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.duel_records import DuelRecordValues


LONG_OWN = "白き森とアザミナの魔女を組み合わせた調和デッキ"
LONG_OPPONENT = "神碑とスプライトを組み合わせたコントロールデッキ"
FIXTURES = (
    ("青眼", "烙印", "ranked", "win", 1, "first", "confirmed"),
    ("青眼", "烙印", "ranked", "loss", 2, "second", "confirmed"),
    ("青眼", "烙印", "ranked", "draw", 2, "second", "confirmed"),
    ("青眼", "神碑", "ranked", "win", 2, "first", "confirmed"),
    ("青眼", "", "ranked", "loss", 2, "unknown", "confirmed"),
    ("青眼", "未設定", "ranked", "win", 2, "first", "confirmed"),
    ("ＢＬＵＥ", "ＲＥＤ", "event", "win", 2, "first", "confirmed"),
    ("blue", "red", "event", "loss", 3, "second", "confirmed"),
    ("", "烙印", "ranked", "loss", 2, "unknown", "confirmed"),
    ("未設定", "烙印", "ranked", "win", 2, "first", "confirmed"),
    (LONG_OWN, LONG_OPPONENT, "room", "draw", 2, "unknown", "confirmed"),
    ("青眼", "烙印", "ranked", "win", 2, "first", "draft"),
)
OTHER_TABLES = (
    "statistics_deck_table", "statistics_order_table", "statistics_coin_table",
    "statistics_duel_type_table", "statistics_season_table", "statistics_trend_table",
)


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class StatisticsUsabilityWidgetTest(unittest.TestCase):
    def inspect_statistics(self, callback):
        from PySide6.QtWidgets import QApplication, QMainWindow
        from master_duel_recorder_lite import pyside_gui

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = RecorderApplicationService(project_root=root, user_data_dir=root / "data")
            try:
                for own, opponent, kind, result, day, order, status in FIXTURES:
                    service.create_manual_duel_record(
                        DuelRecordValues(own_deck=own, opponent_deck=opponent,
                                         duel_type=kind, result=result,
                                         play_order=order, status=status),
                        occurred_at=datetime(2026, 9, day, 12, tzinfo=timezone.utc),
                    )
            finally:
                service.close()

            def inspect(_app):
                app = QApplication.instance()
                window = next(w for w in app.topLevelWidgets()
                              if isinstance(w, QMainWindow) and w.isVisible())
                try:
                    window.show_page("statistics")
                    callback(app, window)
                finally:
                    window.close()
                return 0

            platform = os.environ.get("MDRL_TEST_QPA", "offscreen")
            with patch.dict(os.environ, {"QT_QPA_PLATFORM": platform}), \
                    patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main([
                    "--project-root", str(root), "--user-data-dir", str(root / "data"),
                ]), 0)

    @staticmethod
    def table_contents(table):
        return tuple(tuple(table.item(row, col).text() for col in range(table.columnCount()))
                     for row in range(table.rowCount()))

    def common_contents(self, window):
        return (
            tuple(tuple(label.text() for label in card) for card in window.statistics_cards),
            tuple(self.table_contents(window.widgets[key]) for key in OTHER_TABLES),
            tuple(window.widgets["statistics_chart"].points),
            window.statistics_condition_status.text(),
        )

    def select_own(self, combo, key):
        index = next((index for index in range(combo.count()) if combo.itemData(index) == key), -1)
        self.assertGreaterEqual(index, 0, f"自分デッキの選択肢がありません: {key}")
        combo.setCurrentIndex(index)
        self.assertEqual(combo.currentData(), key)

    def assert_matchups(self, table, expected):
        # 表示名が同じでも各行を消さずに比較する。
        actual = sorted(tuple(table.item(row, col).text() for col in range(7))
                        for row in range(table.rowCount()))
        self.assertEqual(actual, sorted(expected))

    def test_combined_matchup_filters_preserve_common_statistics_and_reset(self):
        from PySide6.QtCore import QDate

        def inspect(app, window):
            widgets = window.widgets
            table = widgets["statistics_matchup_table"]
            own = widgets["statistics_matchup_own_deck"]
            search = widgets["statistics_matchup_search"]
            unassigned = widgets["statistics_matchup_show_unassigned"]
            minimum = widgets["statistics_matchup_min_matches"]
            reset = widgets["statistics_matchup_reset"]
            common = self.common_contents(window)
            self.assertEqual(window.statistics_cards[0][1].text(), "5勝 / 11戦")
            self.assertEqual(table.rowCount(), 8)
            self.assertIsNone(own.currentData())
            self.assertTrue(unassigned.isChecked())
            self.assertEqual(minimum.value(), 0)
            self.assertIn("共通条件 8組合せ / 11戦", window.statistics_matchup_status.text())
            self.assertIn("相手未設定 1組合せ / 1戦", window.statistics_matchup_status.text())

            self.select_own(own, ("青眼", False))
            search.setText("烙")
            unassigned.setChecked(False)
            minimum.setValue(2)
            app.processEvents()
            self.assert_matchups(table, [("烙印", "青眼", "3", "1", "1", "1", "33.3%")])
            self.assertEqual(self.common_contents(window), common)
            self.assertIn("表示 1組合せ / 3戦", window.statistics_matchup_status.text())

            types = widgets["statistics_filters"]
            types.setCurrentIndex(types.findData("ranked"))
            widgets["statistics_date_from_picker"].setDate(QDate(2026, 9, 2))
            widgets["statistics_date_to_picker"].setDate(QDate(2026, 9, 2))
            widgets["statistics_period_enabled"].setChecked(True)
            app.processEvents()
            self.assertEqual(own.currentData(), ("青眼", False))
            self.assertEqual(search.text(), "烙")
            self.assertFalse(unassigned.isChecked())
            self.assertEqual(minimum.value(), 2)
            self.assert_matchups(table, [("烙印", "青眼", "2", "0", "1", "1", "0.0%")])
            self.assertEqual(window.statistics_cards[0][1].text(), "5勝 / 11戦")
            self.assertEqual(window.statistics_cards[1][1].text(), "3勝 / 7戦")
            self.assertIn("共通条件 6組合せ / 7戦", window.statistics_matchup_status.text())
            filtered_common = self.common_contents(window)

            minimum.setValue(3)
            app.processEvents()
            self.assertEqual(table.rowCount(), 0)
            self.assertIn("0組合せ / 0戦", window.statistics_matchup_status.text())
            self.assertEqual(self.common_contents(window), filtered_common)
            reset.click()
            app.processEvents()
            self.assertIsNone(own.currentData())
            self.assertEqual(search.text(), "")
            self.assertTrue(unassigned.isChecked())
            self.assertEqual(minimum.value(), 0)
            self.assertEqual(table.rowCount(), 6)
            self.assertEqual(self.common_contents(window), filtered_common)
            self.assertTrue(widgets["statistics_period_enabled"].isChecked())
            self.assertEqual(types.currentData(), "ranked")

            widgets["statistics_period_enabled"].setChecked(False)
            types.setCurrentIndex(0)
            app.processEvents()
            self.assertEqual(self.common_contents(window), common)
            self.assertEqual(table.rowCount(), 8)
            self.select_own(own, ("blue", False))
            search.setText("  ＲｅＤ  ")
            app.processEvents()
            self.assert_matchups(table, [("ＲＥＤ", "ＢＬＵＥ", "2", "1", "1", "0", "50.0%")])
            self.assertEqual(self.common_contents(window), common)
            reset.click()

            self.select_own(own, ("未設定", False))
            self.assertEqual(own.currentText(), "未設定（デッキ名）")
            self.assert_matchups(table, [("烙印", "未設定（デッキ名）", "1", "1", "0", "0", "100.0%")])
            self.select_own(own, ("未設定", True))
            self.assertEqual(own.currentText(), "未設定")
            unassigned.setChecked(False)
            self.assert_matchups(table, [("烙印", "未設定", "1", "0", "1", "0", "0.0%")])
            self.select_own(own, ("青眼", False))
            search.setText("未設定")
            self.assert_matchups(table, [("未設定（デッキ名）", "青眼", "1", "1", "0", "0", "100.0%")])
            self.assertEqual(self.common_contents(window), common)

            search.setText("該当する相手がいない検索語")
            self.assertEqual(table.rowCount(), 0)
            self.assertIn("0組合せ / 0戦", window.statistics_matchup_status.text())
            self.assertEqual(self.common_contents(window), common)
            reset.click()
            self.assertEqual(table.rowCount(), 8)
            self.select_own(own, ("青眼", False))
            widgets["statistics_date_to_picker"].setDate(QDate(2030, 1, 1))
            widgets["statistics_date_from_picker"].setDate(QDate(2030, 1, 1))
            widgets["statistics_period_enabled"].setChecked(True)
            self.assertEqual(table.rowCount(), 0)
            self.assertEqual(own.currentData(), ("青眼", False))
            self.assertIn("表示できる確定済み対戦がありません", window.statistics_matchup_status.text())
            self.assertEqual(window.statistics_cards[0][1].text(), "5勝 / 11戦")
            self.assertEqual(window.statistics_cards[1][0].text(), "-")
            widgets["statistics_period_enabled"].setChecked(False)
            self.assertEqual(own.currentData(), ("青眼", False))
            self.assertEqual(table.rowCount(), 4)
            reset.click()
            self.assertEqual(table.rowCount(), 8)

        self.inspect_statistics(inspect)

    def test_compact_header_dates_and_matchup_controls_remain_readable_at_small_sizes(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        def inspect(app, window):
            widgets = window.widgets
            table = widgets["statistics_matchup_table"]
            tabs = widgets["statistics_tab_panel"]
            page = window.pages["statistics"]
            self.assertEqual(tabs.count(), 8)
            self.assertEqual([table.horizontalHeaderItem(col).text() for col in range(table.columnCount())],
                             ["相手デッキ", "自分デッキ", "対戦", "勝利", "敗北", "引分", "自分勝率", "参考"])
            self.assertTrue(all(table.item(row, 7).text() == "少数" for row in range(table.rowCount())))
            capture = os.environ.get("MDRL_STATISTICS_USABILITY_CAPTURE_DIR")
            for width, height in ((1180, 760), (980, 640)):
                with self.subTest(width=width, height=height):
                    window.resize(width, height)
                    matchup_index = next(index for index in range(tabs.count())
                                         if tabs.tabText(index) == "デッキ相性")
                    tabs.setCurrentIndex(matchup_index)
                    page.verticalScrollBar().setValue(0)
                    app.processEvents()
                    self.assertEqual(page.horizontalScrollBar().maximum(), 0)
                    self.assertGreaterEqual(table.height(), 180)
                    for value, detail in window.statistics_cards:
                        self.assertLessEqual(value.parentWidget().height(), 96)
                        self.assertTrue(value.isVisible())
                        self.assertTrue(detail.isVisible())
                    for key in ("statistics_date_from_picker", "statistics_date_to_picker"):
                        picker = widgets[key]
                        self.assertEqual(picker.displayFormat(), "yyyy-MM-dd")
                        self.assertTrue(picker.calendarPopup())
                        self.assertGreaterEqual(picker.width(), picker.fontMetrics().horizontalAdvance("2026-09-02") + 28)
                    for key in ("statistics_matchup_own_deck", "statistics_matchup_search",
                                "statistics_matchup_show_unassigned", "statistics_matchup_min_matches",
                                "statistics_matchup_reset"):
                        control = widgets[key]
                        self.assertTrue(control.isVisible(), key)
                        self.assertGreaterEqual(control.width(), 30, key)
                        self.assertLessEqual(control.mapTo(page.viewport(), control.rect().bottomRight()).y(),
                                             page.viewport().height(), key)
                    for row in range(table.rowCount()):
                        for col in range(2, 7):
                            self.assertTrue(table.item(row, col).textAlignment() & Qt.AlignmentFlag.AlignRight)
                        for col in (0, 1):
                            self.assertEqual(table.item(row, col).toolTip(), table.item(row, col).text())
                    for index in range(tabs.count()):
                        tabs.setCurrentIndex(index)
                        app.processEvents()
                        tabbar = tabs.tabBar()
                        self.assertTrue(tabbar.rect().contains(tabbar.tabRect(index).center()))
                        QTest.mouseClick(tabbar, Qt.MouseButton.LeftButton,
                                         pos=tabbar.tabRect(index).center())
                        self.assertEqual(tabs.currentIndex(), index)
                    tabs.setCurrentIndex(matchup_index)
                    app.processEvents()
                    self.assertLess(table.mapTo(page.viewport(), QPoint(0, 0)).y(), page.viewport().height() - 150)
                    if capture:
                        directory = Path(capture)
                        directory.mkdir(parents=True, exist_ok=True)
                        self.assertTrue(window.grab().save(str(directory / f"statistics-usability-{width}x{height}.png")))

        self.inspect_statistics(inspect)
