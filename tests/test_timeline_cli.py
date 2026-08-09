import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.duel_timeline import DuelTimelineRepository
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import (
    default_runtime_paths,
    ensure_runtime_dirs,
)


class TimelineCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "user_data"
        self.paths = default_runtime_paths(user_data_dir=self.root)
        ensure_runtime_dirs(self.paths)
        output = self.paths.recordings / "recording.mkv"
        output.write_bytes(b"recording")
        now = datetime.now(timezone.utc)
        history = RecordingHistoryRepository.from_runtime_paths(self.paths)
        history.register_starting(
            recording_id="recording",
            output_path=output,
            container="mkv",
            source="manual",
        )
        history.finalize(
            "recording",
            RecordingResult(
                RecordingState.COMPLETED,
                output,
                0,
                now,
                now + timedelta(seconds=60),
                output.stat().st_size,
                None,
                (),
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_add_and_filtered_json_list(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            add_code = main(
                [
                    "--user-data-dir",
                    str(self.root),
                    "timeline",
                    "add",
                    "recording",
                    "--elapsed-ms",
                    "1000",
                    "--type",
                    "duel_start",
                    "--json",
                ]
            )
        created = json.loads(output.getvalue())
        output = io.StringIO()
        with redirect_stdout(output):
            list_code = main(
                [
                    "--user-data-dir",
                    str(self.root),
                    "timeline",
                    "list",
                    "recording",
                    "--status",
                    "confirmed",
                    "--type",
                    "duel_start",
                    "--json",
                ]
            )

        self.assertEqual((add_code, list_code), (0, 0))
        self.assertEqual(created["source"], "manual")
        self.assertEqual(
            json.loads(output.getvalue())[0]["event_id"], created["event_id"]
        )

    def test_confirm_and_reject_candidates(self) -> None:
        repository = DuelTimelineRepository.from_runtime_paths(self.paths)
        confirmed_candidate = repository.add(
            "recording",
            elapsed_ms=1000,
            event_type="duel_start",
            source="detected",
            status="candidate",
            confidence=0.9,
            detector_id="test",
            detector_version="1",
        )
        rejected_candidate = repository.add(
            "recording",
            elapsed_ms=2000,
            event_type="marker",
            label="candidate",
            source="detected",
            status="candidate",
            confidence=0.8,
            detector_id="test",
            detector_version="1",
        )
        with redirect_stdout(io.StringIO()):
            confirm_code = main(
                [
                    "--user-data-dir",
                    str(self.root),
                    "timeline",
                    "confirm",
                    confirmed_candidate.event_id,
                ]
            )
            reject_code = main(
                [
                    "--user-data-dir",
                    str(self.root),
                    "timeline",
                    "reject",
                    rejected_candidate.event_id,
                ]
            )

        self.assertEqual((confirm_code, reject_code), (0, 0))
        self.assertEqual(
            repository.get(confirmed_candidate.event_id).status, "confirmed"
        )  # type: ignore[union-attr]
        self.assertEqual(repository.get(rejected_candidate.event_id).status, "rejected")  # type: ignore[union-attr]

    def test_invalid_manual_event_returns_attention(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            code = main(
                [
                    "--user-data-dir",
                    str(self.root),
                    "timeline",
                    "add",
                    "recording",
                    "--elapsed-ms",
                    "1000",
                    "--type",
                    "duel_result",
                ]
            )

        self.assertEqual(code, 4)
        self.assertIn("E_TIMELINE", error.getvalue())


if __name__ == "__main__":
    unittest.main()
