import unittest
from datetime import datetime, timezone

from master_duel_recorder_lite.detection import DetectionSignal
from master_duel_recorder_lite.game_window import (
    GameWindowMonitor,
    GameWindowStatus,
    ProcessSnapshot,
    WindowSnapshot,
)
from master_duel_recorder_lite.master_duel_detector import MasterDuelWindowDetector


class FakeBackend:
    def __init__(self, processes: object = (), windows: object = (), error: Exception | None = None) -> None:
        self.processes = processes
        self.windows = windows
        self.error = error

    def list_processes(self) -> object:
        if self.error:
            raise self.error
        return self.processes

    def list_windows(self) -> object:
        if self.error:
            raise self.error
        return self.windows


class GameWindowMonitorTest(unittest.TestCase):
    def test_not_running_is_distinct(self) -> None:
        monitor = GameWindowMonitor(backend=FakeBackend())  # type: ignore[arg-type]

        result = monitor.observe()

        self.assertIs(result.status, GameWindowStatus.NOT_RUNNING)

    def test_largest_visible_owned_window_is_selected(self) -> None:
        processes = (ProcessSnapshot(42, "MASTERDUEL.EXE"),)
        windows = (
            WindowSnapshot(1, 42, "Small", True, False, 640, 480),
            WindowSnapshot(2, 42, "Master Duel", True, False, 1920, 1080),
            WindowSnapshot(3, 99, "Other", True, False, 3840, 2160),
        )
        monitor = GameWindowMonitor(backend=FakeBackend(processes, windows))  # type: ignore[arg-type]

        result = monitor.observe()

        self.assertIs(result.status, GameWindowStatus.VISIBLE)
        assert result.window is not None
        self.assertEqual(result.window.handle, 2)
        self.assertEqual(result.candidate_count, 2)

    def test_minimized_window_is_distinct(self) -> None:
        processes = (ProcessSnapshot(42, "masterduel.exe"),)
        windows = (WindowSnapshot(2, 42, "Master Duel", True, True, 1920, 1080),)
        monitor = GameWindowMonitor(backend=FakeBackend(processes, windows))  # type: ignore[arg-type]

        result = monitor.observe()

        self.assertIs(result.status, GameWindowStatus.MINIMIZED)

    def test_title_filter_can_reject_owned_window(self) -> None:
        processes = (ProcessSnapshot(42, "masterduel.exe"),)
        windows = (WindowSnapshot(2, 42, "Launcher", True, False, 1920, 1080),)
        monitor = GameWindowMonitor(
            title_contains="Master Duel",
            backend=FakeBackend(processes, windows),  # type: ignore[arg-type]
        )

        result = monitor.observe()

        self.assertIs(result.status, GameWindowStatus.RUNNING_NO_WINDOW)

    def test_backend_failure_is_unknown_observation(self) -> None:
        monitor = GameWindowMonitor(backend=FakeBackend(error=OSError("access denied")))  # type: ignore[arg-type]
        detector = MasterDuelWindowDetector(
            monitor,
            clock=lambda: datetime(2026, 8, 8, tzinfo=timezone.utc),
        )

        result = detector.observe()

        self.assertIs(result.signal, DetectionSignal.UNKNOWN)
        self.assertIn("access denied", result.reason)

    def test_detector_maps_visible_and_minimized(self) -> None:
        process = ProcessSnapshot(42, "masterduel.exe")
        visible = GameWindowMonitor(
            backend=FakeBackend((process,), (WindowSnapshot(2, 42, "Master Duel", True, False, 1920, 1080),))  # type: ignore[arg-type]
        )
        minimized = GameWindowMonitor(
            backend=FakeBackend((process,), (WindowSnapshot(2, 42, "Master Duel", True, True, 1920, 1080),))  # type: ignore[arg-type]
        )

        visible_observation = MasterDuelWindowDetector(visible).observe()
        minimized_signal = MasterDuelWindowDetector(minimized).observe().signal

        self.assertIs(visible_observation.signal, DetectionSignal.PRESENT)
        self.assertEqual(visible_observation.capture_window_handle, 2)
        self.assertIs(minimized_signal, DetectionSignal.ABSENT)


if __name__ == "__main__":
    unittest.main()
