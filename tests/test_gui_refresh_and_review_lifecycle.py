"""録画・カタログの画面同期と、実動画のファイル解放を検証する。"""

from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from master_duel_recorder_lite.application import ApplicationEvent, RecorderApplicationService
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.operation_state import OperationState
from master_duel_recorder_lite.recording_history import RecordingHistoryError, RecordingHistoryRepository
from master_duel_recorder_lite.recording_session import RecordingResult, RecordingState
from master_duel_recorder_lite.visual_worker import VisualDetectionStatus


def register_video(service, name, *, real_video=False):
    output = service.paths.recordings / f"{name}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    if real_video:
        subprocess.run(
            [shutil.which("ffmpeg"), "-nostdin", "-v", "error", "-f", "lavfi",
             "-i", "color=c=blue:s=160x90:r=10", "-t", "2", "-c:v", "mpeg4", str(output)],
            check=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    else:
        output.write_bytes(b"test video")
    started = datetime.now(timezone.utc)
    repository = RecordingHistoryRepository.from_runtime_paths(service.paths)
    repository.register_starting(recording_id=name, output_path=output, container="mp4",
                                 source="manual", created_at=started)
    repository.finalize(name, RecordingResult(RecordingState.COMPLETED, output, 0,
                        started, started + timedelta(seconds=2), output.stat().st_size, None, ()))
    return output


class RecordingRevisionTest(unittest.TestCase):
    def test_manual_stop_and_natural_completion_are_not_lost_between_polls(self):
        with tempfile.TemporaryDirectory() as temp:
            service = RecorderApplicationService(user_data_dir=Path(temp))
            for natural in (False, True):
                service._operation_state.transition(OperationState.MANUAL_STARTING, "starting")
                service._operation_state.transition(OperationState.MANUAL_RECORDING, "recording")
                result = SimpleNamespace(state=RecordingState.COMPLETED, output_path=Path(temp),
                                         started_at=None, ended_at=None)
                prepared = SimpleNamespace(
                    target=SimpleNamespace(recording_id="manual"),
                    visual_detection_status=VisualDetectionStatus("disabled", "", 0, 0, 0),
                    stop=Mock(return_value=result), release=Mock(),
                    poll=Mock(return_value=RecordingState.COMPLETED),
                )
                service._current = prepared
                before = service.recording_history_revision
                if natural:
                    service.recording_snapshot()
                else:
                    service.stop_recording()
                self.assertEqual(service.recording_history_revision, before + 1)
                service.recording_snapshot()
                self.assertEqual(service.recording_history_revision, before + 1)
                prepared.release.assert_called_once()
            service.close()


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class GuiRefreshAndReviewTest(unittest.TestCase):
    def run_gui(self, scenario):
        import test_pyside_catalog_redesign
        test_pyside_catalog_redesign.CatalogRedesignTest().run_gui(scenario)

    def test_recording_refresh_retains_edits_selection_and_coalesces_completions(self):
        def scenario(w, service, app):
            service = w.service
            service.create_manual_duel_record(DuelRecordValues(notes="original"),
                                             occurred_at=datetime.now(timezone.utc))
            w.show_page("history")
            table = w.widgets["history_table"]
            table.selectRow(0)
            selected = w.history_selection_ids
            editor = w.history_editor
            editor.fields["notes"].setPlainText("unsaved")
            for name in ("short1", "short2"):
                register_video(service, name)
                service._emit(None, ApplicationEvent("stopped", "completed", name, "completed"))
            self.assertEqual(service.recording_history_revision, 2)
            with patch.object(service, "get_history_dashboard", wraps=service.get_history_dashboard) as load:
                w._refresh_recording_state()
                self.assertEqual(table.rowCount(), 1)
                self.assertEqual(editor.fields["notes"].toPlainText(), "unsaved")
                load.assert_not_called()
                w._save_history_editor()
                self.assertEqual(table.rowCount(), 3)
                self.assertEqual(w.history_selection_ids, selected)
                self.assertEqual(editor.fields["notes"].toPlainText(), "unsaved")
                load.reset_mock()
                w._refresh_recording_state()
                load.assert_not_called()
            editor.fields["notes"].setPlainText("discard me")
            register_video(service, "third")
            service._emit(None, ApplicationEvent("stopped", "completed", "third", "completed"))
            w._refresh_recording_state()
            editor.bind(editor.view)
            w._refresh_recording_state()
            self.assertEqual(table.rowCount(), 4)
            self.assertEqual(w.history_selection_ids, selected)
            self.assertEqual(editor.fields["notes"].toPlainText(), "unsaved")
            deck_filter = w.widgets["history_own_deck_filter"]
            deck_filter.setCurrentIndex(1)
            w._apply_history_filters()
            selected_deck = deck_filter.currentData()
            register_video(service, "filtered")
            service._emit(None, ApplicationEvent("stopped", "completed", "filtered", "completed"))
            w._refresh_recording_state()
            self.assertEqual(deck_filter.currentData(), selected_deck)
            self.assertEqual(table.rowCount(), 0)
        self.run_gui(scenario)

    def test_catalog_save_updates_choices_without_changing_user_input(self):
        def scenario(w, service, app):
            service = w.service
            watch = w.widgets["watch_default_own_deck"]
            watch.setCurrentText("自由入力")
            own = w.history_editor.fields["own_deck"]
            own.setCurrentText("入力途中")
            deck_filter = w.widgets["history_own_deck_filter"]
            deck_filter.setCurrentIndex(1)
            previous = deck_filter.currentData()
            for key, filter_key in (("decks", "history_own_deck_filter"), ("tags", "history_tag_filter")):
                page = w.catalog_pages[key]
                page.new()
                name = "追加-" + key
                page.fields["name_input"].setText(name)
                self.assertTrue(page.save())
                self.assertTrue(any(page.table.item(i, 0).text() == name for i in range(page.table.rowCount())))
                self.assertGreaterEqual(w.widgets[filter_key].findText(name), 0)
            self.assertGreaterEqual(watch.findText("追加-decks"), 0)
            self.assertGreaterEqual(own.findText("追加-decks"), 0)
            self.assertEqual(watch.currentText(), "自由入力")
            self.assertEqual(own.currentText(), "入力途中")
            self.assertEqual(deck_filter.currentData(), previous)
            page = w.catalog_pages["tags"]
            page.new()
            page.fields["name_input"].setText("失敗したタグ")
            with patch.object(service, "add_tag", side_effect=OSError("failed")), patch.object(w, "_show_warning"):
                self.assertFalse(page.save())
            self.assertEqual(w.widgets["history_tag_filter"].findText("失敗したタグ"), -1)
        self.run_gui(scenario)

    @unittest.skipUnless(os.name == "nt" and shutil.which("ffmpeg"), "Windows and FFmpeg required")
    def test_closed_reviews_release_real_video_and_main_close_releases_all(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtMultimedia import QMediaPlayer
        from master_duel_recorder_lite.pyside_review import create_review_window
        import shiboken6
        import pywintypes
        import win32con
        import win32file

        def scenario(w, service, app):
            service = w.service
            def wait_for(condition):
                deadline = time.monotonic() + 8
                while not condition() and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertTrue(condition())

            def open_review(name):
                review = create_review_window(service=service, recording_id=name, parent=w,
                                               on_closed=w._forget_review_window)
                w.review_windows.append(review)
                review.show()
                review.player.play()
                wait_for(lambda: review.player.position() > 0)
                return review

            def delete_access(path):
                handle = win32file.CreateFile(str(path), win32con.DELETE,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
                    None, win32con.OPEN_EXISTING, 0, None)
                handle.Close()

            for mode in ("playing", "paused", "ended"):
                output = register_video(service, mode, real_video=True)
                review = open_review(mode)
                if mode == "paused":
                    review.player.pause()
                elif mode == "ended":
                    wait_for(lambda: review.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia)
                review.close()
                self.assertNotIn(review, w.review_windows)
                delete_access(output)
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertFalse(shiboken6.isValid(review))
                service.delete_history(mode)
                self.assertFalse(output.exists())
                self.assertIsNone(RecordingHistoryRepository.from_runtime_paths(service.paths).get(mode))
            output = register_video(service, "shared", real_video=True)
            first, second = open_review("shared"), open_review("shared")
            first.close()
            with self.assertRaises(pywintypes.error) as locked:
                delete_access(output)
            self.assertEqual(locked.exception.winerror, 32)
            with self.assertRaises(RecordingHistoryError):
                service.delete_history("shared")
            self.assertTrue(output.exists())
            self.assertIsNotNone(RecordingHistoryRepository.from_runtime_paths(service.paths).get("shared"))
            second.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
            delete_access(output)
            service.delete_history("shared")
            outputs = [register_video(service, name, real_video=True) for name in ("main1", "main2")]
            reviews = [open_review(name) for name in ("main1", "main2")]
            w.close()
            self.assertEqual(w.review_windows, [])
            for review, path in zip(reviews, outputs):
                delete_access(path)
                self.assertTrue(review.player.source().isEmpty())
        self.run_gui(scenario)
