import unittest
from unittest.mock import patch

from master_duel_recorder_lite.recorder import RecordingVisualLifecycle
from master_duel_recorder_lite.visual_detection import DetectionCandidate


def candidate(
    event_type: str,
    *,
    play_order: str | None = None,
    outcome: str | None = None,
) -> DetectionCandidate:
    return DetectionCandidate(
        event_type=event_type,
        elapsed_ms=1000,
        confidence=0.9,
        reason="test",
        detector_id="test",
        detector_version="1",
        play_order=play_order,
        outcome=outcome,
    )


class RecordingVisualLifecycleTest(unittest.TestCase):
    def test_confirmation_and_result_supply_duel_record_values(self) -> None:
        lifecycle = RecordingVisualLifecycle()

        lifecycle.handle(candidate("duel_confirmed", play_order="second"))
        with patch("master_duel_recorder_lite.recorder.time.monotonic", return_value=123.5):
            lifecycle.handle(candidate("duel_result", outcome="win"))

        self.assertEqual(lifecycle.snapshot(), (True, "second", "win", None, 123.5))

    def test_match_error_cancels_candidate_recording(self) -> None:
        lifecycle = RecordingVisualLifecycle()
        lifecycle.handle(candidate("match_error"))

        self.assertFalse(lifecycle.confirmed)
        self.assertIn("エラー", lifecycle.abort_reason or "")

    def test_replay_is_not_saved_as_live_duel(self) -> None:
        lifecycle = RecordingVisualLifecycle()
        lifecycle.handle(candidate("replay_detected"))

        self.assertFalse(lifecycle.confirmed)
        self.assertIn("リプレイ", lifecycle.abort_reason or "")


if __name__ == "__main__":
    unittest.main()
