from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest

from master_duel_recorder_lite import __version__
from master_duel_recorder_lite.pyside_gui import (
    NAVIGATION_PAGES,
    SETTINGS_PARITY_WIDGETS,
    SMOKE_WIDGETS,
    UI_USABILITY_WIDGETS,
    build_gui_parser,
    history_table_display_row,
    smoke_contract,
)
from master_duel_recorder_lite.pyside_review import (
    REVIEW_WIDGETS,
    review_timeline_display_row,
)


class PySideGuiContractTest(unittest.TestCase):
    def test_navigation_keeps_major_pages_without_prepare_or_improve(self) -> None:
        pages = tuple(page for page, _label in NAVIGATION_PAGES)

        self.assertEqual(
            pages,
            (
                "record",
                "history",
                "statistics",
                "decks",
                "tags",
                "seasons",
                "youtube",
                "reliability",
                "settings",
            ),
        )
        self.assertNotIn("prepare", pages)
        self.assertNotIn("improve", pages)

    def test_smoke_contract_matches_release_script_widgets(self) -> None:
        service = SimpleNamespace(paths=SimpleNamespace(root=Path("user_data")))

        contract = smoke_contract(service=service, width=1180, height=760)

        self.assertEqual(contract["version"], __version__)
        self.assertTrue(contract["pyside6"])
        self.assertEqual(contract["gui_entrypoint"], "master_duel_recorder_lite.pyside_gui")
        self.assertTrue(contract["standard_feature_contract"])
        self.assertTrue(contract["standard_operation_contract"])
        self.assertTrue(contract["ui_usability_contract"])
        self.assertTrue(contract["calendar_picker_contract"]["popup_calendar"])
        self.assertIn(
            "history_date_from_picker",
            contract["calendar_picker_contract"]["date_widgets"],
        )
        self.assertIn(
            "history_date_to_picker",
            contract["calendar_picker_contract"]["date_widgets"],
        )
        self.assertEqual(
            contract["history_hub_operation_contract"]["selection_required_buttons"],
            ["history_play", "history_duel", "history_delete", "history_youtube"],
        )
        self.assertEqual(
            contract["history_hub_operation_contract"]["danger_button"],
            "history_delete",
        )
        self.assertIn(
            "win",
            contract["history_hub_operation_contract"]["internal_values_not_displayed"],
        )
        self.assertEqual(
            contract["statistics_chart_contract"]["visual_type"],
            "bar_and_line",
        )
        self.assertTrue(contract["table_readability_contract"]["horizontal_scroll"])
        self.assertTrue(contract["color_swatch_contract"]["history_deck_decoration"])
        self.assertTrue(contract["color_swatch_contract"]["catalog_color_codes_hidden"])
        self.assertEqual(
            contract["control_height_contract"],
            {
                "button_min_height": 36,
                "input_min_height": 36,
                "combo_min_height": 36,
                "date_picker_min_height": 36,
            },
        )
        self.assertEqual(
            contract["active_season_contract"]["status_widget"],
            "active_season_status",
        )
        self.assertTrue(contract["health_status_contract"]["fixed_warning_removed"])
        self.assertIn("deck_save", contract["catalog_edit_contract"]["deck_widgets"])
        self.assertIn("tag_save", contract["catalog_edit_contract"]["tag_widgets"])
        self.assertIn("season_save", contract["season_edit_contract"]["widgets"])
        self.assertTrue(contract["season_edit_contract"]["date_picker"])
        self.assertTrue(contract["template_screen_contract"]["connection_buttons_removed"])
        self.assertEqual(
            contract["template_screen_contract"]["connection_management_page"],
            "settings",
        )
        self.assertIn(
            "reliability_refresh",
            contract["reliability_action_contract"]["buttons"],
        )
        self.assertTrue(contract["background_operation_contract"]["youtube_upload_worker"])
        self.assertEqual(
            contract["background_operation_contract"]["progress_widget"],
            "youtube_upload_progress",
        )
        self.assertTrue(contract["background_operation_contract"]["double_submit_guard"])
        self.assertTrue(contract["settings_parity_contract"])
        self.assertEqual(
            contract["settings_parity_widgets"],
            list(SETTINGS_PARITY_WIDGETS),
        )
        self.assertTrue(
            contract["app_update_state_contract"]["download_enabled_only_after_candidate"]
        )
        self.assertTrue(
            contract["app_update_state_contract"]["latest_without_candidate_disables_download"]
        )
        self.assertEqual(contract["missing_standard_widgets"], [])
        self.assertEqual(contract["failed_standard_operation_checks"], [])
        self.assertTrue(contract["youtube_flow_contract"])
        self.assertEqual(contract["review_video_contract"]["entry_button"], "history_play")
        self.assertEqual(
            contract["review_video_contract"]["supported_extensions"],
            [".mp4", ".mkv"],
        )
        self.assertEqual(contract["review_video_contract"]["fallback"], "external_player")
        self.assertEqual(
            contract["review_video_contract"]["timeline_columns"],
            ["経過", "種別", "状態", "ラベル", "由来"],
        )
        for widget in REVIEW_WIDGETS:
            self.assertIn(widget, contract["review_video_contract"]["widgets"])
        self.assertNotIn("youtube_connect", contract["widgets"])
        self.assertNotIn("youtube_disconnect", contract["widgets"])
        self.assertNotIn("youtube_refresh", contract["widgets"])
        self.assertNotIn("youtube_test_upload", contract["widgets"])
        self.assertEqual(contract["runtime_data"], "user_data")
        for widget in (*SMOKE_WIDGETS, *UI_USABILITY_WIDGETS):
            self.assertIn(widget, contract["widgets"])

    def test_parser_keeps_existing_gui_smoke_arguments(self) -> None:
        args = build_gui_parser().parse_args(
            [
                "--smoke-test",
                "--smoke-output",
                "build/smoke.json",
                "--smoke-screenshot",
                "build/smoke.png",
            ]
        )

        self.assertTrue(args.smoke_test)
        self.assertEqual(args.smoke_output, Path("build/smoke.json"))
        self.assertEqual(args.smoke_screenshot, Path("build/smoke.png"))

    def test_history_table_display_row_uses_japanese_labels(self) -> None:
        view = SimpleNamespace(
            occurred_at=datetime(2026, 8, 23, 12, 0),
            own_deck="白き森調和",
            result="win",
            play_order="first",
            coin_face="heads",
            duel_type="ranked",
            opponent_deck="神碑",
            entry_origin="recording",
        )

        row = history_table_display_row(view)

        self.assertEqual(row[2:6], ("勝ち", "先攻", "表", "ランク戦"))
        self.assertEqual(row[9], "録画")
        self.assertNotIn("win", row)
        self.assertNotIn("first", row)
        self.assertNotIn("heads", row)
        self.assertNotIn("ranked", row)

    def test_review_timeline_display_row_keeps_timeline_support_columns(self) -> None:
        event = SimpleNamespace(
            elapsed_label="01:23.456",
            event_type="marker",
            status="confirmed",
            label="レビューで追加",
            source="manual",
        )

        row = review_timeline_display_row(event)

        self.assertEqual(
            row,
            ("01:23.456", "marker", "confirmed", "レビューで追加", "manual"),
        )


if __name__ == "__main__":
    unittest.main()
