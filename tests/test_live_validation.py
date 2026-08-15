import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from master_duel_recorder_lite.live_validation import (
    evaluate_live_diagnostics,
    render_live_validation_markdown,
)


START = datetime(2026, 8, 15, tzinfo=timezone.utc)


class LiveValidationTest(unittest.TestCase):
    def test_user_stopped_watch_is_reported_as_interrupted_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_session(
                root,
                "interrupted",
                [
                    transition("candidate_started", 0),
                    transition("duel_confirmed", 5),
                    transition("watch_stopped_with_active_recording", 30),
                ],
                sample_at=30,
            )

            report = evaluate_live_diagnostics(root, since=START)
            markdown = render_live_validation_markdown(report, 3)

        self.assertEqual(report.interrupted_attempts, 1)
        self.assertEqual(report.attempts, ())
        self.assertEqual(report.failed_attempts, 0)
        self.assertIn("利用者による録画中断: 1", markdown)

    def test_three_complete_duels_pass_initial_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transitions = []
            for index in range(3):
                base = index * 60
                transitions.extend(
                    (
                        transition("candidate_started", base),
                        transition("duel_confirmed", base + 5),
                        transition("result_stopped", base + 50),
                    )
                )
            write_session(root, "initial", transitions, sample_at=180)

            report = evaluate_live_diagnostics(root, since=START)

        self.assertEqual(report.passed_attempts, 3)
        self.assertEqual(report.latest_consecutive_passes, 3)
        self.assertEqual(report.minimum_effective_fps, 1.8)
        self.assertEqual(report.average_effective_fps, 1.8)
        self.assertTrue(report.gate_passed(3))
        self.assertFalse(report.gate_passed(10))

    def test_latest_failure_resets_consecutive_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transitions = []
            for index in range(4):
                base = index * 60
                transitions.extend(
                    (
                        transition("candidate_started", base),
                        transition("duel_confirmed", base + 5),
                        transition("result_stopped", base + 50),
                    )
                )
            transitions.extend(
                (transition("candidate_started", 240), transition("duel_confirmed", 245))
            )
            write_session(root, "failure", transitions, sample_at=250)

            report = evaluate_live_diagnostics(root, since=START)

        self.assertEqual(report.maximum_consecutive_passes, 4)
        self.assertEqual(report.latest_consecutive_passes, 0)
        self.assertEqual(report.failed_attempts, 1)
        self.assertFalse(report.gate_passed(3))

    def test_consecutive_successes_span_multiple_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, offset in (("one", 0), ("two", 60)):
                write_session(
                    root,
                    name,
                    [
                        transition("candidate_started", offset),
                        transition("duel_confirmed", offset + 5),
                        transition("result_stopped", offset + 50),
                    ],
                    sample_at=offset + 55,
                    started_at=START + timedelta(seconds=offset),
                )

            report = evaluate_live_diagnostics(root, since=START)

        self.assertEqual(report.sessions, 2)
        self.assertEqual(report.latest_consecutive_passes, 2)
        self.assertTrue(report.gate_passed(2))

    def test_discard_is_excluded_and_boundary_stop_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            transitions = [
                transition("candidate_started", 0),
                transition("candidate_discarded", 10),
                transition("candidate_started", 20),
                transition("duel_confirmed", 25),
                transition("stream_restarted", 30),
                transition("boundary_stopped", 50),
            ]
            write_session(
                root,
                "abnormal",
                transitions,
                sample_at=60,
                scores={"error": 0.75, "replay": 0.81, "overlay": 0.72},
            )

            report = evaluate_live_diagnostics(root, since=START)

        self.assertEqual(report.discarded_candidates, 1)
        self.assertEqual(len(report.attempts), 1)
        self.assertEqual(report.attempts[0].stream_restarts, 1)
        self.assertIn("結果以外で停止", report.attempts[0].failure_reasons)
        self.assertTrue(report.observed_match_error)
        self.assertTrue(report.observed_replay)
        self.assertTrue(report.observed_overlay)

    def test_confirmed_candidate_discard_is_a_failed_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_session(
                root,
                "confirmed-discard",
                [
                    transition("candidate_started", 0),
                    transition("duel_confirmed", 5),
                    transition("candidate_discarded", 10),
                ],
                sample_at=20,
            )

            report = evaluate_live_diagnostics(root, since=START)

        self.assertEqual(report.discarded_candidates, 0)
        self.assertEqual(report.failed_attempts, 1)
        self.assertIn("結果以外で停止", report.attempts[0].failure_reasons)

    def test_result_without_recovery_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_session(
                root,
                "no-recovery",
                [
                    transition("candidate_started", 0),
                    transition("duel_confirmed", 5),
                    transition("result_stopped", 50),
                ],
                sample_at=52,
            )

            report = evaluate_live_diagnostics(root, since=START)

        self.assertFalse(report.attempts[0].passed)
        self.assertIn("停止後の監視復帰未確認", report.attempts[0].failure_reasons)

    def test_board_activity_after_result_stop_fails_early_stop_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_session(
                root,
                "early-stop",
                [
                    transition("candidate_started", 0),
                    transition("duel_confirmed", 5),
                    transition("result_stopped", 50),
                ],
                sample_at=70,
                samples=[
                    sample(54, board=0.42),
                    sample(56, board=0.39),
                    sample(58, board=0.46),
                    sample(70),
                ],
            )

            report = evaluate_live_diagnostics(root, since=START)

        self.assertFalse(report.attempts[0].passed)
        self.assertTrue(report.attempts[0].post_stop_duel_activity)
        self.assertIn("結果停止後も盤面継続", report.attempts[0].failure_reasons)
        self.assertFalse(report.gate_passed(1))

    def test_malformed_transition_blocks_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_session(
                root,
                "malformed",
                [transition("duel_confirmed", 0)],
                sample_at=5,
            )

            report = evaluate_live_diagnostics(root, since=START)

        self.assertEqual(len(report.malformed_events), 1)
        self.assertFalse(report.gate_passed(1))

    def test_since_filters_old_sessions_and_report_has_no_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_session(root, "old", [], sample_at=1, started_at=START)
            write_session(
                root,
                "new",
                [
                    transition("candidate_started", 60),
                    transition("duel_confirmed", 65),
                    transition("result_stopped", 70),
                ],
                sample_at=80,
                started_at=START + timedelta(seconds=60),
            )

            report = evaluate_live_diagnostics(
                root,
                since=START + timedelta(seconds=30),
            )
            document = report.to_document(1)
            markdown = render_live_validation_markdown(report, 1)

        serialized = json.dumps(document, ensure_ascii=False)
        self.assertEqual(report.sessions, 1)
        self.assertEqual(document["since"], "2026-08-15T00:00:30+00:00")
        self.assertTrue(document["gate_passed"])
        self.assertNotIn(str(root), serialized)
        self.assertIn("判定: **合格**", markdown)


