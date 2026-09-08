"""実widgetの領域を検証し、smoke契約だけでは分からない重なりを防ぐ。"""

import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class RecordPageLayoutTest(unittest.TestCase):
    def test_controls_and_status_remain_readable_at_supported_sizes(self) -> None:
        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtWidgets import QApplication, QScrollArea, QTableWidget, QWidget

        from master_duel_recorder_lite import pyside_gui
        from master_duel_recorder_lite.operation_state import OperationAction, OperationState

        captured = []
        original_grab = QWidget.grab

        def inspect(window, *args, **kwargs):
            window.record_state_timer.stop()
            sizes = ((980, 640), (1180, 760), (1180, 790), (1180, 1000))
            states = (
                (OperationState.IDLE, False, False, (True, False, True)),
                (OperationState.WATCH_STARTING, True, False, (False, True, True)),
                (OperationState.WATCH_WAITING, True, False, (False, True, True)),
                (OperationState.AUTOMATIC_RECORDING, True, True, (False, True, True)),
                (OperationState.MANUAL_RECORDING, False, True, (False, True, False)),
                (OperationState.STOPPING, False, False, (False, False, False)),
            )
            widgets = window.widgets
            deck = widgets["watch_default_own_deck"]
            deck.setEditText("テストデッキ")
            widgets["watch_default_desired_play_order"].setCurrentIndex(1)
            original_service = window.service
            fake = SimpleNamespace(
                start_recording=Mock(), stop_recording=Mock(),
                start_watch=Mock(), stop_watch=Mock(), save_settings=Mock(),
                visual_detection_status=lambda: SimpleNamespace(message="自動判定を実行中です"),
            )
            window.service = fake
            for (width, height), (state, watching, recording, enabled) in (
                (size, state) for size in sizes for state in states
            ):
                allowed = set()
                if enabled[0]:
                    allowed.add(OperationAction.START_MANUAL)
                if watching:
                    allowed.add(OperationAction.STOP_WATCH)
                elif recording:
                    allowed.add(OperationAction.STOP_RECORDING)
                elif enabled[2]:
                    allowed.add(OperationAction.START_WATCH)
                fake.watch_active = watching
                fake.operation_snapshot = lambda: SimpleNamespace(
                    state=state, message="検証中", allowed_actions=allowed,
                )
                fake.recording_snapshot = lambda: SimpleNamespace(
                    active=recording, state=SimpleNamespace(value="recording" if recording else "completed"),
                    recording_id="test-recording" if recording else None,
                    output_path=Path("recordings/test.mp4") if recording else None,
                    elapsed_seconds=128 if recording else 0,
                )
                window._refresh_recording_state()
                window.resize(width, height)
                QApplication.processEvents()
                watch = widgets["watch_toggle"]
                details = widgets["visual_details_toggle"]
                band = widgets["record_status_band"]
                table = widgets["record_environment_diagnostics"].findChild(QTableWidget)
                page = window.pages["record"]
                def rectangle(widget):
                    return QRect(widget.mapTo(window, QPoint()), widget.size())

                checks = {
                    "controls_separated": not rectangle(watch).intersects(rectangle(details)),
                    "band_full_height": band.height() >= band.sizeHint().height(),
                    "diagnostic_row_visible": table.viewport().height() >= table.rowHeight(0),
                    "controls_inside_parent": all(
                        w.parentWidget().rect().contains(w.geometry())
                        for w in (watch, details, widgets["watch_default_desired_play_order"])
                    ),
                    "enabled_states": tuple(
                        widgets[key].isEnabled() for key in ("record_start", "record_stop", "watch_toggle")
                    ) == enabled,
                }
                if isinstance(page, QScrollArea):
                    page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
                    page.ensureWidgetVisible(watch)
                    QApplication.processEvents()
                    checks["watch_reachable"] = page.viewport().rect().contains(
                        QRect(watch.mapTo(page.viewport(), QPoint()), watch.size())
                    )
                    start = widgets["record_start"]
                    start.setFocus()
                    page.ensureWidgetVisible(start)
                    QApplication.processEvents()
                    checks["start_reachable"] = page.viewport().rect().contains(
                        QRect(start.mapTo(page.viewport(), QPoint()), start.size())
                    )
                else:
                    checks["watch_reachable"] = window.rect().contains(rectangle(watch))
                window.show_page("history")
                window.show_page("record")
                checks["defaults_retained"] = (
                    deck.currentText() == "テストデッキ"
                    and widgets["watch_default_desired_play_order"].currentData() == "first"
                )
                with patch.object(window, "_run_action", side_effect=lambda label, action: action()):
                    for key, expected_enabled in zip(
                        ("record_start", "record_stop", "watch_toggle"), enabled,
                    ):
                        if expected_enabled:
                            widgets[key].click()
                captured.append(((width, height, state.value), checks))
            # クリックが実サービスではなく各対応操作へ届くことも確認する。
            captured.append(("callbacks", {
                "manual_start": fake.start_recording.call_count == len(sizes),
                "manual_stop": fake.stop_recording.call_count == len(sizes),
                "watch_start": fake.start_watch.call_count == len(sizes),
                "watch_stop": fake.stop_watch.call_count == len(sizes) * 6,
                "defaults_forwarded": fake.start_watch.call_args.kwargs["duel_defaults"].own_deck == "テストデッキ",
            }))
            window.service = original_service
            return original_grab(window, *args, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
                with patch.object(QWidget, "grab", inspect):
                    result = pyside_gui.main([
                        "--project-root", str(root),
                        "--user-data-dir", str(root / "isolated-data"),
                        "--smoke-test", "--smoke-page", "record",
                        "--smoke-screenshot", str(root / "record.png"),
                    ])
            self.assertEqual(result, 0)
            self.assertFalse((root / "isolated-data").exists())
        self.assertEqual(len(captured), 25)
        for size, checks in captured:
            with self.subTest(size=size):
                self.assertTrue(all(checks.values()), checks)


if __name__ == "__main__":
    unittest.main()
