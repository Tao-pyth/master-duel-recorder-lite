from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths
from master_duel_recorder_lite.upload_queue import UploadQueueState, UploadQueueStore


class WorkflowE2ETest(unittest.TestCase):
    def test_initialization_history_and_preparation_share_recording_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            with redirect_stdout(io.StringIO()):
                init_code = main(["--user-data-dir", str(root), "config", "init"])
                set_code = main(
                    [
                        "--user-data-dir",
                        str(root),
                        "config",
                        "set",
                        "upload.privacy_status",
                        "unlisted",
                    ]
                )

            recording_id = "workflow-recording"
            recording = paths.recordings / "workflow.mkv"
            recording.write_bytes(b"preserved recording")
            original = recording.read_bytes()
            now = datetime.now(timezone.utc)
            repository = RecordingHistoryRepository.from_runtime_paths(paths)
            repository.register_starting(
                recording_id=recording_id,
                output_path=recording,
                container="mkv",
                source="manual",
                created_at=now,
            )
            repository.mark_recording(recording_id, started_at=now)
            repository.finalize(
                recording_id,
                RecordingResult(
                    RecordingState.COMPLETED,
                    recording,
                    0,
                    now,
                    now,
                    len(original),
                    None,
                    (),
                ),
            )

            history_output = io.StringIO()
            with redirect_stdout(history_output):
                history_code = main(
                    ["--user-data-dir", str(root), "history", "show", recording_id]
                )
            enqueue_output = io.StringIO()
            with redirect_stdout(enqueue_output):
                enqueue_code = main(
                    [
                        "--user-data-dir",
                        str(root),
                        "prepare",
                        "enqueue",
                        recording_id,
                        "--title",
                        "統合テスト",
                    ]
                )
            item = UploadQueueStore(paths).list()[0]
            list_output = io.StringIO()
            with redirect_stdout(list_output):
                list_code = main(["--user-data-dir", str(root), "prepare", "list"])
                cancel_code = main(
                    ["--user-data-dir", str(root), "prepare", "cancel", item.queue_id]
                )
            cancelled = UploadQueueStore(paths).get(item.queue_id)
            preserved = recording.read_bytes()

        self.assertEqual(
            (init_code, set_code, history_code, enqueue_code, list_code, cancel_code),
            (0, 0, 0, 0, 0, 0),
        )
        self.assertIn(recording_id, history_output.getvalue())
        self.assertIn(recording_id, enqueue_output.getvalue())
        self.assertIn(recording_id, list_output.getvalue())
        self.assertEqual(item.metadata.privacy.value, "unlisted")
        self.assertIsNotNone(cancelled)
        assert cancelled is not None
        self.assertIs(cancelled.state, UploadQueueState.CANCELLED)
        self.assertEqual(preserved, original)


if __name__ == "__main__":
    unittest.main()
