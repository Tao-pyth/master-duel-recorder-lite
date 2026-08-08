import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.upload_metadata import UploadMetadata
from master_duel_recorder_lite.upload_queue import (
    UploadQueueError,
    UploadQueueState,
    UploadQueueStore,
)


class UploadQueueStoreTest(unittest.TestCase):
    def make_store(self, root: Path) -> UploadQueueStore:
        paths = default_runtime_paths(user_data_dir=root / "user_data")
        ensure_runtime_dirs(paths)
        return UploadQueueStore(paths)

    def test_enqueue_round_trip_and_duplicate_control(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self.make_store(Path(tmp_dir))
            item = store.enqueue(recording_id="recording", metadata=UploadMetadata("title"))
            restored = UploadQueueStore(
                default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ).get(item.queue_id)

            with self.assertRaises(UploadQueueError):
                store.enqueue(recording_id="recording", metadata=UploadMetadata("duplicate"))

        assert restored is not None
        self.assertIs(restored.state, UploadQueueState.WAITING)
        self.assertEqual(restored.metadata.title, "title")

    def test_state_transitions_and_invalid_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self.make_store(Path(tmp_dir))
            item = store.enqueue(recording_id="recording", metadata=UploadMetadata("title"))
            processing = store.transition(
                item.queue_id,
                UploadQueueState.PROCESSING,
                increment_attempts=True,
            )
            completed = store.transition(item.queue_id, UploadQueueState.COMPLETED)

            with self.assertRaises(UploadQueueError):
                store.transition(item.queue_id, UploadQueueState.PROCESSING)

        self.assertEqual(processing.attempts, 1)
        self.assertIs(completed.state, UploadQueueState.COMPLETED)

    def test_processing_item_is_restored_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            store = self.make_store(root)
            item = store.enqueue(recording_id="recording", metadata=UploadMetadata("title"))
            store.transition(item.queue_id, UploadQueueState.PROCESSING)
            restarted = self.make_store(root)

            restored = restarted.restore_interrupted()
            loaded = restarted.get(item.queue_id)

        self.assertEqual(len(restored), 1)
        assert loaded is not None
        self.assertIs(loaded.state, UploadQueueState.WAITING)
        self.assertIn("中断", loaded.error or "")

    def test_corrupt_current_falls_back_to_previous_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = self.make_store(Path(tmp_dir))
            first = store.enqueue(recording_id="first", metadata=UploadMetadata("first"))
            store.enqueue(recording_id="second", metadata=UploadMetadata("second"))
            store.path.write_text('{"partial":', encoding="utf-8")

            items = store.list()
            recovered = store.enqueue(
                recording_id="recovered",
                metadata=UploadMetadata("recovered"),
            )
            after_recovery = store.list()

        self.assertEqual([item.queue_id for item in items], [first.queue_id])
        self.assertEqual(
            [item.queue_id for item in after_recovery],
            [first.queue_id, recovered.queue_id],
        )


if __name__ == "__main__":
    unittest.main()
