import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from master_duel_recorder_lite.visual_contracts import evaluate_frame_contracts
from master_duel_recorder_lite.visual_dataset import (
    DatasetEvaluation,
    EventEvaluation,
    VideoDatasetEntry,
    VideoEvaluation,
    VisualDataset,
)
from master_duel_recorder_lite.visual_detection import FrameAnalysis, MasterDuelState
from master_duel_recorder_lite.frame_capture import FrameSample
from master_duel_recorder_lite.visual_detection import (
    DetectionCandidate,
    TemporalEventConsensus,
    VisualDetectionPipeline,
)


class FixedDetector:
    def detect(
        self, _frame: FrameSample, elapsed_ms: int
    ) -> tuple[DetectionCandidate, ...]:
        return (
            DetectionCandidate(
                "duel_start",
                elapsed_ms,
                0.9,
                "fixed",
                "contract-test",
                "1",
            ),
        )


class VisualContractsTest(unittest.TestCase):
    def test_live_and_offline_adapters_share_frame_analysis_contract(self) -> None:
        sample = FrameSample(
            datetime.now(timezone.utc), 1, "not-persisted", 640, 360, "bmp", b""
        )

        def analyze_stream() -> FrameAnalysis:
            pipeline = VisualDetectionPipeline(
                FixedDetector(), TemporalEventConsensus(confirmations=2)
            )
            pipeline.analyze_frame(sample, 1000)
            return pipeline.analyze_frame(sample, 1500)

        self.assertEqual(analyze_stream(), analyze_stream())

    def test_event_contracts_extract_independent_scores(self) -> None:
        analysis = FrameAnalysis(
            1000, MasterDuelState.DUEL_ACTIVE, "standard-16:9-window", 640, 360,
            0.8, 0.9, 0.75, 0.1, 0.2, 0.0, 0.0, 0.0, 0.0, (), ()
        )
        evidence = {item.event_type: item for item in evaluate_frame_contracts(analysis)}
        self.assertTrue(evidence["duel_start"].threshold_met)
        self.assertTrue(evidence["duel_confirmed"].threshold_met)
        self.assertTrue(evidence["turn_change"].threshold_met)
        self.assertFalse(evidence["duel_result"].threshold_met)

    def test_dataset_metrics_are_grouped_and_written_atomically(self) -> None:
        entry = VideoDatasetEntry(
            "one", Path("one.mkv"), "live", "standard", "ranked", None, (), ()
        )
        dataset = VisualDataset("set", Path("manifest.json"), (entry,))
        report = DatasetEvaluation(
            "set", 2.0,
            (VideoEvaluation("one", "evaluated", 1, (
                EventEvaluation("one", "duel_start", 1000, 1200, True, 200, "ok"),
            )),),
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = report.write_json(Path(tmp_dir) / "report.json", dataset)
            payload = json.loads(target.read_text(encoding="utf-8"))
        metric = payload["metrics_by_profile"]["standard"]["duel_start"]
        self.assertEqual(metric["recall"], 1.0)
        self.assertEqual(metric["mean_absolute_latency_ms"], 200.0)


if __name__ == "__main__":
    unittest.main()
