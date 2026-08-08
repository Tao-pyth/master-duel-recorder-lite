from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.recording_state_store import (
    RecordingStateStore,
    RecordingStateStoreError,
)
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


BASE_TIME = datetime(2026, 8, 8, tzinfo=timezone.utc)


class RecordingStateStoreTest(unittest.TestCase):
    def make_store(self, root: Path) -> tuple[RecordingStateStore, Path]:
        paths = default_runtime_paths(user_data_dir=root / "user_data")
        ensure_runtime_dirs(paths)
        return RecordingStateStore(paths), paths.recordings / "recording.mkv"

    def test_latest_and_previous_generations_are_retained(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, output = self.make_store(Path(tmp_dir))
            store.save(
                recording_id="id",
                state="starting",
                source="manual",
                output_path=output,
                started_at=None,
                updated_at=BASE_TIME,
            )
            store.save(
                recording_id="id",
                state="recording",
                source="manual",
                output_path=output,
                started_at=BASE_TIME,
                updated_at=BASE_TIME + timedelta(seconds=1),
            )
            latest = store.load()
            previous_document = json.loads(store.previous_path.read_text(encoding="utf-8"))

        assert latest is not None
        self.assertEqual(latest.value.state, "recording")
        self.assertFalse(latest.used_previous)
        self.assertEqual(previous_document["state"], "starting")

    def test_interrupted_replace_keeps_previous_valid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, output = self.make_store(Path(tmp_dir))
            store.save(
                recording_id="id",
                state="starting",
                source="manual",
                output_path=output,
                started_at=None,
                updated_at=BASE_TIME,
            )
            real_replace = __import__("os").replace

            def fail_current(source: Path, destination: Path) -> None:
                if Path(destination) == store.state_path:
                    raise OSError("injected replace failure")
                real_replace(source, destination)

            with patch(
                "master_duel_recorder_lite.recording_state_store.os.replace",
                side_effect=fail_current,
            ):
                with self.assertRaises(RecordingStateStoreError):
                    store.save(
                        recording_id="id",
                        state="recording",
                        source="manual",
                        output_path=output,
                        started_at=BASE_TIME,
                        updated_at=BASE_TIME + timedelta(seconds=1),
                    )

            loaded = store.load()

        assert loaded is not None
        self.assertEqual(loaded.value.state, "starting")

    def test_corrupt_current_falls_back_to_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, output = self.make_store(Path(tmp_dir))
            for state, second in (("starting", 0), ("recording", 1)):
                store.save(
                    recording_id="id",
                    state=state,
                    source="manual",
                    output_path=output,
                    started_at=BASE_TIME if state == "recording" else None,
                    updated_at=BASE_TIME + timedelta(seconds=second),
                )
            store.state_path.write_text('{"partial":', encoding="utf-8")

            loaded = store.load()

        assert loaded is not None
        self.assertTrue(loaded.used_previous)
        self.assertEqual(loaded.value.state, "starting")

    def test_output_outside_recordings_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            store, _output = self.make_store(root)

            with self.assertRaises(RecordingStateStoreError):
                store.save(
                    recording_id="id",
                    state="recording",
                    source="manual",
                    output_path=root / "outside.mkv",
                    started_at=BASE_TIME,
                )


if __name__ == "__main__":
    unittest.main()
