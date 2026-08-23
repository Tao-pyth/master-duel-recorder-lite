from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs
from master_duel_recorder_lite.season_report_html import (
    SeasonReportExportError,
    SeasonReportHtmlExporter,
)
from master_duel_recorder_lite.season_reports import SeasonReportService
from master_duel_recorder_lite.seasons import SeasonRepository


class SeasonReportHtmlExporterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name) / "data")
        ensure_runtime_dirs(self.paths)
        seasons = SeasonRepository.from_runtime_paths(self.paths)
        season = seasons.add(
            name="Season <A>",
            season_type="custom",
            duel_type="other",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 3),
        )
        seasons.update_report(
            season.season_id,
            report_notes="既存 & memo",
            report_goal="<script>alert(1)</script>",
            report_highlights="良かった点",
            report_challenges="課題",
            report_next_plan="次期方針",
            expected_revision=0,
        )
        DuelRecordRepository.from_runtime_paths(self.paths).create_manual(
            DuelRecordValues(
                status="confirmed",
                result="win",
                play_order="first",
                own_deck="Deck A",
                season_id=season.season_id,
            ),
            occurred_at=datetime(2026, 8, 2, 12, tzinfo=timezone.utc),
        )
        self.report = SeasonReportService(self.paths).build(
            season.season_id, use_default_comparison=False
        )
        self.output = Path(self.temporary.name) / "output"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_single_html_is_escaped_printable_and_has_no_external_dependency(self) -> None:
        destination = self.output / "season.html"

        result = SeasonReportHtmlExporter().export(self.report, destination)
        source = result.read_text(encoding="utf-8")

        self.assertIn("<!doctype html>", source)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", source)
        self.assertNotIn("<script", source.casefold())
        self.assertNotIn("http://", source.casefold())
        self.assertNotIn("https://", source.casefold())
        self.assertNotIn("file:", source.casefold())
        self.assertNotIn(str(self.paths.root), source)
        self.assertIn("@media print", source)
        self.assertIn("累積勝率", source)
        self.assertIn("全体・コイントス・先後内訳", source)
        self.assertNotIn("最終勝敗", source)
        self.assertEqual(tuple(self.output.iterdir()), (destination,))

    def test_existing_file_requires_explicit_overwrite(self) -> None:
        destination = self.output / "season.html"
        exporter = SeasonReportHtmlExporter()
        exporter.export(self.report, destination)

        with self.assertRaisesRegex(SeasonReportExportError, "既に存在"):
            exporter.export(self.report, destination)
        exporter.export(self.report, destination, overwrite=True)

    def test_invalid_extension_and_publish_failure_leave_no_output(self) -> None:
        with self.assertRaisesRegex(SeasonReportExportError, "html"):
            SeasonReportHtmlExporter().export(self.report, self.output / "season.txt")
        destination = self.output / "season.html"
        with patch(
            "master_duel_recorder_lite.season_report_html.os.replace",
            side_effect=PermissionError("injected"),
        ):
            with self.assertRaisesRegex(SeasonReportExportError, "保存できません"):
                SeasonReportHtmlExporter().export(self.report, destination)

        self.assertFalse(destination.exists())
        self.assertEqual(tuple(self.output.glob("*.tmp")), ())

    def test_windows_reserved_filename_is_rejected(self) -> None:
        with self.assertRaisesRegex(SeasonReportExportError, "Windows"):
            SeasonReportHtmlExporter().export(self.report, self.output / "CON.html")


if __name__ == "__main__":
    unittest.main()
