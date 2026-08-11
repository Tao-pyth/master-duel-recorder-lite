import queue
import unittest
from unittest.mock import Mock

from master_duel_recorder_lite.gui import (
    HISTORY_ROW_ACTIONS,
    ICON_GLYPHS,
    RecorderGui,
    WAITING_ACTIVITY_PREFIX,
)


class FakeListbox:
    def __init__(self) -> None:
        self.items: list[str] = []

    def insert(self, index: int, message: str) -> None:
        self.items.insert(index, message)

    def size(self) -> int:
        return len(self.items)

    def get(self, index: int) -> str:
        return self.items[index]

    def delete(self, first: int, last: object | None = None) -> None:
        if last == "end":
            del self.items[first:]
        else:
            del self.items[first]


class GuiActivityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gui = RecorderGui.__new__(RecorderGui)
        self.gui.activity_list = FakeListbox()  # type: ignore[assignment]

    def test_waiting_activity_replaces_existing_line(self) -> None:
        self.gui._activity("自動監視を開始しました")
        self.gui._activity(
            "対戦開始を判定中です (0s)",
            replace_prefix=WAITING_ACTIVITY_PREFIX,
        )
        self.gui._activity(
            "対戦開始を判定中です (4s)",
            replace_prefix=WAITING_ACTIVITY_PREFIX,
        )

        self.assertEqual(
            self.gui.activity_list.items,
            ["対戦開始を判定中です (4s)", "自動監視を開始しました"],
        )

    def test_history_row_has_four_distinct_icon_actions_and_keyboard_paths(self) -> None:
        self.assertEqual(
            [item[1] for item in HISTORY_ROW_ACTIONS],
            ["再生", "対戦記録を編集", "保存場所を開く", "削除"],
        )
        self.assertEqual(len({ICON_GLYPHS[item[0]] for item in HISTORY_ROW_ACTIONS}), 4)
        self.assertEqual([item[2] for item in HISTORY_ROW_ACTIONS], ["Enter", "Ctrl+E", "Ctrl+O", "Delete"])

    def test_removing_waiting_activity_preserves_other_history(self) -> None:
        self.gui._activity("録画対象を保存しました")
        self.gui._activity("対戦開始を判定中です (4s)")
        self.gui._activity("対戦開始を判定中です (3s)")

        self.gui._remove_activity(WAITING_ACTIVITY_PREFIX)

        self.assertEqual(self.gui.activity_list.items, ["録画対象を保存しました"])

    def test_runtime_poll_does_not_call_service_while_operation_is_busy(self) -> None:
        gui = RecorderGui.__new__(RecorderGui)
        gui.watch_events = queue.Queue()
        gui.busy_operations = 1
        gui.closing = False
        gui.service = Mock()
        gui.root = Mock()

        gui._poll_runtime()

        gui.service.recording_snapshot.assert_not_called()
        gui.service.visual_detection_status.assert_not_called()
        gui.root.after.assert_called_once_with(500, gui._poll_runtime)


if __name__ == "__main__":
    unittest.main()
