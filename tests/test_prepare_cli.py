from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import RuntimePaths, default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_queue import UploadQueueState, UploadQueueStore
from master_duel_recorder_lite.upload_metadata import UploadMetadata


class PrepareCliTest(unittest.TestCase):
    def add_completed(self, paths: RuntimePaths, recording_id: str) -> None:
        output = paths.recordings / f"{recording_id}.mkv"
        output.write_bytes(b"video")
        now = datetime.now(timezone.utc)
        repository = RecordingHistoryRepository.from_runtime_paths(paths)
        repository.register_starting(
            recording_id=recording_id,
            output_path=output,
            container="mkv",
            source="manual",
            created_at=now,
        )
        repository.mark_recording(recording_id, started_at=now)
        repository.finalize(
            recording_id,
            RecordingResult(
                RecordingState.COMPLETED,
                output,
                0,
                now,
                now,
                output.stat().st_size,
                None,
                (),
            ),
        )

    def test_enqueue_list_show_and_cancel_use_private_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            ensure_runtime_dirs(paths)
            self.add_completed(paths, "recording")
            output = io.StringIO()
            with redirect_stdout(output):
                enqueue_code = main(
                    [
                        "--user-data-dir",
                        str(root),
                        "prepare",
                        "enqueue",
                        "recording",
                        "--title",
                        "対戦記録",
                        "--tag",
                        "Master Duel",
                    ]
                )
            item = UploadQueueStore(paths).list()[0]
            with redirect_stdout(io.StringIO()):
                list_code = main(["--user-data-dir", str(root), "prepare", "list"])
                show_code = main(
                    ["--user-data-dir", str(root), "prepare", "show", item.queue_id]
                )
                cancel_code = main(
                    ["--user-data-dir", str(root), "prepare", "cancel", item.queue_id]
                )
            cancelled = UploadQueueStore(paths).get(item.queue_id)

        self.assertEqual((enqueue_code, list_code, show_code, cancel_code), (0, 0, 0, 0))
        self.assertEqual(item.metadata.privacy.value, "private")
        assert cancelled is not None
        self.assertIs(cancelled.state, UploadQueueState.CANCELLED)

    def test_empty_run_needs_no_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["--user-data-dir", tmp_dir, "prepare", "run"]
                )

        self.assertEqual(exit_code, 0)
        self.assertIn("処理対象", output.getvalue())

    def test_ctrl_c_keeps_restartable_queue_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            ensure_runtime_dirs(paths)
            self.add_completed(paths, "interrupted")
            queue = UploadQueueStore(paths)
            item = queue.enqueue(recording_id="interrupted", metadata=UploadMetadata(title="中断"))

            def interrupt(
                queue_id: str | None,
                *,
                progress: object | None = None,
            ) -> tuple[object, ...]:
                assert queue_id is not None
                self.assertIsNotNone(progress)
                queue.transition(queue_id, UploadQueueState.PROCESSING, increment_attempts=True)
                raise KeyboardInterrupt

            output = io.StringIO()
            with (
                patch(
                    "master_duel_recorder_lite.__main__.discover_ffmpeg",
                    return_value=SimpleNamespace(found=True, executable=Path("C:/ffmpeg.exe")),
                ),
                patch(
                    "master_duel_recorder_lite.__main__.find_ffprobe",
                    return_value=Path("C:/ffprobe.exe"),
                ),
                patch(
                    "master_duel_recorder_lite.__main__.UploadPreparationService.process",
                    side_effect=interrupt,
                ),
                redirect_stdout(output),
            ):
                interrupted_code = main(
                    ["--user-data-dir", str(root), "prepare", "run", item.queue_id]
                )
            processing = UploadQueueStore(paths).get(item.queue_id)
            with redirect_stdout(output):
                list_code = main(["--user-data-dir", str(root), "prepare", "list"])
            restored = UploadQueueStore(paths).get(item.queue_id)

        self.assertEqual(interrupted_code, 130)
        self.assertEqual(list_code, 0)
        assert processing is not None and restored is not None
        self.assertIs(processing.state, UploadQueueState.PROCESSING)
        self.assertIs(restored.state, UploadQueueState.WAITING)
        self.assertIn("停止要求", output.getvalue())
        self.assertIn("待機状態へ戻しました", output.getvalue())


if __name__ == "__main__":
    unittest.main()
