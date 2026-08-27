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
    ReviewVisualTimelineItem,
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
    "review_marker_edit",
    "review_clip_export",
    "review_position_slider",
    "review_position_label",
    "review_visual_timeline",
    "review_timeline_table",
)

REVIEW_EVENT_TYPE_LABELS: dict[str, str] = {
    "marker": "マーカー",
    "duel_start": "対戦開始",
    "duel_end": "対戦終了",
    "duel_result": "対戦結果",
    "turn_change": "ターン切替",
    "recording_start": "録画開始",
    "recording_end": "録画終了",
}

REVIEW_EVENT_STATUS_LABELS: dict[str, str] = {
    "candidate": "候補",
    "confirmed": "確定",
    "rejected": "除外",
    "manual": "手動",
}

REVIEW_EVENT_SOURCE_LABELS: dict[str, str] = {
    "manual": "手動",
    "visual": "自動判定",
    "auto": "自動判定",
    "import": "取込",
    "system": "システム",
}


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
        REVIEW_EVENT_TYPE_LABELS.get(event.event_type, event.event_type or "-"),
        REVIEW_EVENT_STATUS_LABELS.get(event.status, event.status or "-"),
        event.label,
        REVIEW_EVENT_SOURCE_LABELS.get(event.source, event.source or "-"),
    )


def review_visual_timeline_contract() -> dict[str, object]:
    return {
        "widget": "review_visual_timeline",
        "source": "ReviewViewModel.visual_timeline",
        "kinds": ["duel_start", "manual_marker", "clip_candidate", "timeline_event"],
        "sync": ["current_position", "selected_event", "timeline_table"],
        "fallback_safe": True,
    }


def review_operation_error_message(operation: str, error: Exception) -> str:
    detail = str(error).strip()
    if "Unable to choose an output format" in detail or "Invalid argument" in detail:
        return (
            f"{operation}に失敗しました。出力ファイル名または保存形式を確認できませんでした。"
            "アプリを最新版に更新し、もう一度実行してください。"
        )
    if not detail:
        return f"{operation}に失敗しました。"
    compact = " ".join(detail.split())
    if len(compact) > 240:
        compact = compact[:237] + "..."
    return f"{operation}に失敗しました。\n{compact}"


