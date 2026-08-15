import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from master_duel_recorder_lite.visual_detection import (
    DETECTOR_ID,
    DETECTOR_VERSION,
    DetectionCandidate,
    FrameAnalysis,
    MasterDuelState,
)
from master_duel_recorder_lite.visual_diagnostics import VisualDiagnosticSession


class VisualDiagnosticSessionTest(unittest.TestCase):
    def test_effective_fps_counts_all_analyses_after_sample_limit(self) -> None:
        now = [0.0]
        with tempfile.TemporaryDirectory() as temporary:
            session = VisualDiagnosticSession(
                Path(temporary),
                monotonic=lambda: now[0],
                clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
            )
            session._write = Mock()
            analysis = FrameAnalysis(
                elapsed_ms=0,
                state=MasterDuelState.DUEL_ACTIVE,
                profile_name="test",
                source_width=640,
                source_height=360,
                coin_score=0.0,
                board_score=1.0,
                turn_score=0.0,
                turn_order_score=0.0,
                result_score=0.0,
                error_score=0.0,
                replay_score=0.0,
                overlay_score=0.0,
                loading_score=0.0,
                candidates=(),
                agreements=(),
            )

            for frame in range(1803):
                now[0] = frame * 0.5
                session.record(analysis)

            self.assertEqual(len(session.samples), 900)
            self.assertEqual(session.samples[-1]["effective_fps"], 2.0)

    def test_report_contains_numeric_analysis_but_no_capture_metadata(self) -> None:
        now = [0.0]
        with tempfile.TemporaryDirectory() as temporary:
            session = VisualDiagnosticSession(
                Path(temporary),
                monotonic=lambda: now[0],
                clock=lambda: datetime(2026, 8, 11, tzinfo=timezone.utc),
            )
            analysis = FrameAnalysis(
                elapsed_ms=500,
                state=MasterDuelState.COIN_TOSS_CANDIDATE,
                profile_name="ultrawide-fullscreen",
                source_width=1280,
                source_height=536,
                coin_score=0.8,
                board_score=0.2,
                turn_score=0.0,
                turn_order_score=0.7,
                result_score=0.0,
                error_score=0.0,
                replay_score=0.0,
                overlay_score=0.1,
                loading_score=0.0,
                candidates=(
                    DetectionCandidate(
                        "duel_result",
                        500,
                        0.76,
                        "数値診断",
                        DETECTOR_ID,
                        DETECTOR_VERSION,
                        outcome="loss",
                        evidence="result-clean",
                    ),
                ),
                agreements=(),
            )

            self.assertTrue(session.record(analysis, effective_fps=1.9, restart_count=2))
            self.assertFalse(session.record(analysis))
            session.close()

            text = session.path.read_text(encoding="utf-8")
            report = json.loads(text)
            self.assertEqual(len(report["samples"]), 1)
            self.assertEqual(report["samples"][0]["scores"]["coin"], 0.8)
            self.assertEqual(report["samples"][0]["scores"]["turn_order"], 0.7)
            self.assertEqual(
                report["samples"][0]["candidates"],
                [
                    {
                        "event": "duel_result",
                        "confidence": 0.76,
                        "outcome": "loss",
                        "play_order": None,
                        "evidence": "result-clean",
                    }
                ],
            )
            self.assertNotIn("title", text.casefold())
            self.assertNotIn("video", text.casefold())
            self.assertNotIn("bmp", text.casefold())

    def test_only_latest_ten_sessions_are_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            for _index in range(12):
                VisualDiagnosticSession(Path(temporary)).close()

            files = tuple((Path(temporary) / "visual-monitor").glob("*.json"))

        self.assertEqual(len(files), 10)

    def test_transition_keeps_boundary_snapshot_outside_sample_ring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = VisualDiagnosticSession(
                Path(temporary),
                clock=lambda: datetime(2026, 8, 15, tzinfo=timezone.utc),
            )
            session.transition(
                "boundary_handoff_started",
                elapsed_ms=12000,
                details={
                    "handoff_ms": 42,
                    "scores": {"coin": 0.91, "result": 0.12},
                    "agreement": "duel_boundary:2/4",
                },
            )
            session.close()

            report = json.loads(session.path.read_text(encoding="utf-8"))

        transition = report["transitions"][0]
        self.assertEqual(transition["elapsed_ms"], 12000)
        self.assertEqual(transition["details"]["handoff_ms"], 42)
        self.assertEqual(transition["details"]["scores"]["coin"], 0.91)

    def test_export_contains_only_numeric_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = VisualDiagnosticSession(root)
            destination = session.export(root / "diagnostic.zip")
            with zipfile.ZipFile(destination) as archive:
                names = archive.namelist()
                payload = archive.read(names[0]).decode("utf-8")

        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].endswith(".json"))
        self.assertNotIn(".bmp", payload.casefold())


if __name__ == "__main__":
    unittest.main()
