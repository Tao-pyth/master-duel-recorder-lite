import unittest
from datetime import datetime, timedelta, timezone

from master_duel_recorder_lite.detection import DetectionSignal, DuelObservation
from master_duel_recorder_lite.duel_start_monitor import MasterDuelStartMonitor
from master_duel_recorder_lite.frame_capture import FrameCaptureResult, FrameSample
from master_duel_recorder_lite.visual_detection import DetectionCandidate


BASE_TIME = datetime(2026, 8, 9, tzinfo=timezone.utc)


def visible(second: int, *, pid: int = 10, hwnd: int = 100) -> DuelObservation:
    return DuelObservation(
        DetectionSignal.PRESENT,
        0.7,
        "window visible",
        BASE_TIME + timedelta(seconds=second),
        capture_window_handle=hwnd,
        capture_process_id=pid,
        capture_window_title="Master Duel",
    )


def absent(second: int) -> DuelObservation:
    return DuelObservation(
        DetectionSignal.ABSENT,
        0.8,
        "window absent",
        BASE_TIME + timedelta(seconds=second),
    )


def sample(second: int, *, hwnd: int = 100) -> FrameSample:
    return FrameSample(
        captured_at=BASE_TIME + timedelta(seconds=second),
        window_handle=hwnd,
        window_title="Master Duel",
        width=160,
        height=90,
        pixel_format="bmp",
        data=b"BM",
    )


def start_candidate(elapsed_ms: int = 1000) -> DetectionCandidate:
    return DetectionCandidate(
        "duel_start",
        elapsed_ms,
        0.85,
        "3フレーム合意: 対戦開始",
        "test.detector",
        "1",
    )


class SequenceWindowDetector:
    def __init__(self, observations: list[DuelObservation]) -> None:
        self.observations = iter(observations)

    def observe(self) -> DuelObservation:
        return next(self.observations)


class SequencePipeline:
    def __init__(self, outputs: list[tuple[DetectionCandidate, ...]]) -> None:
        self.outputs = outputs

    def analyze(self, _frame: FrameSample, _elapsed_ms: int) -> tuple[DetectionCandidate, ...]:
        return self.outputs.pop(0) if self.outputs else ()


class MasterDuelStartMonitorTest(unittest.TestCase):
    def test_visible_window_does_not_start_until_duel_start_candidate(self) -> None:
        outputs = [(), (start_candidate(),)]
        detector = SequenceWindowDetector([visible(0), visible(1), visible(2)])
        captures = [sample(0), sample(1)]
        monitor = MasterDuelStartMonitor(
            detector,  # type: ignore[arg-type]
            capture=lambda _window: FrameCaptureResult(captures.pop(0), None),
            pipeline_factory=lambda: SequencePipeline(outputs),  # type: ignore[arg-type]
        )

        before_duel = monitor.observe()
        started = monitor.observe()
        latched = monitor.observe()

        self.assertIs(before_duel.signal, DetectionSignal.UNKNOWN)
        self.assertIs(started.signal, DetectionSignal.PRESENT)
        self.assertIs(latched.signal, DetectionSignal.PRESENT)
        self.assertEqual(started.capture_target_key, (10, 100))
        self.assertEqual(monitor.status.processed_frames, 2)
        self.assertEqual(monitor.status.candidate_count, 1)

    def test_capture_failure_is_unknown_and_does_not_start(self) -> None:
        pipeline_count = 0

        def pipeline_factory() -> SequencePipeline:
            nonlocal pipeline_count
            pipeline_count += 1
            return SequencePipeline([])

        monitor = MasterDuelStartMonitor(
            SequenceWindowDetector([visible(0)]),  # type: ignore[arg-type]
            capture=lambda _window: FrameCaptureResult(None, "capture failed"),
            pipeline_factory=pipeline_factory,  # type: ignore[arg-type]
        )

        observation = monitor.observe()

        self.assertIs(observation.signal, DetectionSignal.UNKNOWN)
        self.assertIn("capture failed", observation.reason)
        self.assertEqual(monitor.status.state, "degraded")
        self.assertEqual(monitor.status.dropped_frames, 1)
        self.assertEqual(pipeline_count, 3)

    def test_target_change_resets_latched_start(self) -> None:
        outputs = [(start_candidate(),), ()]
        monitor = MasterDuelStartMonitor(
            SequenceWindowDetector([visible(0), visible(1, hwnd=101)]),  # type: ignore[arg-type]
            capture=lambda window: FrameCaptureResult(sample(window.handle - 100, hwnd=window.handle), None),
            pipeline_factory=lambda: SequencePipeline(outputs),  # type: ignore[arg-type]
        )

        started = monitor.observe()
        changed = monitor.observe()

        self.assertIs(started.signal, DetectionSignal.PRESENT)
        self.assertIs(changed.signal, DetectionSignal.UNKNOWN)
        self.assertIsNone(monitor.start_candidate)

    def test_absence_resets_latched_start(self) -> None:
        outputs = [(start_candidate(),), ()]
        monitor = MasterDuelStartMonitor(
            SequenceWindowDetector([visible(0), absent(1), visible(2)]),  # type: ignore[arg-type]
            capture=lambda _window: FrameCaptureResult(sample(0), None),
            pipeline_factory=lambda: SequencePipeline(outputs),  # type: ignore[arg-type]
        )

        self.assertIs(monitor.observe().signal, DetectionSignal.PRESENT)
        self.assertIs(monitor.observe().signal, DetectionSignal.ABSENT)
        self.assertIs(monitor.observe().signal, DetectionSignal.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
