from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from master_duel_recorder_lite.duel_catalog import DuelCatalogRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.duel_statistics import (
    DuelStatisticsRepository,
    StatisticsFilter,
    statistics_local_date,
)
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository
from master_duel_recorder_lite.seasons import SeasonRepository


class DuelStatisticsRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.recordings = root / "recordings"
        self.recordings.mkdir()
        self.database = root / "history.sqlite3"
        self.history = RecordingHistoryRepository(
            database_path=self.database,
            recordings_root=self.recordings,
        )
        self.records = DuelRecordRepository(self.database)
        self.catalog = DuelCatalogRepository(self.database)
        self.statistics = DuelStatisticsRepository(self.database)
        self.seasons = SeasonRepository(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_local_date_conversion_handles_timezone_day_boundary(self) -> None:
        instant = datetime(2026, 7, 31, 15, 30, tzinfo=timezone.utc)

        self.assertEqual(
            statistics_local_date(instant, timezone(timedelta(hours=9))),
            date(2026, 8, 1),
        )

    def _record(
        self,
        recording_id: str,
        *,
        occurred_at: datetime,
        result: str,
        play_order: str,
        deck: str,
        opponent_deck: str = "",
        tags: tuple[str, ...] = (),
        status: str = "confirmed",
        state: str = "completed",
        season_id: int | None = None,
        coin_face: str = "unknown",
        duel_type: str = "other",
    ) -> None:
        self.history.register_starting(
            recording_id=recording_id,
            output_path=self.recordings / f"{recording_id}.mkv",
            container="mkv",
            source="manual",
            created_at=occurred_at,
        )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "UPDATE recordings SET state = ?, started_at = ? WHERE recording_id = ?",
                (state, occurred_at.isoformat(), recording_id),
            )
        self.records.save(
            recording_id,
            DuelRecordValues(
                status=status,
                result=result,
                play_order=play_order,
                coin_face=coin_face,
                own_deck=deck,
                opponent_deck=opponent_deck,
                tags=tags,
                season_id=season_id,
                duel_type=duel_type,
            ),
            expected_revision=0,
        )
        self.catalog.remember_record_values(
            DuelRecordValues(own_deck=deck, opponent_deck=opponent_deck, tags=tags)
        )

    def test_overall_excludes_drafts_unknown_results_and_failed_recordings(self) -> None:
        base = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
        self._record("win", occurred_at=base, result="win", play_order="first", deck="青眼")
        self._record("loss", occurred_at=base, result="loss", play_order="second", deck="青眼")
        self._record("draw", occurred_at=base, result="draw", play_order="first", deck="烙印")
        self._record(
            "draft", occurred_at=base, result="win", play_order="first", deck="青眼", status="draft"
        )
        self._record(
            "unknown", occurred_at=base, result="unknown", play_order="first", deck="青眼"
        )
        self._record(
            "failed", occurred_at=base, result="win", play_order="first", deck="青眼", state="failed"
        )

        dashboard = self.statistics.dashboard()

        self.assertEqual(dashboard.overall.matches, 3)
        self.assertEqual(dashboard.overall.wins, 1)
        self.assertEqual(dashboard.overall.losses, 1)
        self.assertEqual([(row.key, row.metric.matches) for row in dashboard.by_duel_type], [("other", 3)])
        self.assertEqual(dashboard.overall.draws, 1)
        self.assertAlmostEqual(dashboard.overall.win_rate or 0, 1 / 3)

    def test_manual_record_is_included_without_recording_row(self) -> None:
        occurred_at = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
        record = self.records.create_manual(
            DuelRecordValues(
                status="confirmed",
                result="win",
                play_order="first",
                own_deck="手入力デッキ",
            ),
            occurred_at=occurred_at,
        )

        dashboard = self.statistics.dashboard()

        self.assertIsNone(record.recording_id)
        self.assertEqual(dashboard.overall.matches, 1)
        self.assertEqual(dashboard.overall.wins, 1)
        self.assertEqual(dashboard.trend[0].period_start, date(2026, 8, 1))

    def test_season_breakdown_includes_named_and_unassigned_records(self) -> None:
        season = self.seasons.add(
            name="SEASON 56",
            season_type="ranked",
            duel_type="ranked",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
        )
        self._record(
            "season-win",
            occurred_at=datetime(2026, 8, 3, 12, tzinfo=timezone.utc),
            result="win",
            play_order="first",
            deck="青眼",
            season_id=season.season_id,
        )
        self._record(
            "unassigned-loss",
            occurred_at=datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
            result="loss",
            play_order="second",
            deck="青眼",
        )

        dashboard = self.statistics.dashboard()

        self.assertEqual(
            [(item.label, item.metric.matches, item.metric.wins) for item in dashboard.by_season],
            [("SEASON 56", 1, 1), ("シーズン未設定", 1, 0)],
        )

    def test_season_breakdown_uses_start_date_then_id_with_unassigned_last(self) -> None:
        seasons = [self.seasons.add(
            name=name, season_type="event", duel_type="event",
            start_date=start, end_date=date(2026, 12, 31),
        ) for name, start in (
            ("Z newest", date(2026, 9, 1)),
            ("A oldest", date(2026, 7, 1)),
            ("B newest tie", date(2026, 9, 1)),
            ("C middle", date(2026, 8, 1)),
        )]
        for index, season in enumerate(seasons):
            self._record(str(index), occurred_at=datetime(2026, 9, index + 1, 12, tzinfo=timezone.utc),
                         result="win", play_order="first", deck="青眼", season_id=season.season_id)
        self.seasons.archive(seasons[0].season_id)
        self._record("none", occurred_at=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
                     result="loss", play_order="second", deck="青眼")
        dashboard = self.statistics.dashboard()
        self.assertEqual([row.label for row in dashboard.by_season],
                         ["B newest tie", "Z newest", "C middle", "A oldest", "シーズン未設定"])
        self.assertEqual([row.metric.matches for row in dashboard.by_season], [1] * 5)
        filtered = self.statistics.dashboard(StatisticsFilter(date_from=date(2026, 9, 3)))
        self.assertEqual([row.label for row in filtered.by_season],
                         ["B newest tie", "C middle", "シーズン未設定"])

    def test_duel_type_breakdown_filter_and_eligible_results(self) -> None:
        types = ("ranked", "event", "room", "solo", "other")
        for kind in types:
            for day, result in enumerate(("win", "loss", "draw"), 1):
                self._record(f"{kind}-{day}", occurred_at=datetime(2026, 9, day, 12, tzinfo=timezone.utc),
                             result=result, play_order="first", deck="青眼", duel_type=kind)
        for identifier, status, state in (("draft", "draft", "completed"), ("failed", "confirmed", "failed")):
            self._record(identifier, occurred_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
                         result="win", play_order="first", deck="青眼", duel_type="ranked", status=status, state=state)
        dashboard = self.statistics.dashboard()
        self.assertEqual([row.key for row in dashboard.by_duel_type], list(types))
        self.assertEqual([row.label for row in dashboard.by_duel_type],
                         ["ランク戦", "イベント", "ルーム戦", "ソロモード", "その他"])
        self.assertEqual(dashboard.overall.matches, 15)
        for row in dashboard.by_duel_type:
            self.assertEqual((row.metric.matches, row.metric.wins, row.metric.losses, row.metric.draws), (3, 1, 1, 1))
            self.assertAlmostEqual(row.metric.win_rate, 1 / 3)
        for kind in types:
            filtered = self.statistics.dashboard(StatisticsFilter(duel_type=kind, date_to=date(2026, 9, 2)))
            self.assertEqual(filtered.overall.matches, 15)
            self.assertEqual((filtered.filtered.matches, filtered.filtered.win_rate), (2, 0.5))
            self.assertEqual([row.key for row in filtered.by_duel_type], [kind])
            self.assertEqual(sum(row.metric.matches for row in filtered.trend), 2)
        loss = self.statistics.dashboard(StatisticsFilter(duel_type="event", result="loss"))
        self.assertEqual((loss.filtered.matches, loss.filtered.losses), (1, 1))
        with self.assertRaises(ValueError):
            StatisticsFilter(duel_type="invalid")

    def test_imported_record_is_included_without_recording_row(self) -> None:
        record = self.records.create_manual(
            DuelRecordValues(
                status="confirmed",
                result="loss",
                play_order="second",
                own_deck="取込デッキ",
            ),
            occurred_at=datetime(2026, 8, 3, 12, tzinfo=timezone.utc),
        )
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "UPDATE duel_records SET entry_origin = 'import' WHERE duel_id = ?",
                (record.duel_id,),
            )

        dashboard = self.statistics.dashboard()

        self.assertEqual(dashboard.overall.matches, 1)
        self.assertEqual(dashboard.overall.losses, 1)
        self.assertEqual([(row.key, row.metric.matches) for row in dashboard.by_duel_type], [("other", 1)])

    def test_combines_date_deck_tag_and_play_order_filters(self) -> None:
        self._record(
            "target",
            occurred_at=datetime(2026, 8, 5, 12, tzinfo=timezone.utc),
            result="win",
            play_order="first",
            deck="青眼",
            tags=("大会",),
        )
        self._record(
            "wrong-order",
            occurred_at=datetime(2026, 8, 5, 13, tzinfo=timezone.utc),
            result="loss",
            play_order="second",
            deck="青眼",
            tags=("大会",),
        )
        self._record(
            "wrong-date",
            occurred_at=datetime(2026, 7, 31, 12, tzinfo=timezone.utc),
            result="loss",
            play_order="first",
            deck="青眼",
            tags=("大会",),
        )
        tag = next(entry for entry in self.catalog.list_tags() if entry.name == "大会")

        dashboard = self.statistics.dashboard(
            StatisticsFilter(
                date_from=date(2026, 8, 1),
                date_to=date(2026, 8, 31),
                own_deck="青眼",
                tag_entry_id=tag.entry_id,
                play_order="first",
            ),
            granularity="day",
        )

        self.assertEqual(dashboard.overall.matches, 3)
        self.assertEqual(dashboard.filtered.matches, 1)
        self.assertEqual(dashboard.filtered.wins, 1)
        self.assertEqual(dashboard.filtered.win_rate, 1.0)

    def test_breakdowns_and_monthly_trend_include_empty_periods(self) -> None:
        self._record(
            "jan-win",
            occurred_at=datetime(2026, 1, 10, 12, tzinfo=timezone.utc),
            result="win",
            play_order="first",
            deck="青眼",
        )
        self._record(
            "mar-loss",
            occurred_at=datetime(2026, 3, 10, 12, tzinfo=timezone.utc),
            result="loss",
            play_order="second",
            deck="烙印",
        )

        dashboard = self.statistics.dashboard(
            StatisticsFilter(date_from=date(2026, 1, 1), date_to=date(2026, 3, 31)),
            granularity="month",
        )

        self.assertEqual([item.label for item in dashboard.by_deck], ["烙印", "青眼"])
        self.assertEqual([item.label for item in dashboard.by_play_order], ["先攻", "後攻"])
        self.assertEqual([point.label for point in dashboard.trend], ["2026/01", "2026/02", "2026/03"])
        self.assertEqual([point.metric.matches for point in dashboard.trend], [1, 0, 1])
        self.assertEqual([point.metric.wins for point in dashboard.trend], [1, 0, 0])
        self.assertEqual(
            [point.cumulative_metric.matches for point in dashboard.trend if point.cumulative_metric],
            [1, 1, 2],
        )
        self.assertEqual(
            [point.cumulative_win_rate for point in dashboard.trend],
            [1.0, 1.0, 0.5],
        )

    def test_hidden_own_deck_is_excluded_but_hidden_opponent_is_not(self) -> None:
        base = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
        self._record(
            "hidden-own",
            occurred_at=base,
            result="win",
            play_order="first",
            deck="非表示デッキ",
        )
        self._record(
            "hidden-opponent",
            occurred_at=base,
            result="loss",
            play_order="second",
            deck="青眼",
            opponent_deck="非表示デッキ",
        )
        hidden = next(
            item for item in self.catalog.list_decks() if item.name == "非表示デッキ"
        )
        self.catalog.update_deck(
            hidden.entry_id,
            name=hidden.name,
            description=hidden.description,
            color=hidden.color or "#4F6F8F",
            opponent_only=False,
            hidden_from_history_statistics=True,
        )

        dashboard = self.statistics.dashboard()

        self.assertEqual(dashboard.overall.matches, 1)
        self.assertEqual(dashboard.overall.losses, 1)
        self.assertEqual(
            [item.label for item in dashboard.by_deck_play_order], ["青眼 後攻時"]
        )

    def test_coin_face_filter_and_breakdown(self) -> None:
        base = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
        self._record(
            "heads-loss-second", occurred_at=base, result="win", play_order="second",
            deck="青眼", coin_face="heads",
        )
        self._record(
            "tails-win-first", occurred_at=base, result="loss", play_order="first",
            deck="青眼", coin_face="tails",
        )

        dashboard = self.statistics.dashboard(StatisticsFilter(coin_face="heads"))

        self.assertEqual(dashboard.filtered.matches, 1)
        self.assertEqual(dashboard.filtered.wins, 1)
        self.assertEqual([item.label for item in dashboard.by_coin_face], ["表"])

    def test_filter_validation_rejects_invalid_ranges_and_choices(self) -> None:
        with self.assertRaises(ValueError):
            StatisticsFilter(date_from=date(2026, 8, 2), date_to=date(2026, 8, 1))
        with self.assertRaises(ValueError):
            StatisticsFilter(tag_entry_id=0)
        with self.assertRaises(ValueError):
            StatisticsFilter(play_order="unknown")
        with self.assertRaises(ValueError):
            StatisticsFilter(coin_face="edge")
        with self.assertRaises(ValueError):
            self.statistics.dashboard(granularity="year")


if __name__ == "__main__":
    unittest.main()
