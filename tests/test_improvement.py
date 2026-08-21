import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from master_duel_recorder_lite.duel_records import DuelRecord, DuelRecordValues
from master_duel_recorder_lite.history_database import CURRENT_SCHEMA_VERSION
from master_duel_recorder_lite.improvement import (
    ImprovementRepository,
    deck_improvement_rows,
    export_migration_pack,
    suggest_duel_inputs,
)
from master_duel_recorder_lite.runtime_paths import default_runtime_paths, ensure_runtime_dirs


def _record(
    duel_id: str,
    *,
    own: str,
    opponent: str,
    result: str = "win",
    order: str = "first",
    coin: str = "heads",
    tags: tuple[str, ...] = (),
) -> DuelRecord:
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    return DuelRecord(
        duel_id=duel_id,
        recording_id=duel_id,
        entry_origin="user",
        occurred_at=now,
        values=DuelRecordValues(
            status="confirmed",
            result=result,
            play_order=order,
            coin_face=coin,
            own_deck=own,
            opponent_deck=opponent,
            tags=tags,
        ),
        revision=1,
        created_at=now,
        updated_at=now,
    )


class ImprovementTest(unittest.TestCase):
    def test_repository_creates_templates_and_goals_on_schema_v16(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            repository = ImprovementRepository.from_runtime_paths(paths)

            template = repository.create_tag_template(
                name="ランク戦", tags=("rank", "BO1")
            )
            goal = repository.create_goal(
                title="後攻勝率", metric="second_win_rate", target_value=0.55
            )

            with closing(sqlite3.connect(paths.db / "history.sqlite3")) as connection:
                version = connection.execute(
                    "SELECT version FROM schema_version WHERE singleton = 1"
                ).fetchone()[0]
            templates = repository.list_tag_templates()
            goals = repository.list_goals()

        self.assertGreaterEqual(CURRENT_SCHEMA_VERSION, 16)
        self.assertEqual(version, 16)
        self.assertEqual(templates[0].template_id, template.template_id)
        self.assertEqual(goals[0].goal_id, goal.goal_id)

    def test_suggestions_and_deck_view_use_recent_records(self) -> None:
        records = (
            _record("1", own="A", opponent="X", tags=("重要",)),
            _record("2", own="A", opponent="X", result="loss", order="second"),
            _record("3", own="B", opponent="Y", result="loss", coin="tails"),
        )

        suggestions = suggest_duel_inputs(records)
        rows = deck_improvement_rows(records)

        self.assertEqual(suggestions[0].value, "A")
        self.assertEqual(rows[0].opponent_deck, "Y")
        self.assertEqual(rows[0].win_rate, 0.0)

    def test_migration_pack_excludes_recordings_and_oauth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            paths = default_runtime_paths(user_data_dir=Path(tmp_dir) / "user_data")
            ensure_runtime_dirs(paths)
            (paths.config / "app.toml").write_text("[runtime]\n", encoding="utf-8")
            ImprovementRepository.from_runtime_paths(paths)
            package = export_migration_pack(paths, Path(tmp_dir) / "pack.mdrl-migration")

            with zipfile.ZipFile(package) as archive:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                names = set(archive.namelist())

        self.assertFalse(manifest["contains_recordings"])
        self.assertFalse(manifest["contains_oauth_credentials"])
        self.assertIn("config/app.toml", names)
        self.assertNotIn("recordings", "".join(names))


if __name__ == "__main__":
    unittest.main()
