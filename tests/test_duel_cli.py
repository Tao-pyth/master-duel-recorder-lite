import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from master_duel_recorder_lite.__main__ import main
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


class DuelCliTest(unittest.TestCase):
    @staticmethod
    def _register_completed(paths: object) -> None:
        output = paths.recordings / "recording.mkv"
        output.write_bytes(b"recording")
        repository = RecordingHistoryRepository.from_runtime_paths(paths)
        now = datetime.now(timezone.utc)
        repository.register_starting(
            recording_id="recording",
            output_path=output,
            container="mkv",
            source="manual",
        )
        repository.finalize(
            "recording",
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

    def test_set_show_confirm_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            ensure_runtime_dirs(paths)
            self._register_completed(paths)
            output = io.StringIO()
            with redirect_stdout(output):
                set_code = main(
                    [
                        "--user-data-dir",
                        str(root),
                        "duel",
                        "set",
                        "recording",
                        "--revision",
                        "0",
                        "--result",
                        "win",
                        "--play-order",
                        "first",
                        "--own-deck",
                        "青眼",
                        "--tag",
                        "昇格戦",
                        "--json",
                    ]
                )
            created = json.loads(output.getvalue())
            output = io.StringIO()
            with redirect_stdout(output):
                show_code = main(
                    ["--user-data-dir", str(root), "duel", "show", "recording", "--json"]
                )
            shown = json.loads(output.getvalue())
            with redirect_stdout(io.StringIO()):
                confirm_code = main(
                    [
                        "--user-data-dir",
                        str(root),
                        "duel",
                        "confirm",
                        "recording",
                        "--revision",
                        "1",
                        "--json",
                    ]
                )
            output = io.StringIO()
            with redirect_stdout(output):
                history_code = main(
                    ["--user-data-dir", str(root), "duel", "history", "recording", "--json"]
                )

        self.assertEqual((set_code, show_code, confirm_code, history_code), (0, 0, 0, 0))
        self.assertEqual(created["result"], "win")
        self.assertEqual(shown["tags"], ["昇格戦"])
        self.assertNotIn("output_path", output.getvalue())
        self.assertEqual(len(json.loads(output.getvalue())), 2)

    def test_stale_revision_returns_attention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "user_data"
            paths = default_runtime_paths(user_data_dir=root)
            ensure_runtime_dirs(paths)
            self._register_completed(paths)
            with redirect_stdout(io.StringIO()):
                main(
                    [
                        "--user-data-dir",
                        str(root),
                        "duel",
                        "set",
                        "recording",
                        "--revision",
                        "0",
                    ]
                )
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(
                    [
                        "--user-data-dir",
                        str(root),
                        "duel",
                        "set",
                        "recording",
                        "--revision",
                        "0",
                        "--result",
                        "loss",
                    ]
                )

        self.assertEqual(code, 4)
        self.assertIn("E_DUEL_CONFLICT", error.getvalue())


if __name__ == "__main__":
    unittest.main()
