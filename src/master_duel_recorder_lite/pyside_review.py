from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import os
from pathlib import Path
import webbrowser

from .application import RecorderApplicationService
from .clip_export import resolve_clip_range
from .duel_records import DuelRecordValues, duel_choice_label
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
    "review_timeline_confirm",
    "review_timeline_reject",
    "review_clip_export",
    "review_clip_hint",
    "review_clip_open_folder",
    "review_position_slider",
    "review_position_label",
    "review_visual_timeline",
    "review_editor_tabs",
    "review_marker_tab",
    "review_duel_tab",
    "review_duel_save",
    "review_timeline_table",
)

MARKER_KIND_CHOICES: tuple[str, ...] = (
    "メモ",
    "重要局面",
    "プレミ",
    "ターン判断",
    "リーサル",
    "その他",
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


def review_timeline_display_row(event: ReviewTimelineEvent) -> tuple[str, str, str, str]:
    event_type = REVIEW_EVENT_TYPE_LABELS.get(event.event_type, event.event_type or "-")
    description = event.label
    if event.event_type == "marker":
        event_type, description = split_marker_label(event.label)
    return (
        event.elapsed_label,
        event_type,
        REVIEW_EVENT_STATUS_LABELS.get(event.status, event.status or "-"),
        description,
    )


def split_marker_label(label: str) -> tuple[str, str]:
    normalized = label.strip()
    for kind in MARKER_KIND_CHOICES:
        prefix = f"{kind}:"
        if normalized.startswith(prefix):
            return kind, normalized[len(prefix) :].strip() or kind
    return "メモ", normalized or "レビューで追加"


def compose_marker_label(kind: str, description: str) -> str:
    normalized_kind = kind.strip() if kind.strip() in MARKER_KIND_CHOICES else "メモ"
    normalized_description = description.strip() or normalized_kind
    return f"{normalized_kind}: {normalized_description}"


def review_clip_range_message(
    *,
    center_seconds: float,
    duration_seconds: float | None,
    before_seconds: float = 30.0,
    after_seconds: float = 30.0,
) -> str:
    clip_range = resolve_clip_range(
        center_seconds=center_seconds,
        duration_seconds=duration_seconds,
        before_seconds=before_seconds,
        after_seconds=after_seconds,
    )
    return (
        "選択位置を中心に前30秒・後30秒を出力します。"
        f"実際の出力は{_seconds_label(clip_range.start_seconds)}から"
        f"{_seconds_label(clip_range.duration_seconds)}分です。"
    )


def review_visual_timeline_contract() -> dict[str, object]:
    return {
        "widget": "review_visual_timeline",
        "source": "ReviewViewModel.visual_timeline",
        "kinds": ["duel_start", "manual_marker", "clip_candidate", "timeline_event"],
        "sync": ["current_position", "selected_event", "timeline_table"],
        "fallback_safe": True,
        "tabs": ["マーカー編集", "戦績入力"],
        "source_column_visible": False,
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
    initial_tab: str = "marker",
) -> object:
    try:
        from PySide6.QtCore import QSize, QUrl
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPainter, QPen
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QButtonGroup,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QSlider,
            QTabWidget,
            QTableWidget,
            QTableWidgetItem,
            QTextEdit,
            QVBoxLayout,
            QWidget,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QGridLayout,
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
    window.setStyleSheet(_review_style_sheet())
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
    video.setMinimumHeight(260)
    video.setMaximumHeight(420)
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
    controls.addWidget(play_button)
    controls.addWidget(open_external_button)
    controls.addStretch(1)
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

    tabs = QTabWidget()
    tabs.setObjectName("review_editor_tabs")
    marker_tab = QWidget()
    marker_tab.setObjectName("review_marker_tab")
    marker_layout = QVBoxLayout(marker_tab)
    marker_controls = QHBoxLayout()
    marker_button = QPushButton("現在位置にマーカー")
    marker_button.setObjectName("review_marker_add")
    marker_edit_button = QPushButton("マーカー編集")
    marker_edit_button.setObjectName("review_marker_edit")
    confirm_button = QPushButton("候補を確定")
    confirm_button.setObjectName("review_timeline_confirm")
    reject_button = QPushButton("候補を却下")
    reject_button.setObjectName("review_timeline_reject")
    clip_button = QPushButton("選択位置をクリップ出力")
    clip_button.setObjectName("review_clip_export")
    clip_folder_button = QPushButton("保存先を開く")
    clip_folder_button.setObjectName("review_clip_open_folder")
    clip_folder_button.setEnabled(False)
    for button in (
        marker_button,
        marker_edit_button,
        confirm_button,
        reject_button,
        clip_button,
        clip_folder_button,
    ):
        marker_controls.addWidget(button)
    marker_controls.addStretch(1)
    marker_layout.addLayout(marker_controls)
    clip_hint = QLabel(
        "クリップ出力は、選択行または現在位置を中心に前30秒・後30秒を保存します。"
    )
    clip_hint.setObjectName("review_clip_hint")
    clip_hint.setWordWrap(True)
    marker_layout.addWidget(clip_hint)

    timeline = QTableWidget(0, 4)
    timeline.setObjectName("review_timeline_table")
    timeline.setHorizontalHeaderLabels(("経過", "種別", "状態", "説明"))
    timeline.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    timeline.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    timeline.verticalHeader().setVisible(False)
    timeline.horizontalHeader().setStretchLastSection(True)
    marker_layout.addWidget(timeline)
    tabs.addTab(marker_tab, "マーカー編集")

    duel_tab = QWidget()
    duel_tab.setObjectName("review_duel_tab")
    duel_layout = QVBoxLayout(duel_tab)
    duel_layout.setContentsMargins(10, 8, 10, 8)
    duel_layout.setSpacing(8)
    compact_row = QWidget()
    compact_layout = QHBoxLayout(compact_row)
    compact_layout.setContentsMargins(0, 0, 0, 0)
    compact_layout.setSpacing(8)
    duel_grid = QGridLayout()
    duel_grid.setColumnStretch(1, 1)
    duel_grid.setColumnStretch(3, 1)
    duel_grid.setVerticalSpacing(8)
    duel_editor_data = service.get_duel_editor_data(recording_id)

    def segmented_choice(
        label: str,
        field: str,
        choices: tuple[str, ...],
        current: str,
    ) -> QButtonGroup:
        compact_layout.addWidget(QLabel(label))
        group = QButtonGroup(window)
        group.setExclusive(True)
        for choice in choices:
            button = QPushButton(duel_choice_label(field, choice))
            button.setCheckable(True)
            button.setProperty("segmentButton", True)
            button.setProperty("choiceData", choice)
            if choice == current:
                button.setChecked(True)
            group.addButton(button)
            compact_layout.addWidget(button)
        if group.checkedButton() is None and group.buttons():
            group.buttons()[0].setChecked(True)
        return group

    def choice_combo(
        row: int,
        column: int,
        label: str,
        field: str,
        choices: tuple[str, ...],
        current: str,
    ) -> QComboBox:
        duel_grid.addWidget(QLabel(label), row, column)
        combo = QComboBox()
        for choice in choices:
            combo.addItem(duel_choice_label(field, choice), choice)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        duel_grid.addWidget(combo, row, column + 1)
        return combo

    def editable_deck_combo(current: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        names: list[str] = []
        for deck in duel_editor_data.decks:
            name = str(getattr(deck, "name", "")).strip()
            if name and name not in names:
                names.append(name)
        combo.addItems(names)
        combo.setCurrentText(current)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setToolTip("登録済みデッキを選ぶか、そのまま自由に入力できます")
        return combo

    duel_values = duel_editor_data.values
    status_group = segmented_choice(
        "状態", "status", ("draft", "confirmed"), duel_values.status
    )
    result_group = segmented_choice(
        "勝敗", "result", ("unknown", "win", "loss", "draw"), duel_values.result
    )
    order_group = segmented_choice(
        "先後", "play_order", ("unknown", "first", "second"), duel_values.play_order
    )
    coin_group = segmented_choice(
        "コイン", "coin_face", ("unknown", "heads", "tails"), duel_values.coin_face
    )
    compact_layout.addStretch(1)
    duel_layout.addWidget(compact_row)
    type_combo = choice_combo(
        0,
        0,
        "対戦種別",
        "duel_type",
        ("other", "ranked", "event", "room", "solo"),
        duel_values.duel_type,
    )
    duel_grid.addWidget(QLabel("シーズン"), 0, 2)
    season_combo = QComboBox()
    season_combo.addItem("未設定", None)
    for season in duel_editor_data.seasons:
        season_combo.addItem(str(getattr(season, "name", "")), getattr(season, "season_id", None))
    season_index = season_combo.findData(duel_values.season_id)
    season_combo.setCurrentIndex(season_index if season_index >= 0 else 0)
    duel_grid.addWidget(season_combo, 0, 3)
    duel_grid.addWidget(QLabel("自分デッキ"), 1, 0)
    own_deck = editable_deck_combo(duel_values.own_deck)
    duel_grid.addWidget(own_deck, 1, 1, 1, 3)
    duel_grid.addWidget(QLabel("相手デッキ"), 2, 0)
    opponent_deck = editable_deck_combo(duel_values.opponent_deck)
    duel_grid.addWidget(opponent_deck, 2, 1, 1, 3)
    duel_grid.addWidget(QLabel("タグ"), 3, 0)
    tags = QLineEdit(", ".join(duel_values.tags))
    tags.setToolTip("複数タグはカンマ区切りで入力します")
    duel_grid.addWidget(tags, 3, 1, 1, 3)
    duel_layout.addLayout(duel_grid)
    duel_layout.addWidget(QLabel("メモ"))
    notes = QTextEdit()
    notes.setPlainText(duel_values.notes)
    notes.setMinimumHeight(80)
    duel_layout.addWidget(notes)
    duel_save = QPushButton("戦績を保存")
    duel_save.setObjectName("review_duel_save")
    duel_layout.addWidget(duel_save)
    tabs.addTab(duel_tab, "戦績入力")
    if initial_tab == "duel":
        tabs.setCurrentWidget(duel_tab)
    layout.addWidget(tabs, stretch=1)

    event_id_role = Qt.ItemDataRole.UserRole + 1
    event_type_role = Qt.ItemDataRole.UserRole + 2
    event_source_role = Qt.ItemDataRole.UserRole + 3
    event_status_role = Qt.ItemDataRole.UserRole + 4
    event_label_role = Qt.ItemDataRole.UserRole + 5
    last_clip_output_path: Path | None = None

    def refresh_duel_summary() -> None:
        duel = model.duel
        duel_summary.setText(
            f"戦績: {duel_choice_label('result', duel.result)} / "
            f"{duel_choice_label('play_order', duel.play_order)} / "
            f"{duel.own_deck or '-'} vs {duel.opponent_deck or '-'}"
        )

    def reload_timeline() -> None:
        nonlocal model
        refreshed = service.get_review_view_model(recording_id)
        model = refreshed
        refresh_duel_summary()
        visual_timeline.set_items(refreshed.visual_timeline)
        timeline.setRowCount(0)
        for event in refreshed.timeline:
            row = timeline.rowCount()
            timeline.insertRow(row)
            for column, value in enumerate(review_timeline_display_row(event)):
                item = QTableWidgetItem(value)
                item.setData(event_id_role, event.event_id)
                item.setData(event_type_role, event.event_type)
                item.setData(event_source_role, event.source)
                item.setData(event_status_role, event.status)
                item.setData(event_label_role, event.label)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, event.elapsed_ms)
                timeline.setItem(row, column, item)
        update_timeline_action_state()

    def selected_elapsed_ms() -> int:
        item = timeline.item(timeline.currentRow(), 0)
        if item is None:
            return max(0, int(player.position()))
        value = item.data(Qt.ItemDataRole.UserRole)
        return int(value)

    def selected_timeline_event() -> tuple[str, str, str, str, str] | None:
        item = timeline.item(timeline.currentRow(), 0)
        if item is None:
            return None
        event_id = item.data(event_id_role)
        event_type = item.data(event_type_role)
        source = item.data(event_source_role)
        status = item.data(event_status_role)
        label = item.data(event_label_role)
        return str(event_id), str(event_type), str(source), str(status), str(label)

    def update_timeline_action_state() -> None:
        selected = selected_timeline_event()
        is_candidate = selected is not None and selected[3] == "candidate"
        is_manual_marker = (
            selected is not None and selected[1] == "marker" and selected[2] == "manual"
        )
        confirm_button.setEnabled(is_candidate)
        reject_button.setEnabled(is_candidate)
        marker_edit_button.setEnabled(is_manual_marker)

    reload_timeline()

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
                    label=compose_marker_label("メモ", "レビューで追加"),
                )
            )
        except Exception as exc:
            report_error(review_operation_error_message("マーカー追加", exc))
            return
        reload_timeline()

    def edit_marker() -> None:
        selected = selected_timeline_event()
        if selected is None:
            report_error("編集するマーカーを選択してください。")
            return
        event_id, event_type, source, _status, label = selected
        if event_type != "marker":
            report_error("マーカー行だけを編集できます。")
            return
        if source != "manual":
            report_error("自動判定のラベルは編集できません。候補は確定/却下で整理してください。")
            return
        current_kind, current_description = split_marker_label(label)
        dialog = QDialog(window)
        dialog.setWindowTitle("マーカー編集")
        editor_layout = QVBoxLayout(dialog)
        marker_grid = QGridLayout()
        marker_grid.addWidget(QLabel("種別"), 0, 0)
        kind_combo = QComboBox()
        kind_combo.addItems(MARKER_KIND_CHOICES)
        kind_combo.setCurrentText(current_kind)
        marker_grid.addWidget(kind_combo, 0, 1)
        marker_grid.addWidget(QLabel("説明"), 1, 0)
        description = QLineEdit(current_description)
        marker_grid.addWidget(description, 1, 1)
        editor_layout.addLayout(marker_grid)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if save_button is not None:
            save_button.setText("保存")
        if cancel_button is not None:
            cancel_button.setText("キャンセル")
        editor_layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            service.update_review_marker_label(
                event_id,
                compose_marker_label(kind_combo.currentText(), description.text()),
            )
        except Exception as exc:
            report_error(review_operation_error_message("マーカー編集", exc))
            return
        reload_timeline()

    def confirm_selected_event() -> None:
        selected = selected_timeline_event()
        if selected is None:
            report_error("確定する候補イベントを選択してください。")
            return
        try:
            service.confirm_timeline_event(selected[0])
        except Exception as exc:
            report_error(review_operation_error_message("候補確定", exc))
            return
        reload_timeline()

    def reject_selected_event() -> None:
        selected = selected_timeline_event()
        if selected is None:
            report_error("却下する候補イベントを選択してください。")
            return
        try:
            service.reject_timeline_event(selected[0])
        except Exception as exc:
            report_error(review_operation_error_message("候補却下", exc))
            return
        reload_timeline()

    def open_clip_folder() -> None:
        if last_clip_output_path is None:
            return
        try:
            _open_folder(last_clip_output_path.parent)
        except Exception as exc:
            report_error(review_operation_error_message("保存先を開く", exc))

    def export_clip() -> None:
        nonlocal last_clip_output_path
        center_seconds = selected_elapsed_ms() / 1000
        range_message = review_clip_range_message(
            center_seconds=center_seconds,
            duration_seconds=model.recording.duration_seconds,
        )
        if QMessageBox.question(
            window,
            "クリップ出力",
            range_message + "\n\nこの範囲で出力しますか？",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            result = service.export_review_clip(
                ReviewClipExportRequest(
                    recording_id=recording_id,
                    center_seconds=center_seconds,
                )
            )
        except Exception as exc:
            report_error(review_operation_error_message("クリップ出力", exc))
            return
        last_clip_output_path = result.output_path
        clip_folder_button.setEnabled(True)
        box = QMessageBox(window)
        box.setWindowTitle("クリップ出力")
        box.setText("出力しました。")
        box.setInformativeText(f"{range_message}\n\n保存先:\n{result.output_path}")
        open_button = box.addButton("エクスプローラーで開く", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() == open_button:
            open_clip_folder()

    def save_review_duel() -> None:
        nonlocal duel_editor_data, model
        selected_season = season_combo.currentData()
        selected_tags = tuple(
            part.strip()
            for part in tags.text().replace("、", ",").split(",")
            if part.strip()
        )
        values = DuelRecordValues(
            status=selected_segment_value(status_group),
            result=selected_segment_value(result_group),
            play_order=selected_segment_value(order_group),
            coin_face=selected_segment_value(coin_group),
            own_deck=own_deck.currentText(),
            opponent_deck=opponent_deck.currentText(),
            duel_type=str(type_combo.currentData()),
            tags=selected_tags,
            notes=notes.toPlainText(),
            season_id=int(selected_season) if selected_season is not None else None,
        )
        try:
            if duel_editor_data.record is not None:
                service.update_duel_record(
                    duel_editor_data.record.duel_id,
                    values,
                    expected_revision=duel_editor_data.record.revision,
                )
            else:
                service.save_duel_record(recording_id, values, expected_revision=0)
            duel_editor_data = service.get_duel_editor_data(recording_id)
            model = service.get_review_view_model(recording_id)
        except Exception as exc:
            report_error(review_operation_error_message("戦績保存", exc))
            return
        refresh_duel_summary()
        QMessageBox.information(window, "戦績入力", "戦績を保存しました。")

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
        update_timeline_action_state()

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
    confirm_button.clicked.connect(confirm_selected_event)
    reject_button.clicked.connect(reject_selected_event)
    clip_button.clicked.connect(export_clip)
    clip_folder_button.clicked.connect(open_clip_folder)
    duel_save.clicked.connect(save_review_duel)
    timeline.cellClicked.connect(lambda row, _column: seek_to_timeline_row(row))
    timeline.itemSelectionChanged.connect(update_timeline_action_state)
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
    window.resize(1120, 780)
    update_position_label(0)
    return window


def selected_segment_value(group: object) -> str:
    button = group.checkedButton()
    if button is None:
        return ""
    return str(button.property("choiceData"))


def open_external_player(service: RecorderApplicationService, recording_id: str) -> Path:
    return service.play_recording(recording_id).path


def _position_label(elapsed_ms: int) -> str:
    total_seconds = max(0, elapsed_ms) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{seconds:02d}.{elapsed_ms % 1000:03d}"


def _seconds_label(seconds: float) -> str:
    whole = max(0, int(seconds))
    minutes, remainder = divmod(whole, 60)
    return f"{minutes:02d}:{remainder:02d}"


def _open_folder(path: Path) -> None:
    directory = path.expanduser().resolve()
    if hasattr(os, "startfile"):
        os.startfile(str(directory))  # type: ignore[attr-defined]
        return
    webbrowser.open(directory.as_uri())


def _review_style_sheet() -> str:
    return """
    * {
        font-family: "Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic", "Segoe UI";
        font-size: 10pt;
    }
    QMainWindow { background: #f4f7f5; color: #111827; }
    QLabel { color: #111827; }
    QPushButton {
        min-height: 34px;
        padding: 6px 12px;
        border: 1px solid #b8c1cc;
        border-radius: 6px;
        background: #f9fafb;
        color: #111827;
    }
    QPushButton[segmentButton="true"] {
        min-height: 28px;
        padding: 4px 9px;
        border-radius: 4px;
    }
    QPushButton[segmentButton="true"]:checked {
        background: #007c7a;
        color: #ffffff;
        border-color: #006665;
        font-weight: 700;
    }
    QComboBox, QLineEdit {
        min-height: 34px;
        border: 1px solid #c8d0d8;
        border-radius: 4px;
        background: #ffffff;
        padding: 4px 8px;
    }
    QTableWidget, QTextEdit {
        background: #ffffff;
        border: 1px solid #c8d0d8;
        alternate-background-color: #f7faf9;
        selection-background-color: #d7ece8;
        selection-color: #10201c;
        gridline-color: #e3e8ed;
    }
    QHeaderView::section {
        background: #eef2f0;
        border: 1px solid #c8d0d8;
        padding: 5px;
        font-weight: 700;
    }
    QTabWidget::pane { border: 1px solid #d0d7de; background: #ffffff; }
    QTabBar::tab {
        background: #edf2f1;
        padding: 8px 12px;
        border: 1px solid #d0d7de;
    }
    QTabBar::tab:selected { background: #ffffff; color: #007c7a; font-weight: 700; }
    """
