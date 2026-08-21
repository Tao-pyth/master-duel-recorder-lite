import unittest

from master_duel_recorder_lite.hotkey_actions import (
    HotkeyCommand,
    HotkeyDispatcher,
    OperationState,
    default_hotkey_bindings,
)


class HotkeyActionsTest(unittest.TestCase):
    def test_dispatch_rejects_disallowed_state(self) -> None:
        dispatcher = HotkeyDispatcher(
            {HotkeyCommand.TOGGLE_RECORDING: lambda: "recording"}
        )

        result = dispatcher.dispatch(
            HotkeyCommand.TOGGLE_RECORDING, state=OperationState.BUSY
        )

        self.assertFalse(result.accepted)

    def test_dispatch_calls_handler_when_allowed(self) -> None:
        dispatcher = HotkeyDispatcher({HotkeyCommand.SHOW_STATUS: lambda: "正常"})

        result = dispatcher.dispatch(HotkeyCommand.SHOW_STATUS, state=OperationState.BUSY)

        self.assertTrue(result.accepted)
        self.assertEqual(result.message, "正常")

    def test_duplicate_bindings_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            default_hotkey_bindings(
                record_toggle="Ctrl+Alt+R",
                marker="Ctrl+Alt+R",
                watch_toggle="Ctrl+Alt+W",
            )


if __name__ == "__main__":
    unittest.main()
