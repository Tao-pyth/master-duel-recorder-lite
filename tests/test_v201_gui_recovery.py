from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.gui_feature_parity import (
    STANDARD_GUI_FEATURES,
    required_standard_widget_keys,
    satisfied_standard_feature_keys,
)
from master_duel_recorder_lite.runtime_paths import default_runtime_root


class V201GuiRecoveryTest(unittest.TestCase):
    def test_packaged_gui_entrypoint_returns_to_tkinter_gui(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        source = (project_root / "packaging" / "mdrl_gui_entry.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("master_duel_recorder_lite.gui", source)
        self.assertNotIn("master_duel_recorder_lite.pyside_gui import main", source)

    def test_standard_feature_contract_lists_1x_gui_surface(self) -> None:
        feature_keys = {feature.key for feature in STANDARD_GUI_FEATURES}
        widget_keys = set(required_standard_widget_keys())

        self.assertGreaterEqual(len(STANDARD_GUI_FEATURES), 14)
        self.assertIn("recording_control", feature_keys)
        self.assertIn("history_management", feature_keys)
        self.assertIn("data_protection", feature_keys)
        self.assertIn("dialogs", feature_keys)
        self.assertIn("manual_duel_add", widget_keys)
        self.assertIn("history_bulk", widget_keys)
        self.assertIn("youtube_template", widget_keys)
        self.assertIn("data_backup_table", widget_keys)
        self.assertEqual(
            set(satisfied_standard_feature_keys(widget_keys)),
            feature_keys,
        )

    def test_feature_parity_document_lists_every_standard_feature(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        document = (
            project_root / "docs" / "architecture" / "pyside-feature-parity-2.0.1.md"
        ).read_text(encoding="utf-8")

        for feature in STANDARD_GUI_FEATURES:
            self.assertIn(f"`{feature.key}`", document)
            self.assertIn(feature.label, document)

    def test_existing_database_runtime_is_read_after_service_recreation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime_root = Path(tmp_dir) / "user_data"
            service = RecorderApplicationService(user_data_dir=runtime_root)
            service.create_manual_duel_record(
                DuelRecordValues(
                    status="confirmed",
                    result="win",
                    play_order="first",
                    own_deck="復旧確認デッキ",
                    opponent_deck="相手デッキ",
                    duel_type="ranked",
                    tags=("復旧",),
                ),
                occurred_at=datetime(2026, 8, 22, tzinfo=timezone.utc),
            )

            reloaded = RecorderApplicationService(user_data_dir=runtime_root)
            dashboard = reloaded.get_history_dashboard(limit=20)

            self.assertEqual(len(dashboard.views), 1)
            self.assertEqual(dashboard.views[0].own_deck, "復旧確認デッキ")
            self.assertTrue((reloaded.paths.db / "history.sqlite3").is_file())

    def test_frozen_runtime_root_prefers_existing_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            selected = root / "selected-data"
            pointer = root / "runtime-root.json"
            pointer.write_text(
                '{"schema_version": 1, "runtime_root": "' + str(selected).replace("\\", "\\\\") + '"}',
                encoding="utf-8",
            )

            with (
                patch.dict("os.environ", {}, clear=True),
                patch(
                    "master_duel_recorder_lite.data_location.runtime_root_pointer_path",
                    return_value=pointer,
                ),
            ):
                resolved = default_runtime_root(frozen=True, home=root / "home")

            self.assertEqual(resolved, selected.resolve())


if __name__ == "__main__":
    unittest.main()
