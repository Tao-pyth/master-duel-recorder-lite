import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.ui_preferences import (
    HISTORY_DEFAULT_VISIBLE_COLUMNS,
    HISTORY_OPTIONAL_COLUMNS,
    UiPreferences,
    load_ui_preferences,
    save_ui_preferences,
)


class UiPreferencesTest(unittest.TestCase):
    def test_defaults_preserve_current_history_columns_and_white_colors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            preferences = load_ui_preferences(Path(tmp_dir))
        self.assertEqual(preferences.history_visible_columns, HISTORY_DEFAULT_VISIBLE_COLUMNS)
        self.assertIn("opponent_deck", HISTORY_OPTIONAL_COLUMNS)
        self.assertNotIn("opponent_deck", preferences.history_visible_columns)
        self.assertTrue(all(color == "#FFFFFF" for color in preferences.history_cell_colors.values()))
        self.assertEqual(preferences.history_double_click_action, "play")

    def test_preferences_round_trip_and_invalid_colors_fall_back_to_white(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            save_ui_preferences(
                root,
                UiPreferences(
                    ("duration", "opponent_deck"),
                    {"result.win": "#12ab34", "result.loss": "red"},
                    False,
                    "edit",
                ),
            )
            loaded = load_ui_preferences(root)
        self.assertEqual(loaded.history_visible_columns, ("duration", "opponent_deck"))
        self.assertEqual(loaded.history_cell_colors["result.win"], "#12AB34")
        self.assertEqual(loaded.history_cell_colors["result.loss"], "#FFFFFF")
        self.assertFalse(loaded.automatic_update_check)
        self.assertEqual(loaded.history_double_click_action, "edit")

    def test_legacy_and_unknown_preferences_fall_back_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "ui-preferences.json").write_text(
                '{"history_visible_columns":["duration"],"history_double_click_action":"delete"}',
                encoding="utf-8",
            )
            loaded = load_ui_preferences(root)
        self.assertEqual(loaded.history_visible_columns, ("duration",))
        self.assertEqual(loaded.history_double_click_action, "play")


if __name__ == "__main__":
    unittest.main()
