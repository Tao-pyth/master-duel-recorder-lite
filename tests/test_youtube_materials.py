import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from master_duel_recorder_lite.description_template import (
    YouTubePostingTemplate,
    save_youtube_posting_template,
)
from master_duel_recorder_lite.recording_history import RecordingHistoryEntry
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.youtube_materials import YouTubeMaterialService


class YouTubeMaterialServiceTest(unittest.TestCase):
    def test_generates_materials_without_oauth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            history = RecordingHistoryEntry(
                recording_id="rec-1",
                state="completed",
                source="manual",
                detection_reason=None,
                output_path=Path("2026/08/21/recording.mp4"),
                container="mp4",
                created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
                started_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
                ended_at=datetime(2026, 8, 21, 0, 1, tzinfo=timezone.utc),
                duration_seconds=60.0,
                size_bytes=4,
                returncode=0,
                error=None,
                diagnostics=(),
                failure_code=None,
                audio_input=None,
                audio_state="disabled",
                audio_warning=None,
                updated_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            )

            materials = YouTubeMaterialService(paths).generate(history=history)

        self.assertIn("Master Duel", materials.title)
        self.assertIn("録画ID: rec-1", materials.description)
        self.assertIn("公開範囲を確認した", materials.checklist)
        self.assertIn("Master Duel", materials.tags)

    def test_saved_posting_template_controls_title_description_and_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            save_youtube_posting_template(
                paths.config,
                YouTubePostingTemplate(
                    title="対戦 {deckname}",
                    description="録画 {recording_id}",
                    tags="Master Duel, {deckname}",
                ),
            )
            history = RecordingHistoryEntry(
                recording_id="rec-2",
                state="completed",
                source="manual",
                detection_reason=None,
                output_path=Path("2026/08/21/recording.mp4"),
                container="mp4",
                created_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
                started_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
                ended_at=datetime(2026, 8, 21, 0, 1, tzinfo=timezone.utc),
                duration_seconds=60.0,
                size_bytes=4,
                returncode=0,
                error=None,
                diagnostics=(),
                failure_code=None,
                audio_input=None,
                audio_state="disabled",
                audio_warning=None,
                updated_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            )
            duel_record = type(
                "Record",
                (),
                {"values": type("Values", (), {"own_deck": "青眼"})()},
            )()

            materials = YouTubeMaterialService(paths).generate(
                history=history, duel_record=duel_record
            )

        self.assertEqual(materials.title, "対戦 青眼")
        self.assertEqual(materials.description, "録画 rec-2")
        self.assertEqual(materials.tags, ("Master Duel", "青眼"))


if __name__ == "__main__":
    unittest.main()
