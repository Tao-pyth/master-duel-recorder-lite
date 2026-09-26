"""隔離データと実widgetで、映像の面積配分と編集操作を検証する。"""

from datetime import date
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from master_duel_recorder_lite.application import RecorderApplicationService
from master_duel_recorder_lite.duel_records import DuelRecordValues
from master_duel_recorder_lite.review_viewmodel import ReviewMarkerRequest
from test_gui_refresh_and_review_lifecycle import register_video


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 is optional")
class ReviewLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtWidgets import QMessageBox
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = RecorderApplicationService(user_data_dir=Path(self.temp.name))
        self.addCleanup(self.service.close)
        self.warning = self.enterContext(patch.object(QMessageBox, "warning"))
        self.enterContext(patch.object(QMessageBox, "information"))
        # 再生テスト以外はエラー表示を捕捉し、ダミー動画でUIを検証する。
        self.enterContext(patch.object(self.service, "play_recording"))
        register_video(self.service, "review")

    def open_review(self, *, real=False, **kwargs):
        from master_duel_recorder_lite.pyside_review import create_review_window
        if real:
            w = create_review_window(service=self.service, recording_id="real", **kwargs)
        else:
            w = create_review_window(service=self.service, recording_id="review", **kwargs)
        w.show()
        self.app.processEvents()
        self.warning.reset_mock()
        self.addCleanup(self.close_review, w)
        return w

    def close_review(self, w):
        import shiboken6
        if shiboken6.isValid(w):
            w.close()
            self.app.processEvents()

    def widget(self, w, name):
        from PySide6.QtWidgets import QWidget
        result = w.findChild(QWidget, name)
        self.assertIsNotNone(result, name)
        return result

    def test_resize_and_tabs_keep_video_primary_and_editor_reachable(self):
        from PySide6.QtCore import Qt
        w = self.open_review()
        video = self.widget(w, "review_video")
        tabs = self.widget(w, "review_editor_tabs")
        self.assertEqual(tabs.currentWidget().objectName(), "review_duel_tab")
        self.assertEqual(video.aspectRatioMode(), Qt.AspectRatioMode.KeepAspectRatio)
        measurements = []
        for width, height in ((1120, 780), (1120, 966), (1440, 966), (960, 640)):
            w.resize(width, height)
            self.app.processEvents()
            self.assertEqual((w.width(), w.height()), (width, height))
            self.assertLess(video.mapTo(w, video.rect().topRight()).x(), tabs.x())
            geometry = video.geometry()
            for tab in (1, 0):
                tabs.setCurrentIndex(tab)
                self.app.processEvents()
                self.assertEqual(video.geometry(), geometry)
            measurements.append((video.width(), tabs.width()))
            if width == 1120:
                # V2.7.15同幅で実測した16:9映像は高さ260px。
                self.assertGreater(min(video.height(), video.width() * 9 / 16), 260)
            scroll = self.widget(w, "review_duel_scroll")
            self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
            save = self.widget(w, "review_duel_save")
            self.assertTrue(save.isVisible())
            self.assertLessEqual(save.mapTo(w, save.rect().bottomRight()).y(), w.height())
            if height == 640:
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            for name in ("review_play_pause", "review_position_slider", "review_visual_timeline"):
                control = self.widget(w, name)
                self.assertGreater(control.mapTo(w, control.rect().topLeft()).y(),
                                   video.mapTo(w, video.rect().bottomLeft()).y())
            capture = os.environ.get("MDRL_REVIEW_CAPTURE")
            if capture:
                dest = Path(capture)
                dest.mkdir(parents=True, exist_ok=True)
                w.grab().save(str(dest / f"review-{width}x{height}.png"))
        self.assertEqual(measurements[2][0] - measurements[1][0], 320)
        self.assertEqual(len({entry[1] for entry in measurements}), 1)

    def test_long_values_load_save_and_reload_without_expanding_window(self):
        long_deck = "長いデッキ名称" * 10
        self.service.add_deck(long_deck)
        season = self.service.add_season(name="長いシーズン" * 12,
            season_type="ranked", duel_type="ranked",
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))
        original = DuelRecordValues(status="confirmed", result="win", play_order="second",
            coin_face="tails", own_deck=long_deck, opponent_deck="相手デッキ", duel_type="ranked",
            tags=("検証",), notes="保存済みメモ", season_id=season.season_id)
        self.service.save_duel_record("review", original, expected_revision=0)
        marker = self.service.add_review_marker(ReviewMarkerRequest("review", 500, "メモ: 保存済み"))
        saved = Mock()
        w = self.open_review(on_duel_saved=saved)
        w.resize(960, 640)
        self.app.processEvents()
        self.assertEqual(w.width(), 960)
        own = self.widget(w, "review_duel_own_deck")
        self.assertEqual(own.currentText(), long_deck)
        self.assertEqual(self.widget(w, "review_duel_notes").toPlainText(), original.notes)
        self.assertEqual(self.widget(w, "review_duel_season").currentData(), season.season_id)
        for field, value in (("status", "draft"), ("result", "loss"),
                             ("play_order", "first"), ("coin_face", "heads")):
            self.widget(w, f"review_duel_{field}_{value}").click()
        own.setCurrentText("自由入力")
        self.widget(w, "review_duel_opponent_deck").setCurrentText("更新相手")
        combo = self.widget(w, "review_duel_duel_type")
        combo.setCurrentIndex(combo.findData("event"))
        self.widget(w, "review_duel_tags").setText("検証, 更新")
        self.widget(w, "review_duel_notes").setPlainText("更新メモ")
        self.widget(w, "review_duel_save").click()
        saved.assert_called_once_with()
        expected = DuelRecordValues(status="draft", result="loss", play_order="first",
            coin_face="heads", own_deck="自由入力", opponent_deck="更新相手", duel_type="event",
            tags=("検証", "更新"), notes="更新メモ", season_id=season.season_id)
        self.assertEqual(self.service.get_duel_editor_data("review").values, expected)
        self.assertEqual(self.service.get_review_view_model("review").timeline[0].event_id, marker.event_id)
        self.close_review(w)
        reopened = self.open_review()
        self.assertEqual(self.widget(reopened, "review_duel_own_deck").currentText(), "自由入力")
        self.assertEqual(self.widget(reopened, "review_duel_notes").toPlainText(), "更新メモ")
        self.warning.assert_not_called()

    def test_marker_edit_candidate_actions_and_external_clip_routes(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox
        from master_duel_recorder_lite.duel_timeline import DuelTimelineRepository
        repo = DuelTimelineRepository.from_runtime_paths(self.service.paths)
        for elapsed in (200, 400):
            repo.add("review", elapsed_ms=elapsed, event_type="duel_start",
                     label="候補", source="detected", status="candidate",
                     confidence=.9, detector_id="test", detector_version="1")
        w = self.open_review(initial_tab="marker")
        tabs = self.widget(w, "review_editor_tabs")
        self.assertEqual(tabs.currentWidget().objectName(), "review_marker_tab")
        table = self.widget(w, "review_timeline_table")
        for row, name, status in ((0, "review_timeline_confirm", "confirmed"),
                                  (1, "review_timeline_reject", "rejected")):
            table.selectRow(row)
            self.widget(w, name).click()
            self.assertEqual(self.service.get_review_view_model("review").timeline[row].status, status)
        self.widget(w, "review_marker_add").click()
        row = next(r for r in range(table.rowCount())
                   if table.item(r, 0).data(Qt.ItemDataRole.UserRole + 2) == "marker")
        table.selectRow(row)

        def accept_edit(dialog):
            dialog.findChild(QLineEdit).setText("更新マーカー")
            return QDialog.DialogCode.Accepted

        with patch.object(QDialog, "exec", accept_edit):
            self.widget(w, "review_marker_edit").click()
        self.assertIn("メモ: 更新マーカー", [e.label for e in self.service.get_review_view_model("review").timeline])
        with patch.object(self.service, "play_recording") as play:
            self.widget(w, "review_external_player").click()
            play.assert_called_once_with("review")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
                patch.object(self.service, "export_review_clip") as export:
            self.widget(w, "review_clip_export").click()
            export.assert_not_called()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(QMessageBox, "exec"), \
                patch.object(self.service, "export_review_clip", return_value=Mock(output_path=Path(self.temp.name)/"clip.mp4")) as export, \
                patch("master_duel_recorder_lite.pyside_review._open_folder") as folder:
            self.widget(w, "review_clip_export").click()
            export.assert_called_once()
            self.widget(w, "review_clip_open_folder").click()
            folder.assert_called_once_with(Path(self.temp.name))
        self.warning.assert_not_called()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed for synthetic video")
    def test_real_video_play_pause_seek_timeline_and_release(self):
        from PySide6.QtMultimedia import QMediaPlayer
        register_video(self.service, "real", real_video=True)
        self.service.add_review_marker(ReviewMarkerRequest("real", 900, "メモ: 再生位置"))
        w = self.open_review(real=True)
        play = self.widget(w, "review_play_pause")
        play.click()

        def wait_for(condition):
            deadline = time.monotonic() + 5
            while not condition() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertTrue(condition())

        wait_for(lambda: w.player.position() > 0)
        self.assertEqual(w.player.playbackState(), QMediaPlayer.PlaybackState.PlayingState)
        play.click()
        self.assertEqual(w.player.playbackState(), QMediaPlayer.PlaybackState.PausedState)
        self.widget(w, "review_position_slider").sliderMoved.emit(500)
        wait_for(lambda: w.player.position() == 500)
        self.widget(w, "review_editor_tabs").setCurrentIndex(1)
        self.widget(w, "review_timeline_table").cellClicked.emit(0, 0)
        wait_for(lambda: w.player.position() == 900)
        self.assertIn("00:00.900", self.widget(w, "review_position_label").text())
        w.close()
        self.assertTrue(w.player.source().isEmpty())
        self.assertIsNone(w.player.videoOutput())
        self.warning.assert_not_called()
