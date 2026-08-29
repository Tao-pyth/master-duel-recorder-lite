import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from master_duel_recorder_lite.duel_timeline import (
    DuelTimelineError,
    DuelTimelineRepository,
)
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState


class DuelTimelineRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        recordings = root / "recordings"
        recordings.mkdir()
        self.database = root / "history.sqlite3"
        output = recordings / "recording.mkv"
        output.write_bytes(b"video")
        now = datetime.now(timezone.utc)
        history = RecordingHistoryRepository(
            database_path=self.database,
            recordings_root=recordings,
        )
        history.register_starting(
            recording_id="recording",
            output_path=output,
            container="mkv",
            source="manual",
        )
        history.finalize(
            "recording",
            RecordingResult(
                RecordingState.COMPLETED,
                output,
                0,
                now,
                now + timedelta(seconds=100),
                output.stat().st_size,
                None,
                (),
            ),
        )
        self.repository = DuelTimelineRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_confirmed_start_turn_result_have_stable_order(self) -> None:
        start = self.repository.add(
            "recording", elapsed_ms=1000, event_type="duel_start", event_id="start"
        )
        marker_b = self.repository.add(
            "recording", elapsed_ms=2000, event_type="marker", label="B", event_id="b"
        )
        marker_a = self.repository.add(
            "recording", elapsed_ms=2000, event_type="marker", label="A", event_id="a"
        )
        turn = self.repository.add(
            "recording", elapsed_ms=3000, event_type="turn_change", actor="self"
        )
        result = self.repository.add(
            "recording", elapsed_ms=5000, event_type="duel_result", outcome="win"
        )

        events = self.repository.list("recording")

        self.assertEqual(events[0], start)
        self.assertEqual(events[1:3], (marker_a, marker_b))
        self.assertIn(turn, events)
        self.assertEqual(events[-1], result)

    def test_conflicting_candidates_can_coexist_but_only_one_can_confirm(self) -> None:
        first = self.repository.add(
            "recording",
            elapsed_ms=1000,
            event_type="duel_start",
            source="detected",
            status="candidate",
            confidence=0.8,
            detector_id="test",
            detector_version="1",
        )
        second = self.repository.add(
            "recording",
            elapsed_ms=1200,
            event_type="duel_start",
            source="detected",
            status="candidate",
            confidence=0.9,
            detector_id="test",
            detector_version="1",
        )

        confirmed = self.repository.confirm(first.event_id)
        with self.assertRaisesRegex(DuelTimelineError, "1件"):
            self.repository.confirm(second.event_id)

        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(self.repository.get(second.event_id).status, "candidate")  # type: ignore[union-attr]

    def test_turn_requires_confirmed_start_and_result_must_be_last(self) -> None:
        with self.assertRaisesRegex(DuelTimelineError, "対戦開始"):
            self.repository.add(
                "recording", elapsed_ms=2000, event_type="turn_change", actor="self"
            )
        self.repository.add("recording", elapsed_ms=1000, event_type="duel_start")
        self.repository.add(
            "recording", elapsed_ms=2000, event_type="turn_change", actor="opponent"
        )
        with self.assertRaisesRegex(DuelTimelineError, "開始・ターン"):
            self.repository.add(
                "recording", elapsed_ms=1500, event_type="duel_result", outcome="loss"
            )

    def test_candidate_can_be_rejected_without_deletion(self) -> None:
        event = self.repository.add(
            "recording",
            elapsed_ms=1000,
            event_type="marker",
            label="候補",
            status="candidate",
        )

        rejected = self.repository.reject(event.event_id)

        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(self.repository.get(event.event_id), rejected)
        self.assertFalse(hasattr(self.repository, "delete"))

    def test_marker_label_can_be_updated(self) -> None:
        event = self.repository.add(
            "recording",
            elapsed_ms=1000,
            event_type="marker",
            label="修正前",
        )

        updated = self.repository.update_marker_label(event.event_id, "修正後")

        self.assertEqual(updated.label, "修正後")
        self.assertEqual(updated.event_type, "marker")
        self.assertGreater(updated.updated_at, event.updated_at)

    def test_only_marker_label_can_be_updated(self) -> None:
        event = self.repository.add(
            "recording",
            elapsed_ms=1000,
            event_type="duel_start",
        )

        with self.assertRaisesRegex(DuelTimelineError, "marker"):
            self.repository.update_marker_label(event.event_id, "変更")

    def test_detected_marker_label_cannot_be_overwritten(self) -> None:
        event = self.repository.add(
            "recording",
            elapsed_ms=1000,
            event_type="marker",
            label="自動判定の根拠",
            source="detected",
            status="candidate",
            confidence=0.9,
            detector_id="test",
            detector_version="1",
        )

        with self.assertRaisesRegex(DuelTimelineError, "自動判定"):
            self.repository.update_marker_label(event.event_id, "変更")

    def test_event_cannot_exceed_recording_duration(self) -> None:
        with self.assertRaisesRegex(DuelTimelineError, "録画時間"):
            self.repository.add(
                "recording", elapsed_ms=100001, event_type="marker", label="outside"
            )

    def test_detected_event_must_be_candidate(self) -> None:
        with self.assertRaisesRegex(ValueError, "candidate"):
            self.repository.add(
                "recording",
                elapsed_ms=1000,
                event_type="duel_start",
                source="detected",
                status="confirmed",
                confidence=0.9,
                detector_id="test",
                detector_version="1",
            )

    def test_detected_event_requires_trace_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "detector_id"):
            self.repository.add(
                "recording",
                elapsed_ms=1000,
                event_type="duel_start",
                source="detected",
                status="candidate",
                confidence=0.9,
            )

    def test_actor_is_only_valid_for_turn_change(self) -> None:
        with self.assertRaisesRegex(ValueError, "turn_change"):
            self.repository.add(
                "recording",
                elapsed_ms=1000,
                event_type="marker",
                actor="self",
                label="invalid actor",
            )


if __name__ == "__main__":
    unittest.main()
