from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.recording_history import (
    ConsistencyIssueKind,
    HistoryQuery,
    RecordingHistoryError,
    RecordingHistoryRepository,
)
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.recording_failure import classify_recording_failure


BASE_TIME = datetime(2026, 8, 8, 1, 2, 3, tzinfo=timezone.utc)


def completed_result(path: Path, *, size: int, second: int = 5) -> RecordingResult:
    return RecordingResult(
        state=RecordingState.COMPLETED,
        output_path=path,
        returncode=0,
        started_at=BASE_TIME,
        ended_at=BASE_TIME + timedelta(seconds=second),
        size_bytes=size,
        error=None,
        diagnostics=("done",),
    )


class RecordingHistoryRepositoryTest(unittest.TestCase):
    def make_repository(self, root: Path) -> RecordingHistoryRepository:
        return RecordingHistoryRepository(
            database_path=root / "db" / "history.sqlite3",
            recordings_root=root / "recordings",
        )

    def test_lifecycle_updates_one_record_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output = root / "recordings" / "2026" / "recording.mkv"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"video")
            repository = self.make_repository(root)

            repository.register_starting(
                recording_id="recording-1",
                output_path=output,
                container="mkv",
                source="manual",
                created_at=BASE_TIME,
            )
            repository.mark_recording("recording-1", started_at=BASE_TIME)
            first = repository.finalize("recording-1", completed_result(output, size=5))
            second = repository.finalize("recording-1", completed_result(output, size=5))
            entries = repository.query()

        self.assertEqual(first.state, "completed")
        self.assertEqual(second.state, "completed")
        self.assertEqual(first.duration_seconds, 5.0)
        self.assertEqual(first.diagnostics, ("done",))
        self.assertEqual(len(entries), 1)

    def test_query_combines_filters_with_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repository = self.make_repository(root)
            for recording_id, second in (("b", 1), ("a", 1), ("old", 0)):
                repository.register_starting(
                    recording_id=recording_id,
                    output_path=root / "recordings" / f"{recording_id}.mkv",
                    container="mkv",
                    source="automatic",
                    created_at=BASE_TIME + timedelta(seconds=second),
                )
            entries = repository.query(
                HistoryQuery(
                    state="starting",
                    since=BASE_TIME + timedelta(seconds=1),
                    until=BASE_TIME + timedelta(seconds=2),
                    limit=2,
                )
            )

        self.assertEqual([entry.recording_id for entry in entries], ["b", "a"])

    def test_unknown_recording_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repository = self.make_repository(Path(tmp_dir))
            self.assertIsNone(repository.get("missing"))

    def test_output_path_outside_recordings_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repository = self.make_repository(root)

            with self.assertRaises(RecordingHistoryError):
                repository.register_starting(
                    recording_id="outside",
                    output_path=root / "outside.mkv",
                    container="mkv",
                    source="manual",
                    created_at=BASE_TIME,
                )

    def test_finalize_rejects_result_for_another_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repository = self.make_repository(root)
            expected = root / "recordings" / "expected.mkv"
            other = root / "recordings" / "other.mkv"
            repository.register_starting(
                recording_id="recording",
                output_path=expected,
                container="mkv",
                source="manual",
                created_at=BASE_TIME,
            )

            with self.assertRaisesRegex(RecordingHistoryError, "一致しません"):
                repository.finalize("recording", completed_result(other, size=5))

    def test_failed_result_is_classified_for_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output = root / "recordings" / "partial.mkv"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"partial")
            repository = self.make_repository(root)
            repository.register_starting(
                recording_id="failed",
                output_path=output,
                container="mkv",
                source="automatic",
                created_at=BASE_TIME,
            )
            failed = RecordingResult(
                state=RecordingState.FAILED,
                output_path=output,
                returncode=1,
                started_at=BASE_TIME,
                ended_at=BASE_TIME + timedelta(seconds=2),
                size_bytes=7,
                error="encoder crashed",
                diagnostics=("internal detail",),
            )

            entry = repository.finalize("failed", failed)

        self.assertEqual(entry.failure_code, "process_crash")
        self.assertEqual(entry.recovery_policy, "manual_review")
        self.assertEqual(entry.recovery_state, "pending")

    def test_interrupted_active_recording_becomes_pending_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output = root / "recordings" / "partial.mkv"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"partial")
            repository = self.make_repository(root)
            repository.register_starting(
                recording_id="interrupted",
                output_path=output,
                container="mkv",
                source="manual",
                created_at=BASE_TIME,
            )
            repository.mark_recording("interrupted", started_at=BASE_TIME)
            classification = classify_recording_failure(
                error="process no longer exists",
                returncode=None,
                output_exists=True,
                output_size=7,
                interrupted=True,
            )

            entry = repository.mark_interrupted(
                "interrupted",
                classification=classification,
                ended_at=BASE_TIME + timedelta(seconds=10),
                size_bytes=7,
            )

        self.assertEqual(entry.state, "failed")
        self.assertEqual(entry.failure_code, "application_interrupted")
        self.assertEqual(entry.recovery_state, "pending")
        self.assertEqual(entry.duration_seconds, 10.0)

    def test_consistency_distinguishes_missing_untracked_and_size_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            recordings = root / "recordings"
            recordings.mkdir()
            mismatch = recordings / "mismatch.mkv"
            mismatch.write_bytes(b"actual")
            untracked = recordings / "untracked.mp4"
            untracked.write_bytes(b"untracked")
            repository = self.make_repository(root)
            repository.register_starting(
                recording_id="missing",
                output_path=recordings / "missing.mkv",
                container="mkv",
                source="manual",
                created_at=BASE_TIME,
            )
            repository.register_starting(
                recording_id="mismatch",
                output_path=mismatch,
                container="mkv",
                source="manual",
                created_at=BASE_TIME,
            )
            repository.mark_recording("mismatch", started_at=BASE_TIME)
            repository.finalize("mismatch", completed_result(mismatch, size=999))

            issues = repository.check_consistency()
            untracked_preserved = untracked.exists()

        self.assertEqual(
            {issue.kind for issue in issues},
            {
                ConsistencyIssueKind.MISSING,
                ConsistencyIssueKind.SIZE_MISMATCH,
                ConsistencyIssueKind.UNTRACKED,
            },
        )
        self.assertTrue(untracked_preserved)


if __name__ == "__main__":
    unittest.main()
