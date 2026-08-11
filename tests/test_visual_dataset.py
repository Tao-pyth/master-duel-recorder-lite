import json
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.visual_dataset import (
    evaluate_visual_dataset,
    load_visual_dataset,
    render_evaluation_markdown,
)


class VisualDatasetTest(unittest.TestCase):
    def test_manifest_loads_relative_video_and_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "dataset_id": "test-ja",
                        "videos": [
                            {
                                "id": "video-01",
                                "file": "videos/one.mkv",
                                "source": "replay",
                                "display_profile": "standard-16:9-window",
                                "duel_type": "event",
                                "has_audio": True,
                                "strict_event_types": ["duel_result"],
                                "events": [
                                    {
                                        "event_type": "duel_result",
                                        "window_start_ms": 1000,
                                        "window_end_ms": 2000,
                                        "outcome": "win",
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            dataset = load_visual_dataset(manifest)

        self.assertEqual(dataset.dataset_id, "test-ja")
        self.assertEqual(dataset.videos[0].source, "replay")
        self.assertEqual(dataset.videos[0].file, root / "videos" / "one.mkv")
        self.assertEqual(dataset.videos[0].events[0].outcome, "win")

    def test_missing_local_videos_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = Path(tmp_dir) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "dataset_id": "missing-data",
                        "videos": [
                            {
                                "id": "not-installed",
                                "file": "missing.mkv",
                                "source": "live",
                                "display_profile": "standard-16:9-window",
                                "events": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            report = evaluate_visual_dataset(
                load_visual_dataset(manifest), Path("ffmpeg.exe"), sample_fps=1
            )

        self.assertEqual(report.videos[0].status, "skipped")
        self.assertIn("動画未配置", render_evaluation_markdown(report))

    def test_duplicate_video_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manifest = Path(tmp_dir) / "manifest.json"
            entry = {
                "id": "same",
                "file": "missing.mkv",
                "source": "live",
                "display_profile": "standard-16:9-window",
                "events": [],
            }
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "dataset_id": "duplicates",
                        "videos": [entry, entry],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_visual_dataset(manifest)


if __name__ == "__main__":
    unittest.main()
