from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import webbrowser

from .application import RecorderApplicationService
from .review_viewmodel import ReviewClipExportRequest, ReviewMarkerRequest


class PySideReviewError(RuntimeError):
    """PySide6レビュー画面を起動できない場合のエラーです。"""


@dataclass(frozen=True)
class PySideReviewAvailability:
    available: bool
    message: str


def check_pyside6_review_available() -> PySideReviewAvailability:
    if importlib.util.find_spec("PySide6") is None:
        return PySideReviewAvailability(
            False,
            "PySide6がインストールされていないため、アプリ内レビューは利用できません。",
        )
    return PySideReviewAvailability(True, "PySide6レビュー画面を起動できます。")


def launch_review_window(
    *,
    service: RecorderApplicationService,
    recording_id: str,
) -> int:
    availability = check_pyside6_review_available()
    if not availability.available:
        raise PySideReviewError(availability.message)
    return _launch_review_window(service=service, recording_id=recording_id)


def _launch_review_window(
    *,
    service: RecorderApplicationService,
    recording_id: str,
) -> int:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        from PySide6.QtWidgets import (
            QApplication,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QSlider,
            QVBoxLayout,
            QWidget,
        )
        from PySide6.QtCore import Qt
    except Exception as exc:  # pragma: no cover - depends on local Qt installation
        raise PySideReviewError(f"PySide6レビュー画面の読み込みに失敗しました: {exc}") from exc

    app = QApplication.instance() or QApplication([])
    model = service.get_review_view_model(recording_id)

    window = QMainWindow()
    window.setWindowTitle(f"Master Duel Recorder Lite Review - {recording_id}")
    root = QWidget()
    layout = QVBoxLayout(root)
    title = QLabel(f"{model.recording.recording_id} / {model.video.path.name}")
    layout.addWidget(title)

    video = QVideoWidget()
    layout.addWidget(video, stretch=1)

    player = QMediaPlayer()
    audio = QAudioOutput()
    player.setAudioOutput(audio)
    player.setVideoOutput(video)
    player.setSource(QUrl.fromLocalFile(str(model.video.path)))

    controls = QHBoxLayout()
    play_button = QPushButton("再生")
    stop_button = QPushButton("停止")
    open_external_button = QPushButton("外部で開く")
    marker_button = QPushButton("現在位置にマーカー")
    clip_button = QPushButton("選択位置をクリップ出力")
    controls.addWidget(play_button)
    controls.addWidget(stop_button)
    controls.addWidget(open_external_button)
    controls.addWidget(marker_button)
    controls.addWidget(clip_button)
    layout.addLayout(controls)

    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, max(0, int((model.recording.duration_seconds or 0) * 1000)))
    layout.addWidget(slider)

    timeline = QListWidget()
    for event in model.timeline:
        item = QListWidgetItem(
            f"{event.elapsed_label}  {event.event_type}  {event.label} ({event.status})"
        )
        item.setData(Qt.ItemDataRole.UserRole, event.elapsed_ms)
        timeline.addItem(item)
    layout.addWidget(timeline)

    def selected_elapsed_ms() -> int:
        item = timeline.currentItem()
        if item is None:
            return max(0, int(player.position()))
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value)

    def report_error(message: str) -> None:
        QMessageBox.warning(window, "レビュー操作に失敗しました", message)

    def add_marker() -> None:
        try:
            service.add_review_marker(
                ReviewMarkerRequest(
                    recording_id=recording_id,
                    elapsed_ms=max(0, int(player.position())),
                    label="レビューで追加",
                )
            )
        except Exception as exc:
            report_error(str(exc))

    def export_clip() -> None:
        try:
            result = service.export_review_clip(
                ReviewClipExportRequest(
                    recording_id=recording_id,
                    center_seconds=selected_elapsed_ms() / 1000,
                )
            )
        except Exception as exc:
            report_error(str(exc))
            return
        QMessageBox.information(window, "クリップ出力", f"出力しました:\n{result.output_path}")

    play_button.clicked.connect(player.play)
    stop_button.clicked.connect(player.pause)
    open_external_button.clicked.connect(lambda: service.play_recording(recording_id))
    marker_button.clicked.connect(add_marker)
    clip_button.clicked.connect(export_clip)
    timeline.itemClicked.connect(lambda item: player.setPosition(int(item.data(Qt.ItemDataRole.UserRole))))
    slider.sliderMoved.connect(player.setPosition)
    player.positionChanged.connect(slider.setValue)
    player.errorOccurred.connect(lambda _error, text: report_error(text or "動画再生に失敗しました"))

    if model.duel.youtube_watch_url:
        youtube_button = QPushButton("YouTubeを開く")
        youtube_button.clicked.connect(lambda: webbrowser.open(model.duel.youtube_watch_url or ""))
        controls.addWidget(youtube_button)

    window.setCentralWidget(root)
    window.resize(980, 720)
    window.show()
    return int(app.exec())


def open_external_player(service: RecorderApplicationService, recording_id: str) -> Path:
    return service.play_recording(recording_id).path
