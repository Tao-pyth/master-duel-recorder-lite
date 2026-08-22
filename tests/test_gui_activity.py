import queue
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from master_duel_recorder_lite.application import (
    RecordingSnapshot,
    YouTubeConnectionStatus,
)
from master_duel_recorder_lite.gui import (
    HISTORY_ROW_ACTIONS,
    ICON_GLYPHS,
    RECORD_STATUS_PRESENTATIONS,
    RecorderGui,
    WAITING_ACTIVITY_PREFIX,
    _format_statistics_detail,
    _format_win_rate,
    _audio_input_display,
    _catalog_row_values,
    _review_launch_command,
    _swatch_border_color,
    calendar_header_contract,
    _parse_filter_date,
    incomplete_duel_count_presentation,
    record_status_presentation,
)
from master_duel_recorder_lite.duel_catalog import DuelCatalogEntry
from master_duel_recorder_lite.duel_statistics import StatisticsMetric
from master_duel_recorder_lite.recording_session import RecordingState
from master_duel_recorder_lite.ui_preferences import UiPreferences


class FakeListbox:
    def __init__(self) -> None:
        self.items: list[str] = []

    def insert(self, index: int, message: str) -> None:
        self.items.insert(index, message)

    def size(self) -> int:
        return len(self.items)

    def get(self, index: int) -> str:
        return self.items[index]

    def delete(self, first: int, last: object | None = None) -> None:
        if last == "end":
            del self.items[first:]
        else:
            del self.items[first]


class GuiActivityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gui = RecorderGui.__new__(RecorderGui)
        self.gui.activity_list = FakeListbox()  # type: ignore[assignment]

    def test_waiting_activity_replaces_existing_line(self) -> None:
        self.gui._activity("自動監視を開始しました")
        self.gui._activity(
            "対戦開始を判定中です (0s)",
            replace_prefix=WAITING_ACTIVITY_PREFIX,
        )
        self.gui._activity(
            "対戦開始を判定中です (4s)",
            replace_prefix=WAITING_ACTIVITY_PREFIX,
        )

        self.assertEqual(
            self.gui.activity_list.items,
            ["対戦開始を判定中です (4s)", "自動監視を開始しました"],
        )

    def test_history_row_has_three_list_actions_and_keyboard_paths(
        self,
    ) -> None:
        self.assertEqual(
            [item[1] for item in HISTORY_ROW_ACTIONS],
            ["再生", "対戦記録を編集", "削除"],
        )
        self.assertEqual(len({ICON_GLYPHS[item[0]] for item in HISTORY_ROW_ACTIONS}), 3)
        self.assertEqual(
            [item[2] for item in HISTORY_ROW_ACTIONS],
            ["Enter", "Ctrl+E", "Delete"],
        )
        self.assertTrue(
            all(ord(ICON_GLYPHS[item[0]]) >= 0xE000 for item in HISTORY_ROW_ACTIONS)
        )

    def test_review_launch_command_uses_cli_entry_in_development(self) -> None:
        command = _review_launch_command(
            "recording-id",
            user_data_dir=Path("user_data"),
        )

        self.assertIn("-m", command)
        self.assertIn("master_duel_recorder_lite", command)
        self.assertEqual(command[-3:], ("launch", "recording-id", "--fallback-external"))
        self.assertIn("--user-data-dir", command)

    def test_calendar_header_uses_same_seven_column_grid_as_weekdays(self) -> None:
        contract = calendar_header_contract()

        self.assertEqual(contract["previous"], {"column": 0, "columnspan": 1})
        self.assertEqual(contract["title"], {"column": 1, "columnspan": 3})
        self.assertEqual(contract["today"], {"column": 4, "columnspan": 2})
        self.assertEqual(contract["next"], {"column": 6, "columnspan": 1})
        self.assertEqual(
            sum(item["columnspan"] for item in contract.values()),
            7,
        )

    def test_removing_waiting_activity_preserves_other_history(self) -> None:
        self.gui._activity("録画対象を保存しました")
        self.gui._activity("対戦開始を判定中です (4s)")
        self.gui._activity("対戦開始を判定中です (3s)")

        self.gui._remove_activity(WAITING_ACTIVITY_PREFIX)

        self.assertEqual(self.gui.activity_list.items, ["録画対象を保存しました"])

    def test_runtime_poll_does_not_call_service_while_operation_is_busy(self) -> None:
        gui = RecorderGui.__new__(RecorderGui)
        gui.watch_events = queue.Queue()
        gui.busy_operations = 1
        gui.closing = False
        gui.service = Mock()
        gui.root = Mock()

        gui._poll_runtime()

        gui.service.recording_snapshot.assert_not_called()
        gui.service.visual_detection_status.assert_not_called()
        gui.root.after.assert_called_once_with(500, gui._poll_runtime)

    def test_runtime_poll_updates_automatic_elapsed_time_every_500ms(self) -> None:
        gui = RecorderGui.__new__(RecorderGui)
        gui.watch_events = queue.Queue()
        gui.busy_operations = 0
        gui.closing = False
        gui.service = Mock()
        gui.service.watch_active = True
        gui.service.recording_snapshot.return_value = RecordingSnapshot(
            True,
            RecordingState.RECORDING,
            "automatic-id",
            Path("recordings/automatic.mkv"),
            datetime.now(timezone.utc),
            12.5,
        )
        gui.service.visual_detection_status.return_value = SimpleNamespace(
            message="判定中",
            source="desktop",
            resolution="1920x1080",
            profile="ja",
            effective_fps=2.0,
            visual_state="board",
            coin_score=0.0,
            board_score=1.0,
            turn_score=0.0,
            turn_order_score=0.0,
            result_score=0.0,
            error_score=0.0,
            replay_score=0.0,
            overlay_score=0.0,
            loading_score=0.0,
            agreement="3/5",
            restart_count=0,
        )
        gui.service.operation_snapshot.return_value = SimpleNamespace(message="録画中")
        gui.root = Mock()
        gui.elapsed_var = Mock()
        gui.record_detail_var = Mock()
        gui.visual_status_var = Mock()
        gui.visual_details_var = Mock()
        gui._activity = Mock()

        gui._poll_runtime()

        gui.service.recording_snapshot.assert_called_once_with()
        gui.elapsed_var.set.assert_called_once_with("00:00:12")
        gui.record_detail_var.set.assert_called_once_with(
            "録画ID: automatic-id\n保存先: 履歴で確認"
        )
        gui.root.after.assert_called_once_with(500, gui._poll_runtime)

    def test_automatic_watch_statuses_distinguish_waiting_and_recording(self) -> None:
        waiting = record_status_presentation("watch_waiting")
        candidate = record_status_presentation("candidate_recording")
        recording = record_status_presentation("automatic_recording")

        self.assertIn("録画待機", waiting.text)
        self.assertNotIn("録画中", waiting.text)
        self.assertIn("録画中", candidate.text)
        self.assertIn("対戦確認中", candidate.text)
        self.assertIn("録画中", recording.text)
        self.assertIn("対戦記録中", recording.text)
        self.assertEqual(
            len({waiting.background, candidate.background, recording.background}), 3
        )

    def test_every_record_status_has_visible_text_and_contrast_colors(self) -> None:
        self.assertEqual(
            set(RECORD_STATUS_PRESENTATIONS),
            {
                "idle",
                "starting",
                "manual_recording",
                "watch_waiting",
                "candidate_recording",
                "automatic_recording",
                "stopping",
                "failed",
            },
        )
        for presentation in RECORD_STATUS_PRESENTATIONS.values():
            self.assertTrue(presentation.text.startswith("● "))
            self.assertNotEqual(presentation.background, presentation.foreground)

    def test_incomplete_duel_count_highlights_only_positive_counts(self) -> None:
        complete = incomplete_duel_count_presentation(0)
        incomplete = incomplete_duel_count_presentation(12)

        self.assertEqual(complete.text, "戦績管理 未完了 0件")
        self.assertEqual(incomplete.text, "戦績管理 未完了 12件")
        self.assertNotEqual(complete.background, incomplete.background)
        with self.assertRaises(ValueError):
            incomplete_duel_count_presentation(-1)

    def test_history_double_click_action_uses_saved_preference(self) -> None:
        gui = RecorderGui.__new__(RecorderGui)
        gui.play_selected_history = Mock()
        gui.edit_selected_duel_record = Mock()

        gui.ui_preferences = UiPreferences(history_double_click_action="edit")
        gui._activate_history_double_click_action()
        gui.edit_selected_duel_record.assert_called_once_with()
        gui.play_selected_history.assert_not_called()

        gui.ui_preferences = UiPreferences(history_double_click_action="play")
        gui._activate_history_double_click_action()
        gui.play_selected_history.assert_called_once_with()

    def test_statistics_date_and_metric_presentations_are_unambiguous(self) -> None:
        self.assertEqual(_parse_filter_date("2026-08-12", "開始日"), date(2026, 8, 12))
        self.assertIsNone(_parse_filter_date("", "開始日"))
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            _parse_filter_date("2026/08/12", "開始日")

        metric = StatisticsMetric(matches=7, wins=6, losses=1, draws=0)
        self.assertEqual(_format_win_rate(metric), "85.7%")
        self.assertEqual(_format_statistics_detail(metric), "7戦  6勝  1敗  0引分")
        self.assertEqual(_format_win_rate(StatisticsMetric(0, 0, 0, 0)), "-")

    def test_refresh_improvement_uses_history_view_query(self) -> None:
        gui = RecorderGui.__new__(RecorderGui)
        gui.smoke_mode = False
        gui.service = Mock()
        gui.service.list_history_views.return_value = ("view",)
        gui.service.list_decks.return_value = ("deck",)
        gui.service.list_tags.return_value = ("tag",)
        gui.improvement_status_var = Mock()

        def run_now(operation, callback=None, error_callback=None):
            del error_callback
            value = operation()
            if callback is not None:
                callback(value)

        gui._run = run_now  # type: ignore[method-assign]

        gui.refresh_improvement()

        gui.service.list_history_views.assert_called_once()
        self.assertIn("query", gui.service.list_history_views.call_args.kwargs)
        gui.service.list_decks.assert_called_once_with()
        gui.service.list_tags.assert_called_once_with()
        gui.improvement_status_var.set.assert_called_once_with(
            "最近の戦績候補: 1件 / デッキ: 1件 / タグ: 1件"
        )

    def test_youtube_unconfigured_status_disables_connect_action(self) -> None:
        gui = RecorderGui.__new__(RecorderGui)
        gui.youtube_status_var = Mock()
        gui.youtube_scope_var = Mock()
        gui.youtube_connect_button = Mock()
        gui.youtube_disconnect_button = Mock()
        gui.youtube_test_button = Mock()

        gui._render_youtube_status(
            YouTubeConnectionStatus(
                "unconfigured",
                "このビルドではYouTube連携を開始できません。",
                can_connect=False,
            )
        )

        gui.youtube_status_var.set.assert_called_once()
        gui.youtube_connect_button.configure.assert_called_once_with(state="disabled")
        gui.youtube_disconnect_button.configure.assert_called_once_with(state="disabled")
        gui.youtube_test_button.configure.assert_called_once_with(state="disabled")

    def test_catalog_row_values_show_usage_count_only_for_decks(self) -> None:
        timestamp = datetime.now(timezone.utc)
        deck = DuelCatalogEntry(
            entry_id=1,
            kind="deck",
            name="青眼",
            description="主力",
            color="#ffee00",
            is_archived=False,
            opponent_only=True,
            hidden_from_history_statistics=False,
            deck_only=False,
            created_at=timestamp,
            updated_at=timestamp,
            usage_count=7,
        )
        tag = DuelCatalogEntry(
            entry_id=2,
            kind="tag",
            name="大会",
            description="公式",
            color="#112233",
            is_archived=False,
            opponent_only=False,
            hidden_from_history_statistics=False,
            deck_only=True,
            created_at=timestamp,
            updated_at=timestamp,
        )

        self.assertEqual(_catalog_row_values("deck", deck), ("青眼", "主力", 7, "相手のみ"))
        self.assertEqual(_catalog_row_values("tag", tag), ("大会", "公式", "デッキ専用"))

    def test_deck_color_swatch_border_uses_contrast_color(self) -> None:
        self.assertEqual(_swatch_border_color("#FFFF66"), "#202124")
        self.assertEqual(_swatch_border_color("#123456"), "#ffffff")

    def test_process_audio_input_display_does_not_read_as_audio_none(self) -> None:
        process = _audio_input_display("process", "")
        none = _audio_input_display("none", "")
        device = _audio_input_display("device", "マイク")

        self.assertIn("Master Duel単体音声", process)
        self.assertIn("DirectShow入力は未使用", process)
        self.assertNotEqual(process, none)
        self.assertIn("映像のみ", none)
        self.assertEqual(device, "マイク")

    def test_process_audio_mode_selection_marks_directshow_input_unused(self) -> None:
        gui = RecorderGui.__new__(RecorderGui)
        gui.audio_modes_by_label = {"Master Duelのみ（推奨）": "process"}
        gui.audio_mode_var = Mock(get=Mock(return_value="Master Duelのみ（推奨）"))
        gui.setting_vars = {"recorder.audio_mode": Mock()}
        gui.audio_input_combo = Mock()
        gui.audio_choice_var = Mock()
        gui.audio_status_var = Mock()

        gui._audio_mode_selected()

        gui.setting_vars["recorder.audio_mode"].set.assert_called_once_with("process")
        gui.audio_input_combo.configure.assert_called_once_with(state="disabled")
        gui.audio_choice_var.set.assert_called_once_with(
            "Master Duel単体音声（DirectShow入力は未使用）"
        )
        gui.audio_status_var.set.assert_called_once_with(
            "Master Duelプロセスと子プロセスの音声だけを取得します"
        )


if __name__ == "__main__":
    unittest.main()
