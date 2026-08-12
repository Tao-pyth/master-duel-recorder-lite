from datetime import date, datetime, timezone
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.duel_records import (
    DuelRecordRepository,
    DuelRecordValues,
)
from master_duel_recorder_lite.recording_history import (
    HistoryQuery,
    RecordingHistoryRepository,
)
from master_duel_recorder_lite.seasons import SeasonRepository


class SeasonRepositoryTest(unittest.TestCase):
    def test_crud_archive_and_history_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = RecordingHistoryRepository(
                database_path=root / "history.sqlite3",
                recordings_root=root / "recordings",
            )
            seasons = SeasonRepository(history.database_path)
            season = seasons.add(
                name="ランク Season 40",
                season_type="ranked",
                duel_type="ranked",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 31),
                description="八月",
                report_notes="振り返り",
            )
            output = root / "recordings" / "duel.mkv"
            output.parent.mkdir()
            output.write_bytes(b"video")
            history.register_starting(
                recording_id="duel",
                output_path=output,
                container="mkv",
                source="manual",
                created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            )
            records = DuelRecordRepository(history.database_path)
            records.save(
                "duel",
                DuelRecordValues(
                    status="confirmed", result="win", season_id=season.season_id
                ),
                expected_revision=0,
            )
            self.assertEqual(
                history.query(HistoryQuery(season_id=season.season_id))[0].recording_id,
                "duel",
            )
            archived = seasons.delete(season.season_id)
            self.assertTrue(archived.is_archived)
            self.assertEqual(seasons.list(), ())
            self.assertEqual(
                seasons.list(include_archived=True)[0].name, "ランク Season 40"
            )

    def test_overlapping_periods_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = SeasonRepository(Path(tmp) / "history.sqlite3")
            first = repository.add(
                name="ランク",
                season_type="ranked",
                duel_type="ranked",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 31),
            )
            event = repository.add(
                name="イベント",
                season_type="event",
                duel_type="event",
                start_date=date(2026, 8, 10),
                end_date=date(2026, 8, 20),
            )
            self.assertEqual(
                {first.season_id, event.season_id},
                {item.season_id for item in repository.list()},
            )


if __name__ == "__main__":
    unittest.main()
