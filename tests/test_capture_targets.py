import unittest

from master_duel_recorder_lite.capture_targets import (
    CaptureMode,
    CaptureTarget,
    CaptureTargetCatalog,
    CaptureTargetError,
    MonitorSnapshot,
    capture_input_for_target,
    find_target,
)
from master_duel_recorder_lite.game_window import WindowSnapshot


class FakeBackend:
    def __init__(self, windows: tuple[WindowSnapshot, ...] | None = None) -> None:
        self.windows = windows

    def list_monitors(self) -> tuple[MonitorSnapshot, ...]:
        return (
            MonitorSnapshot("DISPLAY1", "Main", 0, 0, 1920, 1080, True),
            MonitorSnapshot("DISPLAY2", "Side", -1280, 0, 1280, 1024),
        )

    def list_windows(self) -> tuple[WindowSnapshot, ...]:
        return self.windows or (
            WindowSnapshot(42, 100, "MASTER DUEL", True, False, 1280, 720),
            WindowSnapshot(43, 101, "Hidden", False, False, 800, 600),
            WindowSnapshot(44, 102, "Minimized", True, True, 800, 600),
        )


class CaptureTargetTest(unittest.TestCase):
    def test_catalog_lists_desktop_monitors_and_visible_windows(self) -> None:
        targets = CaptureTargetCatalog(FakeBackend()).list_targets()

        self.assertEqual(
            [target.mode for target in targets],
            [CaptureMode.DESKTOP, CaptureMode.MONITOR, CaptureMode.MONITOR, CaptureMode.WINDOW],
        )
        self.assertEqual(targets[-1].identifier, "window:42")

    def test_window_target_uses_hwnd_input(self) -> None:
        target = CaptureTarget(
            CaptureMode.WINDOW,
            "window:42",
            "MASTER DUEL",
            window_handle=42,
            width=1280,
            height=720,
        )

        capture_input = capture_input_for_target(target)

        self.assertEqual(capture_input.input_name, "hwnd=42")
        self.assertEqual(capture_input.options, ())

    def test_monitor_target_uses_desktop_region(self) -> None:
        target = CaptureTarget(
            CaptureMode.MONITOR,
            "monitor:DISPLAY2",
            "Side",
            left=-1280,
            top=0,
            width=1280,
            height=1024,
        )

        capture_input = capture_input_for_target(target)

        self.assertEqual(capture_input.input_name, "desktop")
        self.assertEqual(
            capture_input.options,
            ("-offset_x", "-1280", "-offset_y", "0", "-video_size", "1280x1024"),
        )

    def test_unavailable_target_is_rejected(self) -> None:
        target = CaptureTarget(
            CaptureMode.MASTER_DUEL,
            "master_duel",
            "Master Duel",
            available=False,
        )

        with self.assertRaises(CaptureTargetError):
            capture_input_for_target(target)

    def test_find_target_reports_stale_identifier(self) -> None:
        with self.assertRaises(CaptureTargetError):
            find_target((), CaptureMode.WINDOW, "window:999")

    def test_catalog_omits_shell_and_tiny_windows(self) -> None:
        backend = FakeBackend(
            (
                WindowSnapshot(1, 10, "Program Manager", True, False, 1920, 1080),
                WindowSnapshot(2, 10, "", True, False, 1920, 48),
                WindowSnapshot(3, 10, "Master Duel", True, False, 1280, 720),
            )
        )

        targets = CaptureTargetCatalog(backend).list_targets()

        window_targets = [target for target in targets if target.mode is CaptureMode.WINDOW]
        self.assertEqual(len(window_targets), 1)
        self.assertIn("PID 10, HWND 3", window_targets[0].label)


if __name__ == "__main__":
    unittest.main()