def transition(event: str, seconds: int) -> dict[str, object]:
    return {
        "at": (START + timedelta(seconds=seconds)).isoformat(),
        "event": event,
        "elapsed_ms": None,
    }


def write_session(
    root: Path,
    name: str,
    transitions: list[dict[str, object]],
    *,
    sample_at: int,
    scores: dict[str, float] | None = None,
    samples: list[dict[str, object]] | None = None,
    effective_fps: float = 1.8,
    started_at: datetime = START,
) -> None:
    document = {
        "schema_version": 1,
        "started_at": started_at.isoformat(),
        "ended_at": (START + timedelta(seconds=sample_at + 1)).isoformat(),
        "samples": samples
        or [
            sample(
                sample_at,
                scores=scores,
                effective_fps=effective_fps,
            )
        ],
        "transitions": transitions,
    }
    (root / f"{name}.json").write_text(
        json.dumps(document, ensure_ascii=False),
        encoding="utf-8",
    )


def sample(
    seconds: int,
    *,
    board: float = 0.0,
    scores: dict[str, float] | None = None,
    effective_fps: float = 1.8,
) -> dict[str, object]:
    values = dict(scores or {})
    if board:
        values["board"] = board
    return {
        "at": (START + timedelta(seconds=seconds)).isoformat(),
        "state": "matchmaking",
        "scores": values,
        "effective_fps": effective_fps,
    }


if __name__ == "__main__":
    unittest.main()
