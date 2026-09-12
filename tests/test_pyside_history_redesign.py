"""採用した上下配置と、実DBに対する編集・遷移保護を検証する。"""

from dataclasses import replace
from datetime import date, datetime, timezone
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.duel_records import DuelRecordRepository, DuelRecordValues
from master_duel_recorder_lite.duel_workflow import DuelFilterCriteria
from master_duel_recorder_lite.recording_history import RecordingHistoryRepository


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class HistoryRedesignTest(unittest.TestCase):
    def run_gui(self, scenario):
        from PySide6.QtWidgets import QApplication, QMainWindow
        from master_duel_recorder_lite import pyside_gui

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = RecorderApplicationService(project_root=root, user_data_dir=root / "data")
            season = service.add_season(name="検証シーズン", season_type="ranked", duel_type="ranked",
                                        start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))
            for index in range(16):
                service.create_manual_duel_record(
                    DuelRecordValues(status="draft" if index % 2 else "confirmed",
                                     result="unknown" if index % 2 else "win",
                                     own_deck="白き森エルフェンノーツ" if index < 12 else "白き森調和",
                                     opponent_deck="相手", notes="保存済みメモ", tags=("検証",),
                                     coin_face="heads", play_order="second", duel_type="ranked",
                                     season_id=season.season_id),
                    occurred_at=datetime(2026, 9, 1 + index // 2, 12, index, tzinfo=timezone.utc),
                )

            def inspect(_app):
                app = QApplication.instance()
                window = next(w for w in app.topLevelWidgets() if isinstance(w, QMainWindow) and w.isVisible())
                window.record_state_timer.stop()
                window.show_page("history")
                try:
                    scenario(window, service, app)
                finally:
                    window.history_editor.baseline = None
                    window.settings_baseline = None
                    window.close()
                    window.deleteLater()
                    app.processEvents()
                return 0

            with patch.dict(os.environ, {"QT_QPA_PLATFORM": os.environ.get("MDRL_TEST_QPA", "offscreen")}), patch.object(QApplication, "exec", inspect):
                self.assertEqual(pyside_gui.main(["--project-root", str(root), "--user-data-dir", str(root / "data")]), 0)

    @staticmethod
    def choose(editor, field, value):
        next(b for b in editor.groups[field].buttons() if b.property("choiceData") == value).click()

    def test_toggle_preserves_compound_and_saved_filters_and_empty_state(self):
        from PySide6.QtCore import QDate

        def scenario(w, service, app):
            widgets = w.widgets
            table = widgets["history_table"]
            self.assertEqual(table.rowCount(), 16)
            widgets["history_period_mode"].setCurrentText("期間指定")
            widgets["history_date_from_picker"].setDate(QDate(2026, 9, 2))
            widgets["history_date_to_picker"].setDate(QDate(2026, 9, 5))
            deck_id = next(d.entry_id for d in service.list_decks() if d.name == "白き森エルフェンノーツ")
            deck = widgets["history_own_deck_filter"]
            deck.setCurrentIndex(deck.findData(deck_id))
            widgets["history_coin_filter"].setCurrentIndex(widgets["history_coin_filter"].findData("heads"))
            w._apply_history_filters()
            before = w.history_applied_query
            self.assertEqual(table.rowCount(), 8)
            for count in (4, 8, 4, 8):
                widgets["history_incomplete"].click()
                self.assertEqual(table.rowCount(), count)
                self.assertEqual(replace(w.history_applied_query, incomplete_only=False), before)
                self.assertEqual(widgets["history_incomplete"].text(), "未完了のみ")
            saved = service.save_duel_filter("保存条件", DuelFilterCriteria(own_deck_id=deck_id, coin_face="heads"))
            w._populate_history_filter_choices()
            combo = widgets["history_saved_filter"]
            combo.setCurrentIndex(combo.findText(saved.name))
            w._apply_history_filters()
            self.assertFalse(deck.isEnabled())
            widgets["history_incomplete"].click()
            widgets["history_incomplete"].click()
            self.assertEqual(table.rowCount(), 8)
            self.assertEqual(combo.currentData(), saved)
            widgets["history_date_from_picker"].setDate(QDate(2030, 1, 1))
            widgets["history_date_to_picker"].setDate(QDate(2030, 1, 31))
            w._apply_history_filters()
            self.assertEqual(table.rowCount(), 0)
            self.assertIn("一致する戦績はありません", w.history_summary.text())
            widgets["history_filter_clear"].click()
            self.assertEqual(table.rowCount(), 16)
            self.assertFalse(w.history_incomplete_only)
            self.assertTrue(deck.isEnabled())

        self.run_gui(scenario)

    def test_manual_roundtrip_all_fields_and_confirmation_does_not_require_guesses(self):
        def scenario(w, service, app):
            table = w.widgets["history_table"]
            table.selectRow(0)
            editor = w.history_editor
            original = editor.record
            self.choose(editor, "result", "draw")
            self.choose(editor, "play_order", "first")
            self.choose(editor, "coin_face", "unknown")
            self.choose(editor, "status", "confirmed")
            editor.fields["own_deck"].setCurrentText("自由入力デッキ")
            editor.fields["opponent_deck"].setCurrentText("変更相手")
            editor.fields["tags"].setText("タグA、タグB")
            editor.fields["notes"].setPlainText("更新メモ\n二行目")
            kind = editor.fields["duel_type"]
            kind.setCurrentIndex(kind.findData("event"))
            editor.fields["season_id"].setCurrentIndex(0)
            self.assertTrue(editor.is_dirty())
            w.widgets["history_save"].click()
            saved = DuelRecordRepository.from_runtime_paths(service.paths).get(original.duel_id)
            self.assertEqual(saved.revision, original.revision + 1)
            self.assertEqual(saved.values, DuelRecordValues(status="confirmed", result="draw", play_order="first",
                             coin_face="unknown", own_deck="自由入力デッキ", opponent_deck="変更相手", duel_type="event",
                             tags=("タグA", "タグB"), notes="更新メモ\n二行目", season_id=None))
            self.assertEqual(editor.record.duel_id, original.duel_id)
            self.assertFalse(editor.is_dirty())
            self.assertIn("保存しました", w.history_summary.text())
            w.widgets["history_incomplete"].click()
            self.assertNotIn(original.duel_id, w.history_views_by_row_id)
            self.assertFalse(editor.isVisible())

        self.run_gui(scenario)

    def test_cancel_all_transitions_keeps_selection_inputs_filters_and_database(self):
        from PySide6.QtWidgets import QMessageBox

        def scenario(w, service, app):
            table = w.widgets["history_table"]
            table.selectRow(0)
            editor = w.history_editor
            original = editor.record
            selected = w.history_selection_ids
            editor.fields["own_deck"].setCurrentText("未保存入力")
            filters = w._capture_history_filters()
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
                table.selectRow(1)
                self.assertEqual(w.history_selection_ids, selected)
                w.widgets["history_incomplete"].click()
                self.assertFalse(w.widgets["history_incomplete"].isChecked())
                w.widgets["history_own_deck_filter"].setCurrentIndex(1)
                w._apply_history_filters()
                self.assertEqual(w._capture_history_filters(), filters)
                w.widgets["history_filter_clear"].click()
                w.widgets["history_refresh"].click()
                w.widgets["history_next"].click()
                w.show_page("record")
                self.assertEqual(w.stack.currentWidget(), w.pages["history"])
                self.assertFalse(w.close())
                self.assertEqual(w.history_selection_ids, selected)
                self.assertEqual(editor.fields["own_deck"].currentText(), "未保存入力")
                self.assertTrue(editor.is_dirty())
            # 背景の通知は入力を破壊しない。同一選択の通知でも再bindしない。
            self.assertFalse(w._refresh_history())
            w._history_selection_changed()
            self.assertEqual(editor.fields["own_deck"].currentText(), "未保存入力")
            self.assertEqual(DuelRecordRepository.from_runtime_paths(service.paths).get(original.duel_id), original)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
                w.widgets["history_refresh"].click()
            self.assertFalse(editor.is_dirty())
            self.assertEqual(editor.fields["own_deck"].currentText(), original.values.own_deck)

        self.run_gui(scenario)

    def test_save_failure_revision_conflict_and_write_block_keep_input(self):
        from PySide6.QtWidgets import QMessageBox

        def scenario(w, service, app):
            table = w.widgets["history_table"]
            table.selectRow(0)
            editor = w.history_editor
            original = editor.record
            editor.fields["notes"].setPlainText("保持対象")
            with patch.object(w.service, "update_duel_record", side_effect=OSError("書込失敗")), patch.object(QMessageBox, "warning"), patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
                table.selectRow(1)
                self.assertEqual(w.history_selection_ids, (original.duel_id,))
                self.assertTrue(editor.is_dirty())
            service.update_duel_record(original.duel_id, replace(original.values, notes="別画面の更新"), expected_revision=original.revision)
            with patch.object(QMessageBox, "warning") as warning:
                self.assertFalse(editor.save())
                warning.assert_called_once()
            self.assertTrue(editor.is_dirty())
            self.assertEqual(editor.fields["notes"].toPlainText(), "保持対象")
            self.assertEqual(DuelRecordRepository.from_runtime_paths(service.paths).get(original.duel_id).values.notes, "別画面の更新")
            with patch.object(w.service, "duel_write_block_reason", return_value="録画中"), patch.object(QMessageBox, "warning"):
                self.assertFalse(editor.save())
                w._update_history_action_states()
                self.assertFalse(w.widgets["history_save"].isEnabled())

        self.run_gui(scenario)

    def test_checkbox_multiple_selection_bulk_and_cancel(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QMessageBox

        def scenario(w, service, app):
            table = w.widgets["history_table"]
            app.processEvents()
            QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(table.item(0, 11)).center())
            self.assertTrue(w.history_editor.isVisible())
            w.history_editor.fields["notes"].setPlainText("保護")
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
                QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(table.item(1, 11)).center())
            self.assertEqual(len(w._selected_history_views()), 1)
            self.assertEqual(table.item(1, 11).checkState(), Qt.CheckState.Unchecked)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save):
                QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(table.item(1, 11)).center())
            self.assertEqual(len(w._selected_history_views()), 2)
            self.assertTrue(w.widgets["history_bulk"].isVisible())
            self.assertTrue(w.widgets["history_bulk"].isEnabled())
            self.assertFalse(w.history_editor.isVisible())
            self.assertFalse(w.widgets["history_save"].isVisible())
            self.assertFalse(w.history_menu_actions["history_delete"].isEnabled())
            table.clearSelection()
            self.assertFalse(w.history_selection_bar.isVisible())
            self.assertFalse(w.history_editor.isVisible())

        self.run_gui(scenario)

    def test_recording_without_record_and_existing_record_roundtrip_next(self):
        def scenario(w, service, app):
            history = RecordingHistoryRepository.from_runtime_paths(service.paths)
            history.register_starting(recording_id="mock-video", output_path=service.paths.recordings / "mock.mkv",
                                      container="mkv", source="manual", created_at=datetime(2026, 9, 12, tzinfo=timezone.utc))
            w._refresh_history()
            w.widgets["history_table"].selectRow(0)
            editor = w.history_editor
            self.assertIsNone(editor.record)
            self.assertTrue(w.widgets["history_play"].isEnabled())
            self.choose(editor, "status", "draft")
            editor.fields["own_deck"].setCurrentText("録画戦績")
            w.widgets["history_save"].click()
            self.assertIsNotNone(editor.record)
            self.assertEqual(editor.record.recording_id, "mock-video")
            self.assertEqual(w.history_selection_ids, (editor.record.duel_id,))
            self.choose(editor, "result", "loss")
            w.widgets["history_save"].click()
            self.assertEqual(DuelRecordRepository.from_runtime_paths(service.paths).get("mock-video").values.result, "loss")
            w.widgets["history_incomplete"].click()
            before = editor.record.duel_id
            w.widgets["history_next"].click()
            self.assertNotEqual(editor.record.duel_id, before)
            self.assertNotEqual(editor.record.values.status, "confirmed")
            # 現在のデッキ条件に未完了が1件だけの場合は次なし。
            w._populate_history_filter_choices()
            combo = w.widgets["history_own_deck_filter"]
            combo.setCurrentIndex(combo.findText("録画戦績"))
            w._apply_history_filters()
            w.widgets["history_table"].selectRow(0)
            w.widgets["history_next"].click()
            self.assertIn("次の未完了戦績はありません", w.history_summary.text())
            self.choose(editor, "status", "confirmed")
            w.widgets["history_save"].click()
            self.assertEqual(w.widgets["history_table"].rowCount(), 0)
            self.assertFalse(editor.isVisible())

        self.run_gui(scenario)

    def test_layout_and_existing_actions_remain_reachable(self):
        from PySide6.QtCore import QPoint, QRect

        def scenario(w, service, app):
            table = w.widgets["history_table"]
            self.assertFalse(w.history_editor.isVisible())
            for size in ((980, 640), (1180, 760), (1440, 1024)):
                w.resize(*size)
                table.clearSelection()
                app.processEvents()
                height_unselected = table.height()
                table.selectRow(0)
                app.processEvents()
                page = w.pages["history"]
                self.assertEqual(page.horizontalScrollBar().maximum(), 0)
                self.assertGreaterEqual(height_unselected, table.height())
                table_rect = QRect(table.mapTo(page.widget(), QPoint()), table.size())
                editor_rect = QRect(w.history_editor.mapTo(page.widget(), QPoint()), w.history_editor.size())
                self.assertLessEqual(table_rect.bottom(), editor_rect.top())
                page.ensureWidgetVisible(w.widgets["history_save"])
                app.processEvents()
                save_rect = QRect(w.widgets["history_save"].mapTo(page.viewport(), QPoint()), w.widgets["history_save"].size())
                self.assertTrue(page.viewport().rect().contains(save_rect), size)
                w.history_editor.details_toggle.setChecked(True)
                w.widgets["history_filter_details"].setChecked(True)
                app.processEvents()
                self.assertEqual(page.horizontalScrollBar().maximum(), 0)
                page.ensureWidgetVisible(w.widgets["history_save"])
                app.processEvents()
                self.assertTrue(w.widgets["history_save"].isVisible())
                w.history_editor.details_toggle.setChecked(False)
                w.widgets["history_filter_details"].setChecked(False)
            for key in ("history_duplicates", "history_columns", "history_duel", "history_youtube", "history_delete"):
                self.assertIn(key, w.history_menu_actions)
            # 列を隠すだけでデータやアクセスを削除しない。
            self.assertTrue(table.isColumnHidden(8))
            menu = w.history_columns_menu
            next(action for action in menu.actions() if action.text() == "相手デッキ").trigger()
            self.assertFalse(table.isColumnHidden(8))
            if os.environ.get("MDRL_HISTORY_SCREENSHOTS"):
                output = Path(os.environ["MDRL_HISTORY_SCREENSHOTS"])
                output.mkdir(parents=True, exist_ok=True)
                w.resize(1180, 760)
                table.setColumnHidden(8, True)
                for mode in ("normal", "incomplete", "multiple", "details", "unselected"):
                    w.widgets["history_incomplete"].setChecked(False)
                    w.history_incomplete_only = False
                    w._refresh_history()
                    table.selectRow(0)
                    w.history_editor.details_toggle.setChecked(mode == "details")
                    if mode == "incomplete":
                        w.widgets["history_incomplete"].click()
                    if mode == "multiple":
                        w._restore_history_selection(tuple(w.history_views_by_row_id)[:2])
                        w._history_selection_changed()
                    if mode == "unselected":
                        table.clearSelection()
                    w.pages["history"].verticalScrollBar().setValue(0)
                    app.processEvents()
                    w.grab().save(str(output / f"history-{mode}.png"))

        self.run_gui(scenario)

    def test_next_visits_records_with_identical_dates_and_keeps_scroll(self):
        def scenario(w, service, app):
            identifiers = set()
            for _ in range(3):
                record = service.create_manual_duel_record(
                    DuelRecordValues(status="draft", own_deck="同時刻"),
                    occurred_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
                )
                identifiers.add(record.duel_id)
            w._populate_history_filter_choices()
            combo = w.widgets["history_own_deck_filter"]
            combo.setCurrentIndex(combo.findText("同時刻"))
            w._apply_history_filters()
            w.widgets["history_table"].selectRow(0)
            visited = {w.history_editor.record.duel_id}
            for _ in range(2):
                w.widgets["history_next"].click()
                visited.add(w.history_editor.record.duel_id)
            self.assertEqual(visited, identifiers)
            w.widgets["history_filter_clear"].click()
            table = w.widgets["history_table"]
            table.selectRow(12)
            app.processEvents()
            table.verticalScrollBar().setValue(100)
            scroll = table.verticalScrollBar().value()
            w.history_editor.fields["notes"].setPlainText("スクロール位置を維持")
            w.widgets["history_save"].click()
            self.assertEqual(table.verticalScrollBar().value(), scroll)

        self.run_gui(scenario)

    def test_menus_call_existing_bulk_review_delete_and_youtube_paths(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

        def scenario(w, service, app):
            table = w.widgets["history_table"]
            table.selectRow(0)
            titles = []

            def dialog_exec(dialog):
                titles.append(dialog.windowTitle())
                return 0

            with patch.object(QDialog, "exec", dialog_exec):
                w.history_menu_actions["history_duel"].trigger()
                w.widgets["manual_duel_add"].click()
                table.item(1, 11).setCheckState(Qt.CheckState.Checked)
                w.widgets["history_bulk"].click()
            self.assertEqual(titles, ["戦績編集", "戦績編集", "一括編集"])
            table.selectRow(0)
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question, patch.object(w.service, "delete_duel_record") as delete:
                w.history_menu_actions["history_delete"].trigger()
                question.assert_called_once()
                delete.assert_not_called()
            with patch.object(w, "_show_information") as information, patch.object(w.service, "duplicate_duel_candidates", return_value=()) as duplicate:
                w.history_menu_actions["history_duplicates"].trigger()
                w.history_menu_actions["history_columns"].trigger()
                duplicate.assert_called_once()
                self.assertEqual(information.call_count, 2)
            history = RecordingHistoryRepository.from_runtime_paths(service.paths)
            history.register_starting(recording_id="review-route", output_path=service.paths.recordings / "review.mkv",
                                      container="mkv", source="manual", created_at=datetime(2026, 9, 12, tzinfo=timezone.utc))
            w._refresh_history()
            table.selectRow(0)
            preview = QWidget(w)
            with patch("master_duel_recorder_lite.pyside_review.create_review_window", return_value=preview) as create:
                w.widgets["history_play"].click()
                self.assertEqual(create.call_args.kwargs["recording_id"], "review-route")
                callback = create.call_args.kwargs["on_duel_saved"]
                w.history_editor.fields["notes"].setPlainText("背景更新で保持")
                callback()
                self.assertTrue(w.history_editor.is_dirty())
                w.history_editor.bind(w.history_editor.view)
                w.history_menu_actions["history_duel"].trigger()
                self.assertEqual(create.call_args.kwargs["initial_tab"], "duel")
            with patch.object(w.service, "youtube_preparation_status", return_value=SimpleNamespace(message="確認")), patch.object(w.service, "get_youtube_upload_dialog_data", return_value=SimpleNamespace(youtube_watch_url=None, title="検証")), patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as question, patch.object(w, "_start_youtube_upload") as upload:
                w.history_menu_actions["history_youtube"].trigger()
                question.assert_called_once()
                upload.assert_not_called()
            preview.close()

        self.run_gui(scenario)
