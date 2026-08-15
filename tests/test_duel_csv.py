from __future__ import annotations

import csv
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from master_duel_recorder_lite.duel_csv import (
    CSV_HEADERS,
    DuelCsvError,
    DuelCsvService,
)
from master_duel_recorder_lite.duel_records import (
    DuelRecordRepository,
    DuelRecordValues,
)
from master_duel_recorder_lite.runtime_paths import (
    default_runtime_paths,
    ensure_runtime_dirs,
)


class DuelCsvServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.paths = default_runtime_paths(user_data_dir=Path(self.temporary.name))
        ensure_runtime_dirs(self.paths)
        self.records = DuelRecordRepository.from_runtime_paths(self.paths)
        self.service = DuelCsvService(self.paths)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, name: str, rows: list[list[str]]) -> Path:
        path = self.paths.exports / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(CSV_HEADERS)
            writer.writerows(rows)
        return path

    def test_import_creates_record_and_missing_masters(self) -> None:
        source = self._write(
            "new.csv",
            [[
                "",
                "2026-08-16T12:34:56+09:00",
                "自分デッキ",
                "相手デッキ",
                "勝ち",
                "先攻",
                "表",
                "ランク戦",
                "ランク 2026-08",
                "大会,確認",
                "メモ",
            ]],
        )

        preview = self.service.preview(source)
        self.assertTrue(preview.valid)
        self.assertEqual(preview.create_count, 1)
        self.assertEqual(preview.new_decks, ("自分デッキ", "相手デッキ"))
        result = self.service.apply(preview)

        record = self.records.get(result.created_ids[0])
        assert record is not None
        self.assertEqual(record.entry_origin, "import")
        self.assertEqual(record.values.result, "win")
        self.assertEqual(record.values.tags, ("大会", "確認"))
        self.assertTrue(result.backup_path.is_file())

    def test_existing_id_updates_values_and_preserves_origin(self) -> None:
        existing = self.records.create_manual(
            DuelRecordValues(status="confirmed", result="loss", notes="before"),
            occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        source = self._write(
            "update.csv",
            [[
                existing.duel_id,
                "2026-08-17T01:02:03+09:00",
                "更新デッキ",
                "",
                "勝ち",
                "後攻",
                "裏",
                "イベント",
                "",
                "",
                "after",
            ]],
        )

        result = self.service.apply(self.service.preview(source))
        updated = self.records.get(existing.duel_id)

        assert updated is not None
        self.assertEqual(result.updated_ids, (existing.duel_id,))
        self.assertEqual(updated.entry_origin, "manual")
        self.assertEqual(updated.values.notes, "after")
        self.assertEqual(updated.values.play_order, "second")
        self.assertEqual(updated.revision, existing.revision + 1)

    def test_unknown_id_is_reassigned(self) -> None:
        source = self._write(
            "unknown.csv",
            [[
                "unknown-id",
                "2026-08-16T12:00:00+09:00",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]],
        )

        preview = self.service.preview(source)
        result = self.service.apply(preview)

        self.assertEqual(preview.reassigned_id_count, 1)
        self.assertEqual(result.reassigned_ids[0][1], "unknown-id")
        self.assertNotEqual(result.created_ids[0], "unknown-id")

    def test_export_round_trip_preserves_csv_sensitive_text(self) -> None:
        original = self.records.create_manual(
            DuelRecordValues(
                status="confirmed",
                own_deck="=危険な式",
                tags=("大会",),
                notes='1行目, "引用"\n2行目',
            ),
            occurred_at=datetime(2026, 8, 16, tzinfo=timezone.utc),
        )
        destination = self.paths.exports / "roundtrip.csv"

        self.service.export(destination)
        raw = destination.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertIn(b"\r\n", raw)
        preview = self.service.preview(destination)

        self.assertTrue(preview.valid)
        self.assertEqual(preview.rows[0].requested_id, original.duel_id)
        self.assertEqual(preview.rows[0].own_deck, "=危険な式")
        self.assertEqual(preview.rows[0].notes, '1行目, "引用"\n2行目')

    def test_invalid_row_does_not_change_database(self) -> None:
        source = self._write(
            "invalid.csv",
            [["", "not-a-date", "", "", "不正", "", "", "", "", "", ""]],
        )

        preview = self.service.preview(source)

        self.assertFalse(preview.valid)
        with self.assertRaises(DuelCsvError):
            self.service.apply(preview)
        self.assertEqual(self.records.list(), ())

    def test_duplicate_id_is_rejected_before_apply(self) -> None:
        row = [
            "same",
            "2026-08-16T12:00:00+09:00",
            "",
            "",
            "勝ち",
            "先攻",
            "表",
            "その他",
            "",
            "",
            "",
        ]
        preview = self.service.preview(self._write("duplicate.csv", [row, row]))

        self.assertFalse(preview.valid)
        self.assertEqual({issue.column for issue in preview.issues}, {"ID"})

    def test_database_failure_rolls_back_records_and_masters(self) -> None:
        source = self._write(
            "rollback.csv",
            [
                [
                    "",
                    "2026-08-16T12:00:00+09:00",
                    "先に追加されるデッキ",
                    "",
                    "勝ち",
                    "先攻",
                    "表",
                    "その他",
                    "",
                    "新規タグ",
                    "",
                ],
                [
                    "",
                    "2026-08-16T13:00:00+09:00",
                    "fail",
                    "",
                    "負け",
                    "後攻",
                    "裏",
                    "その他",
                    "",
                    "",
                    "",
                ],
            ],
        )
        with closing(sqlite3.connect(self.service.database_path)) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_csv_test BEFORE INSERT ON duel_records
                WHEN NEW.own_deck = 'fail'
                BEGIN SELECT RAISE(ABORT, 'injected failure'); END
                """
            )
            connection.commit()

        with self.assertRaises(DuelCsvError):
            self.service.apply(self.service.preview(source))

        self.assertEqual(self.records.list(), ())
        with closing(sqlite3.connect(self.service.database_path)) as connection:
            catalog_count = connection.execute(
                "SELECT COUNT(*) FROM duel_catalog_entries"
            ).fetchone()[0]
        self.assertEqual(catalog_count, 0)

    def test_sample_matches_import_contract(self) -> None:
        sample = self.service.export_sample(self.paths.exports / "sample.csv")

        preview = self.service.preview(sample)

        self.assertTrue(preview.valid)
        self.assertEqual(len(preview.rows), 1)


if __name__ == "__main__":
    unittest.main()
