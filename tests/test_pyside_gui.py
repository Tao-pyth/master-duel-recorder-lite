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
    history_color_target_label,
    history_table_display_row,
    pyside_record_ui_state,
    season_table_display_row,
    season_type_label,
    smoke_contract,
)
from master_duel_recorder_lite.operation_state import OperationAction
from master_duel_recorder_lite.pyside_review import (
    REVIEW_WIDGETS,
    compose_marker_label,
    review_clip_range_message,
    review_operation_error_message,
    review_timeline_display_row,
    split_marker_label,
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
                "settings",
            ),
        )
        self.assertNotIn("prepare", pages)
        self.assertNotIn("improve", pages)
        self.assertNotIn("reliability", pages)

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
            [
                "history_bulk",
                "history_play",
                "history_duel",
                "history_delete",
                "history_youtube",
            ],
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
        self.assertTrue(contract["statistics_chart_contract"]["separate_label_regions"])
        self.assertTrue(contract["table_readability_contract"]["horizontal_scroll"])
        self.assertTrue(contract["table_readability_contract"]["stable_catalog_table_height"])
        self.assertEqual(contract["table_readability_contract"]["fixed_row_height"], 38)
        self.assertTrue(
            contract["table_readability_contract"]["selection_does_not_resize_rows"]
        )
        self.assertTrue(contract["color_swatch_contract"]["history_deck_decoration"])
        self.assertTrue(contract["color_swatch_contract"]["catalog_color_codes_hidden"])
        self.assertTrue(contract["color_swatch_contract"]["color_text_hidden"])
        self.assertEqual(
            contract["color_swatch_contract"]["settings_table"],
            "settings_display_color_table",
        )
        self.assertEqual(contract["color_swatch_contract"]["settings_change_column"], "変更")
        self.assertEqual(
            contract["color_swatch_contract"]["settings_change_source"],
            "QColorDialog",
        )
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
        self.assertEqual(
            contract["recording_control_state_contract"]["status_widget"],
            "record_status_band",
        )
        self.assertEqual(
            contract["recording_control_state_contract"]["poll_interval_ms"],
            500,
        )
        self.assertTrue(
            contract["recording_control_state_contract"][
                "manual_recording_disables_start"
            ]
        )
        self.assertTrue(
            contract["recording_control_state_contract"][
                "stop_button_routes_active_operation"
            ]
        )
        self.assertTrue(
            contract["recording_control_state_contract"]["watch_starting_allows_stop"]
        )
        self.assertTrue(contract["health_status_contract"]["fixed_warning_removed"])
        self.assertEqual(contract["health_status_contract"]["ready_text"], "準備OK")
        self.assertIn("deck_save", contract["catalog_edit_contract"]["deck_widgets"])
        self.assertIn("tag_save", contract["catalog_edit_contract"]["tag_widgets"])
        self.assertIn("season_save", contract["season_edit_contract"]["widgets"])
        self.assertIn("season_type_select", contract["season_edit_contract"]["widgets"])
        self.assertTrue(contract["season_edit_contract"]["date_picker"])
        self.assertEqual(
            contract["season_edit_contract"]["layout"],
            "name_row_then_equal_type_start_end_row",
        )
        self.assertEqual(contract["season_edit_contract"]["table_type_labels"], "japanese")
        self.assertTrue(contract["template_screen_contract"]["connection_buttons_removed"])
        self.assertTrue(contract["template_screen_contract"]["mp4_preparation_hidden"])
        self.assertTrue(contract["template_screen_contract"]["background_status_hidden"])
        self.assertEqual(
            contract["template_screen_contract"]["connection_management_page"],
            "settings",
        )
        self.assertIn(
            "settings_reliability_refresh",
            contract["reliability_action_contract"]["buttons"],
        )
        self.assertTrue(contract["reliability_action_contract"]["navigation_removed"])
        self.assertEqual(
            contract["reliability_action_contract"]["settings_tab"],
            "録画設定②",
        )
        self.assertEqual(
            contract["reliability_action_contract"]["recording_tabs"],
            ["録画設定①", "録画設定②"],
        )
        self.assertEqual(
            contract["reliability_action_contract"]["record_page_entry"],
            "record_reliability_check",
        )
        self.assertTrue(contract["background_operation_contract"]["youtube_upload_worker"])
        self.assertIsNone(contract["background_operation_contract"]["progress_widget"])
        self.assertTrue(contract["background_operation_contract"]["template_progress_hidden"])
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
        self.assertIn(
            "history_saved_filter",
            contract["history_hub_operation_contract"]["filter_widgets"],
        )
        self.assertIn(
            "tag_entry_ids",
            contract["history_hub_operation_contract"]["query_filters"],
        )
        self.assertEqual(contract["review_video_contract"]["entry_button"], "history_play")
        self.assertEqual(contract["review_video_contract"]["duel_entry_button"], "history_duel")
        self.assertEqual(
            contract["review_video_contract"]["history_duel_initial_tab"],
            "戦績入力",
        )
        self.assertEqual(
            contract["review_video_contract"]["duel_save_parent_refresh"],
            {
                "callback_argument": "on_duel_saved",
                "connected_from": ["history_play", "history_duel"],
                "refresh_source": "_refresh_history",
                "success_only": True,
                "preserves_history_filters": True,
            },
        )
        self.assertEqual(
            contract["review_video_contract"]["supported_extensions"],
            [".mp4", ".mkv"],
        )
        self.assertEqual(contract["review_video_contract"]["fallback"], "external_player")
        self.assertEqual(
            contract["review_video_contract"]["visual_timeline"]["widget"],
            "review_visual_timeline",
        )
        self.assertEqual(
            contract["review_video_contract"]["visual_timeline"]["source"],
            "ReviewViewModel.visual_timeline",
        )
        self.assertIn(
            "manual_marker",
            contract["review_video_contract"]["visual_timeline"]["kinds"],
        )
        self.assertTrue(
            contract["review_video_contract"]["visual_timeline"]["fallback_safe"]
        )
        self.assertEqual(
            contract["review_video_contract"]["marker_edit_source"],
            "RecorderApplicationService.update_review_marker_label",
        )
        self.assertEqual(
            contract["review_video_contract"]["timeline_columns"],
            ["経過", "種別", "状態", "説明"],
        )
        self.assertTrue(contract["review_video_contract"]["timeline_user_labels"])
        self.assertEqual(
            contract["review_video_contract"]["tabs"],
            ["マーカー編集", "戦績入力"],
        )
        self.assertEqual(
            contract["review_video_contract"]["duel_compact_segment_fields"],
            ["status", "result", "play_order", "coin_face"],
        )
        self.assertFalse(contract["review_video_contract"]["source_column_visible"])
        self.assertEqual(
            contract["duel_editor_contract"]["deck_inputs"],
            "editable_candidate_combo",
        )
        self.assertEqual(
            contract["duel_editor_contract"]["entry_target"],
            "review_duel_tab_when_recording_exists",
        )
        self.assertEqual(
            contract["duel_editor_contract"]["compact_segment_fields"],
            ["status", "result", "play_order", "coin_face"],
        )
        self.assertEqual(contract["duel_editor_contract"]["dialog_minimum_size"], [720, 520])
        self.assertEqual(
            contract["bulk_duel_editor_contract"]["update_source"],
            "RecorderApplicationService.bulk_update_duel_records",
        )
        self.assertEqual(
            contract["operational_quality_audit_contract"]["target_version"],
            "2.6.0",
        )
        self.assertEqual(
            contract["operational_quality_audit_contract"]["missing_action_widgets"],
            [],
        )
        self.assertEqual(
            contract["operational_quality_audit_contract"]["placeholder_only_actions"],
            [],
        )
        self.assertIn(
            "録画",
            contract["operational_quality_audit_contract"]["screens"],
        )
        self.assertIn(
            "record_target_refresh",
            contract["operational_quality_audit_contract"]["action_widgets"]["record"],
        )
        self.assertIn(
            "clean_uninstall",
            contract["operational_quality_audit_contract"]["danger_actions_guarded"],
        )
        self.assertTrue(
            contract["operational_quality_audit_contract"]["review_timeline_localized"]
        )
        self.assertIn("history_duel", contract["duel_editor_contract"]["entry_button"])
        self.assertEqual(
            contract["duel_editor_contract"]["save_source"],
            "RecorderApplicationService.update_duel_record",
        )
        self.assertEqual(
            contract["duel_editor_contract"]["manual_create_source"],
            "RecorderApplicationService.create_manual_duel_record",
        )
        self.assertEqual(
            contract["settings_input_contract"]["visual_language_widget"],
            "QComboBox",
        )
        self.assertEqual(
            contract["settings_input_contract"]["visual_language_choices"],
            ["auto", "ja", "en"],
        )
        self.assertIn("history_duel", contract["icon_button_contract"]["buttons"])
        self.assertEqual(
            contract["icon_button_contract"]["provider"],
            "pictogrammers-inspired app line icons",
        )
        self.assertFalse(contract["icon_button_contract"]["uses_qt_standard_icons"])
        for widget in REVIEW_WIDGETS:
            self.assertIn(widget, contract["review_video_contract"]["widgets"])
        self.assertNotIn("youtube_connect", contract["widgets"])
        self.assertNotIn("youtube_disconnect", contract["widgets"])
        self.assertNotIn("youtube_refresh", contract["widgets"])
        self.assertNotIn("youtube_test_upload", contract["widgets"])
        self.assertNotIn("youtube_background_status", contract["widgets"])
        self.assertNotIn("youtube_upload_progress", contract["widgets"])
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

    def test_record_ui_state_switches_manual_controls_while_recording(self) -> None:
        state = pyside_record_ui_state(
            operation_state="manual_recording",
            operation_message="手動録画中",
            allowed_actions=frozenset({OperationAction.STOP_RECORDING}),
            watch_active=False,
            recording_active=True,
            recording_state="recording",
            recording_id="recording-id",
            output_path=Path("recordings/duel.mkv"),
            elapsed_seconds=65,
            visual_message="この録画では自動判定を使用しません",
        )

        self.assertEqual(state.status_text, "● 手動録画中")
        self.assertEqual(state.timer_text, "00:01:05")
        self.assertIn("録画状態: 録画中", state.record_detail)
        self.assertIn("録画ID: recording-id", state.record_detail)
        self.assertFalse(state.start_enabled)
        self.assertTrue(state.stop_enabled)
        self.assertFalse(state.watch_enabled)
        self.assertEqual(state.watch_text, "自動監視開始")

    def test_record_ui_state_switches_watch_controls_while_monitoring(self) -> None:
        state = pyside_record_ui_state(
            operation_state="watch_waiting",
            operation_message="対戦を待機しています",
            allowed_actions=frozenset({OperationAction.STOP_WATCH}),
            watch_active=True,
            recording_active=False,
            recording_state="completed",
            recording_id=None,
            output_path=None,
            elapsed_seconds=0,
            visual_message="対戦開始を待機しています",
        )

        self.assertEqual(state.status_text, "● 自動監視中")
        self.assertFalse(state.start_enabled)
        self.assertTrue(state.stop_enabled)
        self.assertTrue(state.watch_enabled)
        self.assertEqual(state.watch_text, "自動監視停止")

    def test_record_ui_state_allows_stop_while_watch_is_starting(self) -> None:
        state = pyside_record_ui_state(
            operation_state="watch_starting",
            operation_message="自動監視を開始しています",
            allowed_actions=frozenset({OperationAction.STOP_WATCH}),
            watch_active=True,
            recording_active=False,
            recording_state="completed",
            recording_id=None,
            output_path=None,
            elapsed_seconds=0,
            visual_message="録画開始後に自動判定状態を表示します",
        )

        self.assertEqual(state.status_text, "● 自動監視開始中")
        self.assertFalse(state.start_enabled)
        self.assertTrue(state.stop_enabled)
        self.assertTrue(state.watch_enabled)
        self.assertEqual(state.watch_text, "自動監視停止")

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

    def test_history_color_target_label_uses_japanese_display_names(self) -> None:
        self.assertEqual(history_color_target_label("coin_face.heads"), "コイン: 表")
        self.assertEqual(
            history_color_target_label("entry_origin.recording"),
            "登録元: 録画",
        )
        self.assertEqual(history_color_target_label("custom.key"), "custom.key")

    def test_season_table_display_row_uses_japanese_type_labels(self) -> None:
        season = SimpleNamespace(
            name="レジェンドアンソロジー",
            season_type="event",
            start_date="2026-08-17",
            end_date="2026-08-28",
            is_archived=False,
        )

        row = season_table_display_row(season)

        self.assertEqual(row[1], "イベント")
        self.assertNotIn("event", row)
        self.assertEqual(season_type_label("ranked"), "ランク戦")
        self.assertEqual(season_type_label("custom"), "カスタム")

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
            ("01:23.456", "メモ", "確定", "レビューで追加"),
        )

    def test_marker_label_splits_type_and_description(self) -> None:
        self.assertEqual(split_marker_label("プレミ: 展開順を確認"), ("プレミ", "展開順を確認"))
        self.assertEqual(split_marker_label("レビューで追加"), ("メモ", "レビューで追加"))
        self.assertEqual(compose_marker_label("リーサル", "打点確認"), "リーサル: 打点確認")

    def test_review_clip_range_message_explains_actual_output_range(self) -> None:
        message = review_clip_range_message(center_seconds=10.0, duration_seconds=45.0)

        self.assertIn("前30秒・後30秒", message)
        self.assertIn("00:00", message)
        self.assertIn("00:40", message)

    def test_review_error_message_summarizes_ffmpeg_output_format_error(self) -> None:
        message = review_operation_error_message(
            "クリップ出力",
            RuntimeError(
                "Unable to choose an output format for 'C:/tmp/clip'; "
                "Invalid argument"
            ),
        )

        self.assertIn("クリップ出力に失敗しました", message)
        self.assertIn("保存形式", message)
        self.assertNotIn("Unable to choose", message)


if __name__ == "__main__":
    unittest.main()
