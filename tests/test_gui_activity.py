import unittest

from master_duel_recorder_lite.gui import RecorderGui, WAITING_ACTIVITY_PREFIX


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

    def test_removing_waiting_activity_preserves_other_history(self) -> None:
        self.gui._activity("録画対象を保存しました")
        self.gui._activity("対戦開始を判定中です (4s)")
        self.gui._activity("対戦開始を判定中です (3s)")

        self.gui._remove_activity(WAITING_ACTIVITY_PREFIX)

        self.assertEqual(self.gui.activity_list.items, ["録画対象を保存しました"])


if __name__ == "__main__":
    unittest.main()
