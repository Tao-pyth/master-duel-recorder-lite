from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest

from master_duel_recorder_lite.duel_catalog import DuelCatalogRepository
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.duel_statistics import (
    DuelStatisticsRepository,
    StatisticsFilter,
)
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.season_reports import SeasonReportService
from master_duel_recorder_lite.seasons import SeasonConflictError, SeasonRepository


class SeasonReportServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        ensure_runtime_dirs(self.paths)
        self.seasons = SeasonRepository.from_runtime_paths(self.paths)
        self.records = DuelRecordRepository.from_runtime_paths(self.paths)
        self.catalog = DuelCatalogRepository.from_runtime_paths(self.paths)
        self.statistics = DuelStatisticsRepository.from_runtime_paths(self.paths)
        self.previous = self.seasons.add(
            name="ランク前期",
            season_type="ranked",
            duel_type="ranked",
            start_date=date(2026, 7, 25),
            end_date=date(2026, 8, 2),
        )
        self.current = self.seasons.add(
            name="ランク今期",
            season_type="ranked",
            duel_type="ranked",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 5),
        )
        self.catalog.add_deck("青眼", color="#3366AA")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _record(
        self,
        *,
        season_id: int,
        day: int,
        result: str,
        order: str,
        face: str,
        deck: str = "青眼",
    ) -> None:
        self.records.create_manual(
            DuelRecordValues(
                status="confirmed",
                result=result,
                play_order=order,
                coin_face=face,
                own_deck=deck,
                duel_type="ranked",
                season_id=season_id,
            ),
            occurred_at=datetime(2026, 8 if season_id == self.current.season_id else 7, day, 12, tzinfo=timezone.utc),
        )

    def test_report_reuses_statistics_population_and_fills_empty_periods(self) -> None:
        self._record(
            season_id=self.current.season_id,
            day=1,
            result="win",
            order="first",
            face="heads",
        )
        self._record(
            season_id=self.current.season_id,
            day=3,
            result="loss",
            order="second",
            face="tails",
        )
        self._record(
            season_id=self.current.season_id,
            day=3,
            result="draw",
            order="unknown",
            face="unknown",
        )

        report = SeasonReportService(self.paths).build(self.current.season_id)
        dashboard = self.statistics.dashboard(
            StatisticsFilter(
                season_id=self.current.season_id,
                date_from=self.current.start_date,
                date_to=self.current.end_date,
            ),
            granularity="day",
        )

        self.assertEqual(report.summary.filtered, dashboard.filtered)
        self.assertEqual(len(report.daily_trend), 5)
        self.assertEqual([point.metric.matches for point in report.daily_trend], [1, 0, 2, 0, 0])
        self.assertEqual(len(report.daily_deck_usage), 5)
        self.assertEqual(report.daily_deck_usage[2].decks[0].ratio, 1.0)
        self.assertTrue(report.small_sample)

    def test_default_comparison_allows_overlap_and_returns_safe_deltas(self) -> None:
        self._record(
            season_id=self.previous.season_id,
            day=30,
            result="loss",
            order="second",
            face="tails",
        )
        self._record(
            season_id=self.current.season_id,
            day=1,
            result="win",
            order="first",
            face="heads",
        )

        report = SeasonReportService(self.paths).build(self.current.season_id)

        self.assertEqual(report.comparison_season, self.previous)
        self.assertEqual(report.comparison.match_delta, 0)
        self.assertEqual(report.comparison.win_rate_delta, 1.0)

    def test_deck_order_and_unknown_axes_are_explicit(self) -> None:
        self._record(
            season_id=self.current.season_id,
            day=2,
            result="win",
            order="first",
            face="unknown",
        )

        report = SeasonReportService(self.paths).build(
            self.current.season_id, use_default_comparison=False
        )

        deck_rows = [item for item in report.deck_orders if item.deck_name == "青眼"]
        self.assertEqual([item.play_order for item in deck_rows], ["overall", "first", "second", "unknown"])
        self.assertEqual([item.metric.matches for item in deck_rows], [1, 1, 0, 0])
        self.assertTrue(all(item.color == "#3366AA" for item in deck_rows))
        unknown_axes = {(item.axis, item.key): item.metric.matches for item in report.axes}
        self.assertEqual(unknown_axes[("coin_face", "unknown")], 1)

    def test_report_sections_use_revision_and_archive_is_explicit(self) -> None:
        saved = self.seasons.update_report(
            self.current.season_id,
            report_notes="従来メモ",
            report_goal="目標",
            report_highlights="良かった点",
            report_challenges="課題",
            report_next_plan="次期方針",
            expected_revision=0,
        )

        self.assertEqual(saved.report_revision, 1)
        self.assertEqual(saved.report_notes, "従来メモ")
        with self.assertRaises(SeasonConflictError):
            self.seasons.update_report(
                self.current.season_id,
                report_notes="競合",
                report_goal="",
                report_highlights="",
                report_challenges="",
                report_next_plan="",
                expected_revision=0,
            )
        archived = self.seasons.archive(self.current.season_id)
        self.assertTrue(archived.is_archived)


if __name__ == "__main__":
    unittest.main()
