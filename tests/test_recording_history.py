from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from master_duel_recorder_lite.duel_records import (
    DuelRecordRepository,
    DuelRecordValues,
)
from master_duel_recorder_lite.duel_catalog import DuelCatalogRepository
from master_duel_recorder_lite.seasons import SeasonRepository
from master_duel_recorder_lite.duel_timeline import DuelTimelineRepository
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
            second = repository.finalize(
                "recording-1", completed_result(output, size=5)
            )
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

    def test_query_combines_season_decks_and_tag_or_before_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            repository = self.make_repository(root)
            records = DuelRecordRepository(repository.database_path)
            catalog = DuelCatalogRepository(repository.database_path)
            seasons = SeasonRepository(repository.database_path)
            season = seasons.add(
                name="Season 40",
                season_type="ranked",
                duel_type="ranked",
                start_date=BASE_TIME.date(),
                end_date=BASE_TIME.date(),
            )
            for index, (recording_id, own, opponent, tags) in enumerate(
                (
                    ("target", "青眼", "烙印", ("大会",)),
                    ("wrong-own", "粛声", "烙印", ("大会",)),
                    ("wrong-tag", "青眼", "烙印", ("練習",)),
                )
            ):
                repository.register_starting(
                    recording_id=recording_id,
                    output_path=root / "recordings" / f"{recording_id}.mkv",
                    container="mkv",
                    source="automatic",
                    created_at=BASE_TIME + timedelta(seconds=index),
                )
                records.save(
                    recording_id,
                    DuelRecordValues(
                        own_deck=own,
                        opponent_deck=opponent,
                        tags=tags,
                        season_id=season.season_id,
                    ),
                    expected_revision=0,
                )
            decks = {item.name: item.entry_id for item in catalog.list_decks()}
            tags = {item.name: item.entry_id for item in catalog.list_tags()}

            entries = repository.query(
                HistoryQuery(
                    season_id=season.season_id,
                    own_deck_id=decks["青眼"],
                    opponent_deck_id=decks["烙印"],
                    tag_entry_ids=(tags["大会"], tags["公式"])
                    if "公式" in tags
                    else (tags["大会"],),
                    limit=1,
                )
            )

        self.assertEqual([entry.recording_id for entry in entries], ["target"])

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

    def test_failed_result_keeps_failure_diagnostic(self) -> None:
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

    def test_interrupted_active_recording_becomes_failed(self) -> None:
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
        self.assertEqual(entry.duration_seconds, 10.0)

    def test_consistency_distinguishes_missing_untracked_and_size_mismatch(
        self,
    ) -> None:
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

    def test_delete_removes_file_and_all_recording_relations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            recordings = root / "recordings"
            output = recordings / "partial.mkv"
            recordings.mkdir()
            output.write_bytes(b"partial")
            repository = self.make_repository(root)
            repository.register_starting(
                recording_id="delete-me",
                output_path=output,
                container="mkv",
                source="automatic",
                created_at=BASE_TIME,
            )
            repository.mark_recording("delete-me", started_at=BASE_TIME)
            classification = classify_recording_failure(
                error="interrupted",
                returncode=None,
                output_exists=True,
                output_size=7,
                interrupted=True,
            )
            repository.mark_interrupted(
                "delete-me",
                classification=classification,
                ended_at=BASE_TIME + timedelta(seconds=10),
                size_bytes=7,
            )
            DuelRecordRepository(repository.database_path).save(
                "delete-me",
                DuelRecordValues(own_deck="青眼", tags=("大会",)),
                expected_revision=0,
            )
            DuelTimelineRepository(repository.database_path).add(
                "delete-me",
                elapsed_ms=1000,
                event_type="marker",
                label="確認",
            )

            result = repository.delete("delete-me")

            with closing(sqlite3.connect(repository.database_path)) as connection:
                counts = {
                    table: connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                    for table in (
                        "recordings",
                        "duel_records",
                        "duel_record_tags",
                        "duel_record_changes",
                        "duel_events",
                    )
                }

        self.assertEqual(result.recording_id, "delete-me")
        self.assertEqual(set(result.deleted_files), {output.resolve()})
        self.assertFalse(output.exists())
        self.assertIsNone(repository.get("delete-me"))
        self.assertEqual(set(counts.values()), {0})

    def test_delete_with_missing_file_still_removes_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output = root / "recordings" / "missing.mkv"
            repository = self.make_repository(root)
            repository.register_starting(
                recording_id="missing",
                output_path=output,
                container="mkv",
                source="manual",
                created_at=BASE_TIME,
            )

            result = repository.delete("missing")

        self.assertEqual(result.missing_files, (output.resolve(),))
        self.assertIsNone(repository.get("missing"))

    def test_delete_staging_failure_preserves_history_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output = root / "recordings" / "recording.mkv"
            output.parent.mkdir()
            output.write_bytes(b"video")
            repository = self.make_repository(root)
            repository.register_starting(
                recording_id="preserve",
                output_path=output,
                container="mkv",
                source="manual",
                created_at=BASE_TIME,
            )

            with (
                patch("pathlib.Path.replace", side_effect=OSError("injected failure")),
                self.assertRaisesRegex(RecordingHistoryError, "削除できません"),
            ):
                repository.delete("preserve")

            preserved = repository.get("preserve")
            file_preserved = output.exists()

        self.assertIsNotNone(preserved)
        self.assertTrue(file_preserved)


if __name__ == "__main__":
    unittest.main()
