from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import webbrowser

from .application import RecorderApplicationService
from .review_viewmodel import (
    ReviewClipExportRequest,
    ReviewMarkerRequest,
    ReviewTimelineEvent,
)


class PySideReviewError(RuntimeError):
    """PySide6レビュー画面を起動できない場合のエラーです。"""


@dataclass(frozen=True)
class PySideReviewAvailability:
    available: bool
    message: str


REVIEW_WIDGETS: tuple[str, ...] = (
    "review_window",
    "review_recording_summary",
    "review_duel_summary",
    "review_video",
    "review_play_pause",
    "review_external_player",
    "review_marker_add",
    "review_clip_export",
    "review_position_slider",
    "review_position_label",
    "review_timeline_table",
)


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
    app = _review_application()
    window = create_review_window(service=service, recording_id=recording_id)
    window.show()
    return int(app.exec())


def _review_application() -> object:
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover - depends on local Qt installation
        raise PySideReviewError(f"PySide6レビュー画面の読み込みに失敗しました: {exc}") from exc

    return QApplication.instance() or QApplication([])


def review_timeline_display_row(event: ReviewTimelineEvent) -> tuple[str, str, str, str, str]:
    return (
        event.elapsed_label,
        event.event_type,
        event.status,
        event.label,
        event.source,
    )


def create_review_window(
    *,
    service: RecorderApplicationService,
    recording_id: str,
    parent: object | None = None,
) -> object:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtCore import Qt
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QHBoxLayout,
            QLabel,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QSlider,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:  # pragma: no cover - depends on local Qt installation
        raise PySideReviewError(f"PySide6レビュー画面の読み込みに失敗しました: {exc}") from exc

    model = service.get_review_view_model(recording_id)
    if not model.video.can_play_in_app:
        raise PySideReviewError(
            f"{model.video.suffix or '不明な形式'}はアプリ内再生対象外です。"
        )

    window = QMainWindow(parent)
    window.setObjectName("review_window")
    window.setWindowTitle(f"Master Duel Recorder Lite Review - {recording_id}")
    root = QWidget()
    layout = QVBoxLayout(root)
    title = QLabel(f"{model.recording.recording_id} / {model.video.path.name}")
    title.setObjectName("review_recording_summary")
    layout.addWidget(title)
    duel = model.duel
    duel_summary = QLabel(
        f"戦績: {duel.result or '-'} / {duel.play_order or '-'} / "
        f"{duel.own_deck or '-'} vs {duel.opponent_deck or '-'}"
    )
    duel_summary.setObjectName("review_duel_summary")
    layout.addWidget(duel_summary)

    video = QVideoWidget()
    video.setObjectName("review_video")
    layout.addWidget(video, stretch=1)

    player = QMediaPlayer()
    audio = QAudioOutput()
    player.setAudioOutput(audio)
    player.setVideoOutput(video)
    player.setSource(QUrl.fromLocalFile(str(model.video.path)))

    controls = QHBoxLayout()
    play_button = QPushButton("再生/一時停止")
    play_button.setObjectName("review_play_pause")
    open_external_button = QPushButton("外部で開く")
    open_external_button.setObjectName("review_external_player")
    marker_button = QPushButton("現在位置にマーカー")
    marker_button.setObjectName("review_marker_add")
    clip_button = QPushButton("選択位置をクリップ出力")
    clip_button.setObjectName("review_clip_export")
    controls.addWidget(play_button)
    controls.addWidget(open_external_button)
    controls.addWidget(marker_button)
    controls.addWidget(clip_button)
    layout.addLayout(controls)

    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setObjectName("review_position_slider")
    slider.setRange(0, max(0, int((model.recording.duration_seconds or 0) * 1000)))
    layout.addWidget(slider)
    position_label = QLabel("00:00.000 / --:--.---")
    position_label.setObjectName("review_position_label")
    layout.addWidget(position_label)

    timeline = QTableWidget(0, 5)
    timeline.setObjectName("review_timeline_table")
    timeline.setHorizontalHeaderLabels(("経過", "種別", "状態", "ラベル", "由来"))
    timeline.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    timeline.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    timeline.verticalHeader().setVisible(False)
    timeline.horizontalHeader().setStretchLastSection(True)

    def reload_timeline() -> None:
        refreshed = service.get_review_view_model(recording_id)
        timeline.setRowCount(0)
        for event in refreshed.timeline:
            row = timeline.rowCount()
            timeline.insertRow(row)
            for column, value in enumerate(review_timeline_display_row(event)):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, event.elapsed_ms)
                timeline.setItem(row, column, item)

    reload_timeline()
    layout.addWidget(timeline)

    def selected_elapsed_ms() -> int:
        item = timeline.item(timeline.currentRow(), 0)
        if item is None:
            return max(0, int(player.position()))
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value)

    def report_error(message: str) -> None:
        QMessageBox.warning(window, "レビュー操作に失敗しました", message)

    def open_external_after_error(message: str) -> None:
        try:
            service.play_recording(recording_id)
        except Exception as exc:
            report_error(f"{message}\n外部プレイヤー起動にも失敗しました: {exc}")
            return
        report_error(f"{message}\n外部プレイヤーで開きました。")

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
            return
        reload_timeline()

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

    def toggle_play_pause() -> None:
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        else:
            player.play()

    def update_position_label(position_ms: int) -> None:
        duration_ms = max(slider.maximum(), int(player.duration()))
        position_label.setText(f"{_position_label(position_ms)} / {_position_label(duration_ms)}")

    def seek_to_timeline_row(row: int) -> None:
        item = timeline.item(row, 0)
        if item is None:
            return
        player.setPosition(int(item.data(Qt.ItemDataRole.UserRole)))

    play_button.clicked.connect(toggle_play_pause)
    open_external_button.clicked.connect(lambda: service.play_recording(recording_id))
    marker_button.clicked.connect(add_marker)
    clip_button.clicked.connect(export_clip)
    timeline.cellClicked.connect(lambda row, _column: seek_to_timeline_row(row))
    slider.sliderMoved.connect(player.setPosition)
    player.positionChanged.connect(slider.setValue)
    player.positionChanged.connect(update_position_label)
    player.durationChanged.connect(lambda value: slider.setMaximum(max(0, int(value))))
    player.errorOccurred.connect(
        lambda _error, text: open_external_after_error(text or "動画再生に失敗しました")
    )

    if model.duel.youtube_watch_url:
        youtube_button = QPushButton("YouTubeを開く")
        youtube_button.clicked.connect(lambda: webbrowser.open(model.duel.youtube_watch_url or ""))
        controls.addWidget(youtube_button)

    window.setCentralWidget(root)
    window.resize(980, 720)
    update_position_label(0)
    return window


def open_external_player(service: RecorderApplicationService, recording_id: str) -> Path:
    return service.play_recording(recording_id).path


def _position_label(elapsed_ms: int) -> str:
    total_seconds = max(0, elapsed_ms) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{elapsed_ms % 1000:03d}"
