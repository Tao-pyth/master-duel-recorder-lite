import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from master_duel_recorder_lite.frame_capture import FrameCaptureResult, FrameSample
from master_duel_recorder_lite.visual_detection import DetectionCandidate
from master_duel_recorder_lite.visual_worker import VisualDetectionStatus, VisualDetectionWorker


def sample(started_at: datetime, offset_seconds: float = 1.0) -> FrameSample:
    return FrameSample(
        captured_at=started_at + timedelta(seconds=offset_seconds),
        window_handle=1,
        window_title="Master Duel",
        width=160,
        height=90,
        pixel_format="bmp",
        data=b"frame",
    )


class VisualDetectionWorkerTest(unittest.TestCase):
    def test_worker_processes_one_frame_at_a_time_and_stops(self) -> None:
        started_at = datetime.now(timezone.utc)
        active = 0
        maximum_active = 0
        lock = threading.Lock()
        candidates: list[DetectionCandidate] = []

        def analyze(_sample: FrameSample, elapsed_ms: int) -> tuple[DetectionCandidate, ...]:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.08)
            with lock:
                active -= 1
            return (
                DetectionCandidate(
                    "duel_start", elapsed_ms, 0.8, "test", "test", "1"
                ),
            )

        worker = VisualDetectionWorker(
            recording_started_at=started_at,
            capture=lambda: FrameCaptureResult(sample(started_at), None),
            analyze=analyze,
            on_candidate=candidates.append,
            maximum_fps=2,
        )
        worker.start()
        time.sleep(0.65)
        worker.stop()

        self.assertEqual(maximum_active, 1)
        self.assertGreaterEqual(worker.status.processed_frames, 1)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertFalse(worker.active)
        self.assertEqual(worker.status.state, "stopped")

    def test_analysis_exception_disables_only_worker(self) -> None:
        started_at = datetime.now(timezone.utc)
        statuses: list[VisualDetectionStatus] = []

        def fail(_sample: FrameSample, _elapsed_ms: int) -> tuple[DetectionCandidate, ...]:
            raise RuntimeError("analysis failed")

        worker = VisualDetectionWorker(
            recording_started_at=started_at,
            capture=lambda: FrameCaptureResult(sample(started_at), None),
            analyze=fail,
            on_candidate=lambda _candidate: None,
            on_status=statuses.append,
        )
        worker.start()
        time.sleep(0.1)
        worker.stop()

        self.assertEqual(worker.status.state, "failed")
        self.assertIn("analysis failed", worker.status.message)
        self.assertFalse(worker.active)
        self.assertTrue(any(status.state == "failed" for status in statuses))

    def test_slow_analysis_counts_frames_skipped_by_latest_frame_policy(self) -> None:
        started_at = datetime.now(timezone.utc)

        def analyze(
            _sample: FrameSample, _elapsed_ms: int
        ) -> tuple[DetectionCandidate, ...]:
            time.sleep(0.55)
            return ()

        worker = VisualDetectionWorker(
            recording_started_at=started_at,
            capture=lambda: FrameCaptureResult(sample(started_at), None),
            analyze=analyze,
            on_candidate=lambda _candidate: None,
            maximum_fps=2,
        )
        worker.start()
        time.sleep(0.65)
        worker.stop()

        self.assertGreaterEqual(worker.status.processed_frames, 1)
        self.assertGreaterEqual(worker.status.dropped_frames, 1)

    def test_stop_request_discards_analysis_that_finishes_after_recording_stop(self) -> None:
        started_at = datetime.now(timezone.utc)
        entered = threading.Event()
        release = threading.Event()
        candidates: list[DetectionCandidate] = []

        def analyze(
            _sample: FrameSample, elapsed_ms: int
        ) -> tuple[DetectionCandidate, ...]:
            entered.set()
            release.wait(1)
            return (
                DetectionCandidate(
                    "duel_start", elapsed_ms, 0.8, "test", "test", "1"
                ),
            )

        worker = VisualDetectionWorker(
            recording_started_at=started_at,
            capture=lambda: FrameCaptureResult(sample(started_at), None),
            analyze=analyze,
            on_candidate=candidates.append,
        )
        worker.start()
        self.assertTrue(entered.wait(1))
        worker.request_stop()
        release.set()
        worker.stop()

        self.assertEqual(candidates, [])
        self.assertEqual(worker.status.state, "stopped")

    def test_capture_error_is_degraded_and_worker_can_stop(self) -> None:
        started_at = datetime.now(timezone.utc)
        worker = VisualDetectionWorker(
            recording_started_at=started_at,
            capture=lambda: FrameCaptureResult(None, "capture unavailable"),
            analyze=lambda _sample, _elapsed: (),
            on_candidate=lambda _candidate: None,
        )
        worker.start()
        time.sleep(0.1)
        self.assertEqual(worker.status.state, "degraded")
        worker.stop()
        self.assertEqual(worker.status.state, "stopped")

    def test_rejects_more_than_two_frames_per_second(self) -> None:
        with self.assertRaisesRegex(ValueError, "2以下"):
            VisualDetectionWorker(
                recording_started_at=datetime.now(timezone.utc),
                capture=lambda: FrameCaptureResult(None, "unused"),
                analyze=lambda _sample, _elapsed: (),
                on_candidate=lambda _candidate: None,
                maximum_fps=2.1,
            )


if __name__ == "__main__":
    unittest.main()
