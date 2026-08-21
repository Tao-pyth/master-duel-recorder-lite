import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
