"""統計表の数値操作と、日付の描画範囲を実widgetで確認する。"""

from datetime import date, timedelta
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gui_test_helpers import close_gui_test_window, wait_for_gui_startup

from master_duel_recorder_lite.duel_statistics import StatisticsMetric, StatisticsTrendPoint


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class StatisticsPresentationWidgetTest(unittest.TestCase):
    def test_numeric_sort_updates_rate_bars_and_date_labels(self):
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QMainWindow
        from master_duel_recorder_lite import pyside_gui

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def inspect(_app):
                app = QApplication.instance()
                window = next(w for w in app.topLevelWidgets()
                              if isinstance(w, QMainWindow) and w.isVisible())
                try:
                    wait_for_gui_startup(app, window)
                    window.show_page("statistics")
                    table = window._table(
                        "statistics_presentation_test_table", ("名称", "対戦", "勝利", "勝率"),
                        (("十分", 10, 10, "100.0%"), ("少数", 2, 1, "50.0%"),
                         ("小数", 11, 1, "9.1%"), ("0戦", 0, 0, "-"), ("0%", 3, 0, "0.0%")),
                        column_widths=(130, 80, 80, 110),
                    )
                    table.setParent(window)
                    table.setGeometry(230, 300, 460, 250)
                    table.show()
                    app.processEvents()
                    self.assertFalse(table.isSortingEnabled())
                    self.assertEqual([table.item(row, 1).text() for row in range(5)], ["10", "2", "11", "0", "3"])
                    for row in range(5):
                        for column in (1, 2, 3):
                            self.assertTrue(table.item(row, column).textAlignment() & Qt.AlignmentFlag.AlignRight)

                    header = table.horizontalHeader()
                    position = header.viewport().rect().center()
                    position.setX(header.sectionViewportPosition(1) + header.sectionSize(1) // 2)
                    QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, pos=position)
                    self.assertTrue(table.isSortingEnabled())
                    self.assertEqual([table.item(row, 1).text() for row in range(5)], ["0", "2", "3", "10", "11"])
                    table.sortItems(3, Qt.SortOrder.AscendingOrder)
                    self.assertEqual([table.item(row, 3).text() for row in range(5)], ["-", "0.0%", "9.1%", "50.0%", "100.0%"])
                    table.sortItems(3, Qt.SortOrder.DescendingOrder)
                    self.assertEqual([table.item(row, 3).text() for row in range(5)], ["100.0%", "50.0%", "9.1%", "0.0%", "-"])
                    app.processEvents()
                    delegate = table.itemDelegateForColumn(3)
                    self.assertEqual(delegate.bar_fraction(table.model().index(0, 3)), 1)
                    self.assertAlmostEqual(delegate.bar_fraction(table.model().index(2, 3)), 0.091)
                    self.assertEqual(delegate.bar_fraction(table.model().index(3, 3)), 0)
                    self.assertIsNone(delegate.bar_fraction(table.model().index(4, 3)))
                    painted = table.viewport().grab().toImage()
                    bar_rect = table.visualItemRect(table.item(0, 3))
                    self.assertEqual(painted.pixelColor(bar_rect.left() + 9, bar_rect.bottom() - 3).name(), "#4f8f82")

                    window._set_table_rows(table, (("A", 2, 1, "50.0%"), ("B", 10, 9, "90.0%"), ("Z", 0, 0, "-")))
                    self.assertTrue(table.isSortingEnabled())
                    self.assertEqual(header.sortIndicatorSection(), 3)
                    self.assertEqual(header.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder)
                    self.assertEqual(
                        [tuple(table.item(row, column).text() for column in range(4)) for row in range(3)],
                        [("B", "10", "9", "90.0%"), ("A", "2", "1", "50.0%"), ("Z", "0", "0", "-")],
                    )
                    window._set_table_rows(table, ())
                    self.assertEqual(table.rowCount(), 0)
                    window._set_table_rows(table, (("新しい行", 1, 1, "100.0%"),))
                    self.assertEqual(table.item(0, 0).text(), "新しい行")
                    window._set_table_rows(table, (("千件", "1,000", 1000, "100.0%"), ("十件", 10, 10, "100.0%")))
                    table.sortItems(1, Qt.SortOrder.AscendingOrder)
                    self.assertEqual([table.item(row, 1).text() for row in range(2)], ["10", "1,000"])
                    table.sortItems(1, Qt.SortOrder.DescendingOrder)
                    self.assertEqual([table.item(row, 1).text() for row in range(2)], ["1,000", "10"])
                    table.hide()

                    panel = window._table_panel(
                        "statistics_placeholder_test_table", ("名称", "対戦", "勝利", "勝率", "参考")
                    )
                    panel.setParent(window)
                    placeholder = window.widgets["statistics_placeholder_test_table"]
                    self.assertEqual(placeholder.item(0, 4).text(), "")
                    self.assertEqual(placeholder.columnWidth(4), 64)
                    self.assertEqual(placeholder.columnWidth(3), 112)
                    self.assertFalse(placeholder.horizontalHeader().stretchLastSection())
                    self.assertIn("10戦未満", placeholder.horizontalHeaderItem(4).toolTip())

                    ordinary = window._table("presentation_non_statistics_table", ("名称", "対戦"), (("通常", 10),))
                    ordinary.setParent(window)
                    self.assertIsNone(ordinary.property("statistics_numeric_columns"))
                    self.assertFalse(ordinary.isSortingEnabled())

                    chart = window.widgets["statistics_chart"]
                    for kind, count in (("day", 90), ("week", 13), ("month", 12), ("month", 1)):
                        dates = [date(2026, 7, 1) + timedelta(days=index * (7 if kind == "week" else 1))
                                 if kind != "month" else date(2025 + index // 12, index % 12 + 1, 1)
                                 for index in range(count)]
                        points = tuple(StatisticsTrendPoint(
                            current, current.strftime("%Y/%m" if kind == "month" else "%m/%d") + ("週" if kind == "week" else ""),
                            StatisticsMetric(5, 3, 1, 1),
                        ) for current in dates)
                        chart.set_points(points)
                        for width in (360, 560, 900):
                            chart.setFixedSize(width, 300)
                            app.processEvents()
                            labels = chart.date_label_rectangles()
                            self.assertEqual((labels[0][0], labels[-1][0]), (0, count - 1))
                            frame = QRectF(chart.rect().adjusted(14, 12, -14, -14))
                            for index, text, rectangle in labels:
                                self.assertEqual(text, points[index].label)
                                self.assertGreaterEqual(rectangle.width(), chart.fontMetrics().horizontalAdvance(text))
                                self.assertTrue(frame.contains(rectangle), (kind, count, width, text))
                            for position, label in enumerate(labels):
                                for other in labels[position + 1:]:
                                    self.assertFalse(label[2].intersects(other[2]), (kind, width, label[1], other[1]))
                            self.assertIs(chart.points, points)
                            if count == 90:
                                self.assertLess(len(labels), count)
                            capture = os.environ.get("MDRL_STATISTICS_PRESENTATION_CAPTURE_DIR")
                            if capture and width == 560:
                                directory = Path(capture)
                                directory.mkdir(parents=True, exist_ok=True)
                                self.assertTrue(chart.grab().save(str(directory / f"chart-{kind}-{count}.png")))
                    chart.set_points(())
                    self.assertEqual(chart.date_label_rectangles(), ())
                finally:
                    close_gui_test_window(app, window)
                return 0

            platform = os.environ.get("MDRL_TEST_QPA", "offscreen")
            with patch.dict(os.environ, {"QT_QPA_PLATFORM": platform}), patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(root / "data")]), 0)
