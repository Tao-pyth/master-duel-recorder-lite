"""隔離した戦績と実GUIで、相手×自分の勝率と条件操作を確認する。"""

from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gui_test_helpers import close_gui_test_window, wait_for_gui_startup

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.duel_records import DuelRecordValues


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class MatchupStatisticsWidgetTest(unittest.TestCase):
    def test_matchup_tab_matches_filters_empty_state_and_narrow_layout(self):
        from PySide6.QtCore import QDate, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QMainWindow
        from master_duel_recorder_lite import pyside_gui

        long_own = "白き森とアザミナの魔女を組み合わせた調和デッキ"
        long_opponent = "神碑とスプライトを組み合わせたコントロールデッキ"
        fixtures = (
            ("青眼", "烙印", "ranked", "win", 1, "confirmed"),
            ("青眼", "烙印", "ranked", "loss", 2, "confirmed"),
            ("青眼", "烙印", "ranked", "draw", 2, "confirmed"),
            ("青眼", "神碑", "ranked", "win", 2, "confirmed"),
            (long_own, long_opponent, "event", "win", 2, "confirmed"),
            (long_own, long_opponent, "event", "loss", 3, "confirmed"),
            ("", "", "room", "loss", 2, "confirmed"),
            ("青眼", "烙印", "ranked", "win", 2, "draft"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = RecorderApplicationService(project_root=root, user_data_dir=root / "data")
            for own, opponent, kind, result, day, status in fixtures:
                service.create_manual_duel_record(
                    DuelRecordValues(own_deck=own, opponent_deck=opponent, duel_type=kind,
                                     result=result, status=status),
                    occurred_at=datetime(2026, 9, day, 12, tzinfo=timezone.utc),
                )

            def inspect(_app):
                app = QApplication.instance()
                window = next(w for w in app.topLevelWidgets()
                              if isinstance(w, QMainWindow) and w.isVisible())
                try:
                    wait_for_gui_startup(app, window)
                    window.show_page("statistics")
                    widgets = window.widgets
                    table = widgets["statistics_matchup_table"]
                    tabs = widgets["statistics_tab_panel"]
                    tab_index = next(i for i in range(tabs.count()) if tabs.tabText(i) == "デッキ相性")
                    self.assertEqual(
                        [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())],
                        ["相手デッキ", "自分デッキ", "対戦", "勝利", "敗北", "引分", "自分勝率", "参考"],
                    )

                    def rows():
                        return {
                            (table.item(row, 0).text(), table.item(row, 1).text()):
                                tuple(table.item(row, col).text() for col in range(2, 7))
                            for row in range(table.rowCount())
                        }

                    expected = {
                        ("烙印", "青眼"): ("3", "1", "1", "1", "33.3%"),
                        ("神碑", "青眼"): ("1", "1", "0", "0", "100.0%"),
                        (long_opponent, long_own): ("2", "1", "1", "0", "50.0%"),
                        ("未設定", "未設定"): ("1", "0", "1", "0", "0.0%"),
                    }
                    self.assertEqual(rows(), expected)
                    self.assertEqual(window.statistics_cards[0][1].text(), "3勝 / 7戦")
                    self.assertIn("4組合せ / 7戦", window.statistics_matchup_status.text())
                    selector = widgets["statistics_filters"]
                    selector.setCurrentIndex(selector.findData("ranked"))
                    self.assertEqual(rows(), {key: expected[key] for key in (("烙印", "青眼"), ("神碑", "青眼"))})
                    widgets["statistics_date_from_picker"].setDate(QDate(2026, 9, 2))
                    widgets["statistics_date_to_picker"].setDate(QDate(2026, 9, 2))
                    widgets["statistics_period_enabled"].setChecked(True)
                    self.assertEqual(rows(), {
                        ("烙印", "青眼"): ("2", "0", "1", "1", "0.0%"),
                        ("神碑", "青眼"): expected[("神碑", "青眼")],
                    })
                    self.assertEqual(window.statistics_cards[0][1].text(), "3勝 / 7戦")
                    widgets["statistics_date_to_picker"].setDate(QDate(2030, 1, 1))
                    widgets["statistics_date_from_picker"].setDate(QDate(2030, 1, 1))
                    self.assertEqual(table.rowCount(), 0)
                    self.assertIn("表示できる確定済み対戦がありません", window.statistics_matchup_status.text())
                    self.assertEqual(window.statistics_cards[1][0].text(), "-")
                    widgets["statistics_period_enabled"].setChecked(False)
                    selector.setCurrentIndex(0)
                    self.assertEqual(rows(), expected)

                    capture = os.environ.get("MDRL_MATCHUP_CAPTURE_DIR")
                    for width, height in ((1180, 760), (980, 640)):
                        window.resize(width, height)
                        tabs.setCurrentIndex(0)
                        app.processEvents()
                        tabbar = tabs.tabBar()
                        self.assertTrue(tabs.usesScrollButtons())
                        self.assertTrue(tabbar.rect().contains(tabbar.tabRect(tab_index).center()))
                        QTest.mouseClick(tabbar, Qt.MouseButton.LeftButton,
                                         pos=tabbar.tabRect(tab_index).center())
                        app.processEvents()
                        self.assertEqual(tabs.currentIndex(), tab_index)
                        self.assertTrue(table.isVisible())
                        self.assertGreater(table.height(), 100)
                        self.assertEqual(window.pages["statistics"].horizontalScrollBar().maximum(), 0)
                        for row in range(table.rowCount()):
                            for column in (0, 1):
                                self.assertEqual(table.item(row, column).toolTip(), table.item(row, column).text())
                        if width == 980:
                            self.assertGreater(table.horizontalScrollBar().maximum(), 0)
                            table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
                            app.processEvents()
                            self.assertGreater(table.horizontalScrollBar().value(), 0)
                            table.horizontalScrollBar().setValue(0)
                            app.processEvents()
                        if capture:
                            directory = Path(capture)
                            directory.mkdir(parents=True, exist_ok=True)
                            self.assertTrue(window.grab().save(str(directory / f"matchups-{width}x{height}.png")))
                finally:
                    close_gui_test_window(app, window)
                return 0

            platform = os.environ.get("MDRL_TEST_QPA", "offscreen")
            with patch.dict(os.environ, {"QT_QPA_PLATFORM": platform}), patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(root / "data")]), 0)