def create_review_window(
    *,
    service: RecorderApplicationService,
    recording_id: str,
    parent: object | None = None,
) -> object:
    try:
        from PySide6.QtCore import QSize, QUrl
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPainter, QPen
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QHBoxLayout,
            QInputDialog,
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

    class ReviewVisualTimelineWidget(QWidget):
        def __init__(self, items: tuple[ReviewVisualTimelineItem, ...]) -> None:
            super().__init__()
            self.setObjectName("review_visual_timeline")
            self.setMinimumHeight(54)
            self.setToolTip("録画内のイベント位置")
            self._items = items
            self._current_ms = 0
            self._selected_event_id: str | None = None
            self._on_item_selected: object | None = None

        def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
            return QSize(760, 54)

        def set_items(self, items: tuple[ReviewVisualTimelineItem, ...]) -> None:
            self._items = items
            if self._selected_event_id not in {item.event_id for item in items}:
                self._selected_event_id = None
            self.update()

        def set_current_position(self, position_ms: int) -> None:
            self._current_ms = max(0, int(position_ms))
            self.update()

        def set_selected_event(self, event_id: str | None) -> None:
            self._selected_event_id = event_id
            self.update()

        def set_item_selected_callback(self, callback: object) -> None:
            self._on_item_selected = callback

        def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt override
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            left = 16
            right = max(left + 1, self.width() - 16)
            y = self.height() // 2
            painter.setPen(QPen(QColor("#9ca3af"), 3))
            painter.drawLine(left, y, right, y)
            current_ratio = self._current_position_ratio()
            current_x = left + int((right - left) * current_ratio)
            painter.setPen(QPen(QColor("#111827"), 2))
            painter.drawLine(current_x, y - 18, current_x, y + 18)
            for item in self._items:
                x = left + int((right - left) * item.ratio)
                radius = 7 if item.event_id == self._selected_event_id else 5
                color = _visual_timeline_color(item.kind, item.in_range)
                painter.setBrush(color)
                painter.setPen(QPen(QColor("#111827"), 1))
                painter.drawEllipse(x - radius, y - radius, radius * 2, radius * 2)
            painter.end()

        def mousePressEvent(self, event: object) -> None:  # noqa: N802 - Qt override
            selected = self._nearest_item(int(event.position().x()))
            if selected is None:
                return
            self._selected_event_id = selected.event_id
            self.setToolTip(selected.tooltip)
            self.update()
            if callable(self._on_item_selected):
                self._on_item_selected(selected)

        def _nearest_item(self, x_position: int) -> ReviewVisualTimelineItem | None:
            if not self._items:
                return None
            left = 16
            right = max(left + 1, self.width() - 16)
            positioned = (
                (abs(x_position - (left + int((right - left) * item.ratio))), item)
                for item in self._items
            )
            distance, item = min(positioned, key=lambda pair: pair[0])
            return item if distance <= 14 else None

        def _current_position_ratio(self) -> float:
            maximum = max(0, int(slider.maximum()))
            if maximum <= 0:
                return 0.0
            return min(1.0, max(0.0, self._current_ms / maximum))

    def _visual_timeline_color(kind: str, in_range: bool) -> QColor:
        if not in_range:
            return QColor("#9ca3af")
        colors = {
            "duel_start": QColor("#16a34a"),
            "manual_marker": QColor("#2563eb"),
            "clip_candidate": QColor("#f59e0b"),
            "timeline_event": QColor("#6b7280"),
        }
        return colors.get(kind, QColor("#6b7280"))

    controls = QHBoxLayout()
    play_button = QPushButton("再生/一時停止")
    play_button.setObjectName("review_play_pause")
    open_external_button = QPushButton("外部で開く")
    open_external_button.setObjectName("review_external_player")
    marker_button = QPushButton("現在位置にマーカー")
    marker_button.setObjectName("review_marker_add")
    marker_edit_button = QPushButton("マーカー編集")
    marker_edit_button.setObjectName("review_marker_edit")
    clip_button = QPushButton("選択位置をクリップ出力")
    clip_button.setObjectName("review_clip_export")
    controls.addWidget(play_button)
    controls.addWidget(open_external_button)
    controls.addWidget(marker_button)
    controls.addWidget(marker_edit_button)
    controls.addWidget(clip_button)
    layout.addLayout(controls)

    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setObjectName("review_position_slider")
    slider.setRange(0, max(0, int((model.recording.duration_seconds or 0) * 1000)))
    layout.addWidget(slider)
    position_label = QLabel("00:00.000 / --:--.---")
    position_label.setObjectName("review_position_label")
    layout.addWidget(position_label)
    visual_timeline = ReviewVisualTimelineWidget(model.visual_timeline)
    layout.addWidget(visual_timeline)

    timeline = QTableWidget(0, 5)
    timeline.setObjectName("review_timeline_table")
    timeline.setHorizontalHeaderLabels(("経過", "種別", "状態", "ラベル", "由来"))
    timeline.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    timeline.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    timeline.verticalHeader().setVisible(False)
    timeline.horizontalHeader().setStretchLastSection(True)
    event_id_role = Qt.ItemDataRole.UserRole + 1
    event_type_role = Qt.ItemDataRole.UserRole + 2

    def reload_timeline() -> None:
        refreshed = service.get_review_view_model(recording_id)
        visual_timeline.set_items(refreshed.visual_timeline)
        timeline.setRowCount(0)
        for event in refreshed.timeline:
            row = timeline.rowCount()
            timeline.insertRow(row)
            for column, value in enumerate(review_timeline_display_row(event)):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, event.elapsed_ms)
                    item.setData(event_id_role, event.event_id)
                if column == 1:
                    item.setData(event_type_role, event.event_type)
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
            report_error(review_operation_error_message("マーカー追加", exc))
            return
        reload_timeline()

    def edit_marker() -> None:
        row = timeline.currentRow()
        type_item = timeline.item(row, 1)
        label_item = timeline.item(row, 3)
        elapsed_item = timeline.item(row, 0)
        if type_item is None or label_item is None or elapsed_item is None:
            report_error("編集するマーカーを選択してください。")
            return
        if type_item.data(event_type_role) != "marker":
            report_error("マーカー行だけを編集できます。")
            return
        event_id = elapsed_item.data(event_id_role)
        label, accepted = QInputDialog.getText(
            window,
            "マーカー編集",
            "ラベル",
            text=label_item.text(),
        )
        if not accepted:
            return
        try:
            service.update_review_marker_label(str(event_id), label)
        except Exception as exc:
            report_error(review_operation_error_message("マーカー編集", exc))
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
            report_error(review_operation_error_message("クリップ出力", exc))
            return
        QMessageBox.information(window, "クリップ出力", f"出力しました:\n{result.output_path}")

    def open_external_player_window() -> None:
        try:
            service.play_recording(recording_id)
        except Exception as exc:
            report_error(review_operation_error_message("外部プレイヤー起動", exc))

    def toggle_play_pause() -> None:
        if player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            player.pause()
        else:
            player.play()

    def update_position_label(position_ms: int) -> None:
        duration_ms = max(slider.maximum(), int(player.duration()))
        position_label.setText(f"{_position_label(position_ms)} / {_position_label(duration_ms)}")
        visual_timeline.set_current_position(position_ms)

    def seek_to_timeline_row(row: int) -> None:
        item = timeline.item(row, 0)
        if item is None:
            return
        visual_timeline.set_selected_event(str(item.data(event_id_role)))
        player.setPosition(int(item.data(Qt.ItemDataRole.UserRole)))

    def select_timeline_event(item: ReviewVisualTimelineItem) -> None:
        for row in range(timeline.rowCount()):
            event_id_item = timeline.item(row, 0)
            if event_id_item is None:
                continue
            if event_id_item.data(event_id_role) == item.event_id:
                timeline.selectRow(row)
                break
        player.setPosition(item.elapsed_ms)

    play_button.clicked.connect(toggle_play_pause)
    open_external_button.clicked.connect(open_external_player_window)
    marker_button.clicked.connect(add_marker)
    marker_edit_button.clicked.connect(edit_marker)
    clip_button.clicked.connect(export_clip)
    timeline.cellClicked.connect(lambda row, _column: seek_to_timeline_row(row))
    visual_timeline.set_item_selected_callback(select_timeline_event)
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
