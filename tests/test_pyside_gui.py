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
    smoke_contract,
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
        self.assertEqual(
            contract["statistics_chart_contract"]["visual_type"],
            "bar_and_line",
        )
        self.assertTrue(contract["table_readability_contract"]["horizontal_scroll"])
        self.assertTrue(contract["color_swatch_contract"]["history_deck_decoration"])
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


if __name__ == "__main__":
    unittest.main()
