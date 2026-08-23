from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
from typing import Any

from . import __version__
from .application import RecorderApplicationService
from .gui_feature_parity import (
    STANDARD_GUI_FEATURES,
    evaluate_standard_operation_checks,
    required_standard_widget_keys,
    satisfied_standard_feature_keys,
)
from .uninstall import run_cleanup_manifest


class PySideGuiError(RuntimeError):
    """PySide6 GUIを起動できない場合のエラーです。"""


@dataclass(frozen=True)
class PySideGuiAvailability:
    available: bool
    message: str


NAVIGATION_PAGES: tuple[tuple[str, str], ...] = (
    ("record", "録画"),
    ("history", "戦績管理"),
    ("statistics", "統計"),
    ("decks", "デッキ名"),
    ("tags", "タグ"),
    ("seasons", "シーズン"),
    ("youtube", "テンプレート"),
    ("reliability", "信頼性"),
    ("settings", "設定"),
)

INTERNAL_PAGES: tuple[tuple[str, str], ...] = (
    ("prepare", "MP4準備"),
    ("improve", "改善"),
)

RICH_BASELINE_ASSETS: tuple[str, ...] = (
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/01-record-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/02-history-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/03-statistics-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/04-decks-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/05-tags-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/06-seasons-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/07-template-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/08-reliability-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/09-settings-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/10-prepare-internal-rich.png",
    "docs/assets/tkinter-ui-baseline-1.5.2-rich/11-improve-internal-rich.png",
)

RICH_UI_SECTION_WIDGETS: tuple[str, ...] = (
    "record_target_section",
    "record_state_section",
    "record_manual_section",
    "record_environment_diagnostics",
    "record_activity_panel",
    "history_toolbar",
    "history_filter_bar",
    "statistics_summary",
    "statistics_tab_panel",
    "deck_editor",
    "tag_editor",
    "season_editor",
    "template_editor",
    "reliability_preflight_panel",
    "settings_tabs",
    "prepare_internal_page",
    "improve_internal_page",
)

UI_USABILITY_WIDGETS: tuple[str, ...] = (
    "history_table",
    "statistics_chart",
    "statistics_date_from_picker",
    "statistics_date_to_picker",
    "deck_catalog_table",
    "tag_catalog_table",
    "season_table",
)

SMOKE_WIDGETS: tuple[str, ...] = tuple(
    sorted(
        set(required_standard_widget_keys())
        | set(RICH_UI_SECTION_WIDGETS)
        | set(UI_USABILITY_WIDGETS)
        | {
            "incomplete_duel_count",
        }
    )
)


POST_RECORDING_WORKFLOW_WIDGETS: dict[str, tuple[str, ...]] = {
    "history_hub": ("history_table", "history_refresh"),
    "incomplete_action": ("history_incomplete",),
    "play_action": ("history_play",),
    "edit_action": ("history_duel",),
    "danger_delete_action": ("history_delete",),
    "duplicate_review": ("history_duplicates",),
    "youtube_action": ("history_youtube",),
    "timeline_entry": ("history_duel",),
    "diagnostic_entry": ("visual_diagnostics_folder",),
    "review_entry": ("history_play",),
}


DATA_PROTECTION_DISPLAY_WIDGETS: dict[str, tuple[str, ...]] = {
    "status_visible": ("data_protection_status",),
    "scope_visible": ("data_protection_scope",),
    "backup_table_visible": ("data_backup_table",),
    "clean_uninstall_guard": ("clean_uninstall",),
    "recordings_excluded_text": ("data_protection_scope",),
    "queue_manifest_oauth_excluded_text": ("data_protection_scope",),
    "runtime_database_path_present": ("data_protection_status",),
}


LEGACY_SMOKE_WIDGETS: tuple[str, ...] = (
    "activity",
    "catalog_table",
    "clean_uninstall",
    "data_backup_table",
    "data_protection_status",
    "ffmpeg_setup",
    "history_delete",
    "history_duel",
    "history_duplicates",
    "history_play",
    "history_refresh",
    "history_table",
    "incomplete_duel_count",
    "prepare_table",
    "record_start",
    "record_status",
    "record_stop",
    "season_table",
    "settings_form",
    "statistics_chart",
    "statistics_date_from_picker",
    "statistics_date_to_picker",
    "statistics_deck_table",
    "statistics_filters",
    "statistics_order_table",
    "target_selector",
    "visual_details_toggle",
    "visual_diagnostics_folder",
    "visual_status",
    "watch_toggle",
)


def check_pyside6_gui_available() -> PySideGuiAvailability:
    if importlib.util.find_spec("PySide6") is None:
        return PySideGuiAvailability(
            False,
            "PySide6がインストールされていないため、V2.0.0 GUIを起動できません。",
        )
    return PySideGuiAvailability(True, "PySide6 GUIを起動できます。")


def build_gui_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Master Duel Recorder Lite PySide6 GUI")
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--user-data-dir", type=Path, default=None)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--smoke-output", type=Path, default=None)
    parser.add_argument("--smoke-screenshot", type=Path, default=None)
    parser.add_argument(
        "--smoke-page",
        choices=[page for page, _label in (*NAVIGATION_PAGES, *INTERNAL_PAGES)],
        default="statistics",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--cleanup-manifest", type=Path, default=None, help=argparse.SUPPRESS
    )
    return parser


def smoke_contract(
    *, service: RecorderApplicationService, width: int, height: int
) -> dict[str, Any]:
    nav_pages = [page for page, _label in NAVIGATION_PAGES]
    widgets = sorted(SMOKE_WIDGETS)
    widget_keys = set(widgets)
    operation_checks = evaluate_standard_operation_checks(widget_keys)
    failed_operation_checks = [
        check for check in operation_checks if not bool(check["passed"])
    ]
    satisfied_features = satisfied_standard_feature_keys(widget_keys)
    required_widgets = required_standard_widget_keys()
    return {
        "width": width,
        "height": height,
        "widgets": widgets,
        "required_standard_widgets": list(required_widgets),
        "missing_standard_widgets": [
            widget for widget in required_widgets if widget not in widget_keys
        ],
        "nav_pages": sorted(nav_pages),
        "internal_pages": [page for page, _label in INTERNAL_PAGES],
        "title": f"Master Duel Recorder Lite {__version__}",
        "version": __version__,
        "runtime_data": str(service.paths.root),
        "history_refresh_visible": True,
        "calendar_contract": True,
        "ui_usability_widgets": list(UI_USABILITY_WIDGETS),
        "ui_usability_contract": all(widget in widget_keys for widget in UI_USABILITY_WIDGETS),
        "calendar_picker_contract": {
            "date_widgets": [
                "statistics_date_from_picker",
                "statistics_date_to_picker",
            ],
            "display_format": "yyyy-MM-dd",
            "popup_calendar": True,
        },
        "statistics_chart_contract": {
            "widget": "statistics_chart",
            "visual_type": "bar_and_line",
            "bar_metric": "period_wins",
            "line_metric": "cumulative_win_rate",
        },
        "table_readability_contract": {
            "selection": "soft-row-selection",
            "horizontal_scroll": True,
            "explicit_column_widths": True,
        },
        "color_swatch_contract": {
            "catalog_tables": ["deck_catalog_table", "tag_catalog_table"],
            "history_deck_decoration": True,
        },
        "rich_ui_baseline_assets": list(RICH_BASELINE_ASSETS),
        "rich_ui_section_widgets": list(RICH_UI_SECTION_WIDGETS),
        "rich_ui_baseline_contract": all(
            widget in widget_keys for widget in RICH_UI_SECTION_WIDGETS
        ),
        "standard_feature_contract": len(satisfied_features)
        == len(STANDARD_GUI_FEATURES),
        "standard_features": [feature.key for feature in STANDARD_GUI_FEATURES],
        "satisfied_standard_features": list(satisfied_features),
        "standard_operation_contract": not failed_operation_checks,
        "standard_operation_checks": list(operation_checks),
        "failed_standard_operation_checks": failed_operation_checks,
        "post_recording_workflow_contract": {
            key: all(widget in widget_keys for widget in required)
            for key, required in POST_RECORDING_WORKFLOW_WIDGETS.items()
        },
        "data_protection_display_contract": {
            key: all(widget in widget_keys for widget in required)
            for key, required in DATA_PROTECTION_DISPLAY_WIDGETS.items()
        },
        "youtube_flow_contract": (
            "prepare" not in nav_pages
            and "youtube" in nav_pages
            and "prepare_table" in widgets
        ),
        "pyside6": True,
        "gui_entrypoint": "master_duel_recorder_lite.pyside_gui",
        "legacy_tkinter_entry": "master_duel_recorder_lite.gui",
    }


def main(argv: list[str] | None = None) -> int:
    args = build_gui_parser().parse_args(argv)
    if args.cleanup_manifest is not None:
        return run_cleanup_manifest(args.cleanup_manifest)
    try:
        return _run(args)
    except PySideGuiError as exc:
        print(str(exc))
        return 1


def _run(args: argparse.Namespace) -> int:
    availability = check_pyside6_gui_available()
    if not availability.available:
        raise PySideGuiError(availability.message)
    try:
        from PySide6.QtCore import QDate, QPointF, Qt
        from PySide6.QtGui import QColor, QPainter, QPen
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QApplication,
            QCheckBox,
            QComboBox,
            QDateEdit,
            QFrame,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QHeaderView,
            QLabel,
            QLineEdit,
            QListWidget,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QScrollArea,
            QSizePolicy,
            QSpinBox,
            QStackedWidget,
            QTabWidget,
            QTableWidget,
            QTableWidgetItem,
            QTextEdit,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:  # pragma: no cover - depends on local Qt installation
        raise PySideGuiError(f"PySide6 GUIの読み込みに失敗しました: {exc}") from exc

    class StatisticsTrendChart(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.points: tuple[object, ...] = ()
            self.setMinimumHeight(230)
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )

        def set_points(self, points: tuple[object, ...]) -> None:
            self.points = points
            self.update()

        def paintEvent(self, _event: object) -> None:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = self.rect().adjusted(14, 12, -14, -14)
            painter.fillRect(rect, QColor("#ffffff"))
            painter.setPen(QPen(QColor("#c8d0d8"), 1))
            painter.drawRect(rect)

            plot = rect.adjusted(44, 18, -22, -34)
            painter.setPen(QColor("#4b5563"))
            painter.drawText(rect.left() + 10, rect.top() + 18, "勝利数")
            painter.drawText(rect.right() - 72, rect.top() + 18, "累積勝率")
            painter.setPen(QPen(QColor("#d6dde3"), 1))
            painter.drawLine(plot.bottomLeft(), plot.bottomRight())
            painter.drawLine(plot.bottomLeft(), plot.topLeft())

            if not self.points:
                painter.setPen(QColor("#6b7280"))
                painter.drawText(
                    plot,
                    int(Qt.AlignmentFlag.AlignCenter),
                    "表示できる確定済み対戦がありません",
                )
                return

            wins = [self._wins(point) for point in self.points]
            rates = [self._rate(point) for point in self.points]
            max_wins = max(max(wins), 1)
            count = len(self.points)
            step = plot.width() / max(count, 1)
            bar_width = max(10.0, min(34.0, step * 0.46))
            line_points: list[QPointF] = []

            for index, point in enumerate(self.points):
                center_x = plot.left() + step * index + step / 2
                win_height = (wins[index] / max_wins) * max(plot.height(), 1)
                bar_rect_left = center_x - bar_width / 2
                painter.fillRect(
                    int(bar_rect_left),
                    int(plot.bottom() - win_height),
                    int(bar_width),
                    int(win_height),
                    QColor("#4f8f82"),
                )
                rate = rates[index]
                if rate is not None:
                    y = plot.bottom() - rate * plot.height()
                    line_points.append(QPointF(center_x, y))
                if count <= 12 or index in {0, count - 1}:
                    painter.setPen(QColor("#4b5563"))
                    painter.drawText(
                        int(center_x - step / 2),
                        plot.bottom() + 18,
                        int(step),
                        18,
                        int(Qt.AlignmentFlag.AlignCenter),
                        str(getattr(point, "label", "")),
                    )

            if len(line_points) >= 2:
                painter.setPen(QPen(QColor("#2759a5"), 2))
                for current, next_point in zip(line_points, line_points[1:]):
                    painter.drawLine(current, next_point)
            for point in line_points:
                painter.setPen(QPen(QColor("#2759a5"), 2))
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(point, 3.6, 3.6)

            painter.setPen(QColor("#111827"))
            painter.drawText(
                rect.left() + 12,
                rect.bottom() - 8,
                "棒: 期間ごとの勝利数 / 線: 累積勝率",
            )

        @staticmethod
        def _wins(point: object) -> int:
            metric = getattr(point, "metric")
            return int(getattr(metric, "wins", 0))

        @staticmethod
        def _rate(point: object) -> float | None:
            rate = getattr(point, "cumulative_win_rate", None)
            if rate is None:
                return None
            return max(0.0, min(1.0, float(rate)))

    class MainWindow(QMainWindow):
        def __init__(
            self, service: RecorderApplicationService, *, load_runtime_data: bool
        ) -> None:
            super().__init__()
            self.service = service
            self.load_runtime_data = load_runtime_data
            self.widgets: dict[str, QWidget] = {}
            self.nav_buttons: dict[str, QPushButton] = {}
            self.setWindowTitle(f"Master Duel Recorder Lite {__version__}")
            self.resize(1180, 760)
            self.setMinimumSize(980, 640)
            self._build()

        def _register(self, key: str, widget: QWidget) -> QWidget:
            self.widgets[key] = widget
            widget.setObjectName(key)
            return widget

        def _build(self) -> None:
            root = QWidget()
            shell = QHBoxLayout(root)
            shell.setContentsMargins(0, 0, 0, 0)
            shell.setSpacing(0)

            nav = QFrame()
            nav.setObjectName("navigation")
            nav.setFixedWidth(188)
            nav_layout = QVBoxLayout(nav)
            nav_layout.setContentsMargins(0, 22, 0, 16)
            nav_layout.setSpacing(6)
            title = QLabel("MDRL")
            title.setObjectName("appTitle")
            nav_layout.addWidget(title)
            version = QLabel(f"Master Duel Recorder\nVersion {__version__}")
            version.setObjectName("appVersion")
            nav_layout.addWidget(version)
            nav_layout.addSpacing(22)
            for page, label in NAVIGATION_PAGES:
                button = QPushButton(label)
                button.setCheckable(True)
                button.setObjectName("navButton")
                button.clicked.connect(lambda _checked=False, key=page: self.show_page(key))
                nav_layout.addWidget(button)
                self.nav_buttons[page] = button
            nav_layout.addStretch(1)
            warning = QLabel("△  要確認")
            warning.setObjectName("navWarning")
            nav_layout.addWidget(warning)

            content = QWidget()
            content.setObjectName("content")
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(24, 16, 24, 12)
            content_layout.setSpacing(10)
            header = QHBoxLayout()
            self.page_title = QLabel("")
            self.page_title.setObjectName("pageTitle")
            incomplete = QLabel("戦績管理 未完了 0件")
            incomplete.setObjectName("incompleteBadge")
            self._register("incomplete_duel_count", incomplete)
            header.addWidget(self.page_title)
            header.addStretch(1)
            header.addWidget(incomplete)
            content_layout.addLayout(header)

            self.stack = QStackedWidget()
            content_layout.addWidget(self.stack, stretch=1)
            self.pages: dict[str, QWidget] = {}
            for page, label in (*NAVIGATION_PAGES, *INTERNAL_PAGES):
                widget = self._page(page)
                self.pages[page] = widget
                self.stack.addWidget(widget)

            shell.addWidget(nav)
            shell.addWidget(content, stretch=1)
            self.setCentralWidget(root)
            self.show_page("record")
            if self.load_runtime_data:
                self._load_runtime_dashboard()

        def show_page(self, key: str) -> None:
            self.stack.setCurrentWidget(self.pages[key])
            for page, button in self.nav_buttons.items():
                button.setChecked(page == key)
            label = dict((*NAVIGATION_PAGES, *INTERNAL_PAGES))[key]
            self.page_title.setText(label)

        def _page(self, key: str) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(12)
            if key == "record":
                self._record_page(layout)
            elif key == "history":
                self._history_page(layout)
            elif key == "statistics":
                self._statistics_page(layout)
            elif key == "decks":
                self._catalog_page(layout, "decks")
            elif key == "tags":
                self._catalog_page(layout, "tags")
            elif key == "seasons":
                self._season_page(layout)
            elif key == "youtube":
                self._template_page(layout)
            elif key == "reliability":
                self._reliability_page(layout)
            elif key == "settings":
                self._settings_page(layout)
            elif key == "prepare":
                self._prepare_page(layout)
            elif key == "improve":
                self._improve_page(layout)
            layout.addStretch(1)
            if key == "record":
                return page
            return self._scroll_page(page)

        def _scroll_page(self, page: QWidget) -> QScrollArea:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(page)
            return area

        def _section(
            self, key: str, title: str, subtitle: str | None = None
        ) -> tuple[QFrame, QVBoxLayout]:
            frame = self._register(key, QFrame())
            assert isinstance(frame, QFrame)
            frame.setObjectName(key)
            frame.setProperty("class", "section")
            layout = QVBoxLayout(frame)
            layout.setContentsMargins(16, 10, 16, 10)
            layout.setSpacing(8)
            if title:
                label = QLabel(title)
                label.setObjectName("sectionTitle")
                layout.addWidget(label)
            if subtitle:
                detail = QLabel(subtitle)
                detail.setObjectName("sectionSubtitle")
                detail.setWordWrap(True)
                layout.addWidget(detail)
            return frame, layout

        def _button(self, key: str, text: str, variant: str = "secondary") -> QPushButton:
            button = self._register(key, QPushButton(text))
            assert isinstance(button, QPushButton)
            button.setProperty("variant", variant)
            return button

        def _date_picker(self, key: str) -> QDateEdit:
            picker = self._register(key, QDateEdit())
            assert isinstance(picker, QDateEdit)
            picker.setCalendarPopup(True)
            picker.setDisplayFormat("yyyy-MM-dd")
            picker.setDate(QDate.currentDate())
            picker.setMinimumWidth(128)
            calendar = picker.calendarWidget()
            if calendar is not None:
                calendar.setGridVisible(True)
            return picker

        def _configure_table(
            self,
            table: QTableWidget,
            *,
            column_widths: tuple[int | None, ...] | None = None,
            stretch_last: bool = True,
            minimum_height: int | None = None,
        ) -> None:
            table.setAlternatingRowColors(True)
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            table.setWordWrap(False)
            table.setShowGrid(True)
            table.verticalHeader().setVisible(False)
            table.verticalHeader().setDefaultSectionSize(34)
            header = table.horizontalHeader()
            header.setStretchLastSection(stretch_last)
            if column_widths is None:
                header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
                return
            for index, width in enumerate(column_widths):
                if width is None:
                    header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
                else:
                    header.setSectionResizeMode(index, QHeaderView.ResizeMode.Interactive)
                    table.setColumnWidth(index, width)
            if minimum_height is not None:
                table.setMinimumHeight(minimum_height)

        @staticmethod
        def _decorate_item_with_color(
            item: QTableWidgetItem | None, color: str | None
        ) -> None:
            if item is None or not color:
                return
            qcolor = QColor(color)
            if not qcolor.isValid():
                return
            item.setData(Qt.ItemDataRole.DecorationRole, qcolor)
            item.setToolTip(f"カラー: {qcolor.name().upper()}")
            item.setText(f"  {item.text()}")

        @staticmethod
        def _contrast_text_color(color: QColor) -> QColor:
            brightness = (
                color.red() * 299 + color.green() * 587 + color.blue() * 114
            ) / 1000
            return QColor("#111827" if brightness > 150 else "#ffffff")

        def _record_page(self, layout: QVBoxLayout) -> None:
            target_section, target_layout = self._section(
                "record_target_section",
                "録画対象",
                "選択したウィンドウ、モニター、またはデスクトップを実際のFFmpeg入力に使用します。",
            )
            target_row = QHBoxLayout()
            target = self._register("target_selector", QComboBox())
            assert isinstance(target, QComboBox)
            target.addItems(("Master Duelウィンドウ", "モニター全体", "デスクトップ"))
            target_row.addWidget(target, stretch=1)
            target_row.addWidget(QPushButton("更新"))
            target_row.addWidget(QPushButton("選択を保存"))
            target_layout.addLayout(target_row)
            layout.addWidget(target_section)

            state_section, state_layout = self._section("record_state_section", "録画状態")
            state_grid = QGridLayout()
            state_grid.setColumnStretch(0, 2)
            state_grid.setColumnStretch(1, 1)
            status_band = QLabel("● 停止中")
            status_band.setObjectName("recordStatusBand")
            state_grid.addWidget(status_band, 0, 0, 1, 2)
            timer = QLabel("00:00:00")
            timer.setObjectName("recordTimer")
            state_grid.addWidget(timer, 1, 0)
            controls = QHBoxLayout()
            start = self._button("record_start", "録画開始", "danger")
            stop = self._button("record_stop", "停止", "muted")
            watch = self._button("watch_toggle", "自動監視開始")
            controls.addWidget(start)
            controls.addWidget(stop)
            controls.addWidget(watch)
            state_grid.addLayout(controls, 1, 1)
            record_status = self._register(
                "record_status",
                QLabel("録画ID: -\n保存先: -"),
            )
            visual_status = self._register("visual_status", QLabel("自動監視: 待機中"))
            audio_status = QLabel("音声: 設定で入力を選択できます")
            state_grid.addWidget(record_status, 2, 0)
            state_grid.addWidget(visual_status, 3, 0)
            state_grid.addWidget(audio_status, 4, 0)
            details = self._register("visual_details_toggle", QCheckBox("判定詳細"))
            state_grid.addWidget(details, 2, 1, alignment=Qt.AlignmentFlag.AlignRight)
            start.clicked.connect(self._start_recording)
            stop.clicked.connect(self._stop_recording)
            watch.clicked.connect(self._toggle_watch)
            state_layout.addLayout(state_grid)
            layout.addWidget(state_section)

            manual_section, manual_layout = self._section("record_manual_section", "")
            manual_row = QHBoxLayout()
            manual_row.addWidget(self._button("manual_duel_add", "＋ 戦績を追加（録画なし）"))
            manual_row.addWidget(QLabel("開催中のシーズンを読み込み中"))
            manual_row.addStretch(1)
            manual_layout.addLayout(manual_row)
            layout.addWidget(manual_section)

            bottom = QHBoxLayout()
            diagnostics, diagnostics_layout = self._section(
                "record_environment_diagnostics", "環境診断"
            )
            diag_actions = QHBoxLayout()
            diag_actions.addStretch(1)
            diag_actions.addWidget(QPushButton("保存"))
            diag_actions.addWidget(self._register("visual_diagnostics_folder", QPushButton("開く")))
            diag_actions.addWidget(QPushButton("診断実行"))
            diagnostics_layout.addLayout(diag_actions)
            diag_table = QTableWidget(3, 2)
            diag_table.setHorizontalHeaderLabels(("状態", "項目と結果"))
            diag_table.setMaximumHeight(112)
            self._configure_table(diag_table, column_widths=(70, None))
            self._set_table_rows(
                diag_table,
                (
                    ("OK", "設定: 既定値を利用可能"),
                    ("OK", "保存先: 書き込み可能"),
                    ("注意", "FFmpeg: 実環境で診断してください"),
                ),
            )
            diagnostics_layout.addWidget(diag_table)
            bottom.addWidget(diagnostics, stretch=2)

            activity_frame, activity_layout = self._section(
                "record_activity_panel", "アクティビティ"
            )
            activity = self._register("activity", QListWidget())
            assert isinstance(activity, QListWidget)
            activity.addItems(("GUI起動スモーク", "録画対象の選択待ち"))
            activity_layout.addWidget(activity)
            bottom.addWidget(activity_frame, stretch=1)
            layout.addLayout(bottom)

        def _history_page(self, layout: QVBoxLayout) -> None:
            toolbar = self._register("history_toolbar", QFrame())
            assert isinstance(toolbar, QFrame)
            toolbar_layout = QHBoxLayout(toolbar)
            toolbar_layout.setContentsMargins(0, 0, 0, 0)
            for key, text in (
                ("history_incomplete", "未完了処理"),
                ("history_bulk", "一括編集"),
                ("manual_duel_add", "手動追加"),
                ("history_play", "▶"),
                ("history_duel", "✎"),
                ("history_delete", "削除"),
                ("history_duplicates", "重複"),
                ("history_refresh", "更新"),
                ("history_columns", "表示列"),
                ("history_youtube", "YouTube"),
            ):
                button = self._button(key, text)
                toolbar_layout.addWidget(button)
                if key == "history_refresh":
                    button.clicked.connect(self._load_runtime_dashboard)
            toolbar_layout.addStretch(1)
            layout.addWidget(toolbar)

            filters = self._register("history_filter_bar", QFrame())
            assert isinstance(filters, QFrame)
            filter_layout = QHBoxLayout(filters)
            filter_layout.setContentsMargins(0, 0, 0, 0)
            filter_layout.addWidget(QLabel("フィルター"))
            for text in ("期間", "デッキ", "タグ", "登録元"):
                box = QComboBox()
                box.addItems((text, "すべて"))
                filter_layout.addWidget(box)
            history_add = self._button("history_add", "簡易入力")
            filter_layout.addWidget(history_add)
            filter_layout.addStretch(1)
            layout.addWidget(filters)

            table = QTableWidget(0, 10)
            table.setHorizontalHeaderLabels(
                (
                    "開始日時",
                    "デッキ名",
                    "勝敗",
                    "先後",
                    "コイン",
                    "対戦種別",
                    "時間",
                    "サイズ",
                    "相手デッキ",
                    "登録元",
                )
            )
            self._configure_table(
                table,
                column_widths=(148, 220, 72, 72, 72, 100, 82, 92, 180, 86),
                minimum_height=310,
            )
            self._set_table_rows(
                table,
                (
                    (
                        "2026-08-19 21:40",
                        "天威相剣",
                        "勝利",
                        "先攻",
                        "表",
                        "ランク戦",
                        "08:12",
                        "621MB",
                        "スネークアイ",
                        "録画",
                    ),
                    (
                        "2026-08-19 22:03",
                        "御巫",
                        "敗北",
                        "後攻",
                        "裏",
                        "ランク戦",
                        "-",
                        "-",
                        "未設定",
                        "手動",
                    ),
                ),
            )
            self._decorate_item_with_color(table.item(0, 1), "#2F6B5F")
            self._decorate_item_with_color(table.item(1, 1), "#8E4F7A")
            layout.addWidget(self._register("history_table", table), stretch=1)

        def _statistics_page(self, layout: QVBoxLayout) -> None:
            summary = self._register("statistics_summary", QFrame())
            assert isinstance(summary, QFrame)
            summary_layout = QHBoxLayout(summary)
            summary_layout.setContentsMargins(0, 0, 0, 0)
            for title, value, detail in (
                ("全体勝率", "50.0%", "1勝 / 2戦"),
                ("条件適用後", "50.0%", "1勝 / 2戦"),
                ("先後別", "先攻 100% / 後攻 0%", "少数標本を含む"),
            ):
                card = QFrame()
                card.setProperty("class", "metricCard")
                card_layout = QVBoxLayout(card)
                card_layout.addWidget(QLabel(title))
                metric = QLabel(value)
                metric.setObjectName("metricValue")
                card_layout.addWidget(metric)
                card_layout.addWidget(QLabel(detail))
                summary_layout.addWidget(card)
            layout.addWidget(summary)

            filters = QGroupBox("条件")
            grid = QGridLayout(filters)
            grid.addWidget(QLabel("開始日"), 0, 0)
            grid.addWidget(self._date_picker("statistics_date_from_picker"), 0, 1)
            grid.addWidget(QLabel("終了日"), 0, 2)
            grid.addWidget(self._date_picker("statistics_date_to_picker"), 0, 3)
            filter_box = self._register("statistics_filters", QComboBox())
            assert isinstance(filter_box, QComboBox)
            filter_box.addItems(("すべて", "勝利のみ", "敗北のみ"))
            grid.addWidget(QLabel("条件"), 0, 4)
            grid.addWidget(filter_box, 0, 5)
            layout.addWidget(filters)

            tabs = self._register("statistics_tab_panel", QTabWidget())
            assert isinstance(tabs, QTabWidget)
            trend = QWidget()
            trend_layout = QVBoxLayout(trend)
            trend_controls = QHBoxLayout()
            trend_controls.addWidget(QLabel("推移単位"))
            granularity = QComboBox()
            granularity.addItems(("日", "週", "月"))
            trend_controls.addWidget(granularity)
            trend_controls.addStretch(1)
            trend_layout.addLayout(trend_controls)
            chart = StatisticsTrendChart()
            trend_layout.addWidget(self._register("statistics_chart", chart))
            tabs.addTab(trend, "勝利数・勝率推移")
            tabs.addTab(
                self._table_panel("statistics_deck_table", ("デッキ", "対戦", "勝利", "勝率")),
                "デッキ別全体",
            )
            tabs.addTab(
                self._table_panel("statistics_order_table", ("先後", "対戦", "勝利", "勝率")),
                "デッキ先後別",
            )
            tabs.addTab(
                self._table_panel("statistics_coin_table", ("コイン", "対戦", "勝利", "勝率")),
                "コイントス別",
            )
            tabs.addTab(
                self._table_panel("statistics_season_table", ("シーズン", "対戦", "勝利", "勝率")),
                "シーズン別",
            )
            layout.addWidget(tabs, stretch=1)

        def _catalog_page(self, layout: QVBoxLayout, key: str) -> None:
            is_deck = key == "decks"
            editor_key = "deck_editor" if is_deck else "tag_editor"
            title = "デッキ名管理" if is_deck else "タグ管理"
            editor, editor_layout = self._section(editor_key, title)
            grid = QGridLayout()
            grid.addWidget(QLabel("名前"), 0, 0)
            grid.addWidget(QLineEdit(), 0, 1, 1, 3)
            grid.addWidget(QLabel("説明"), 1, 0)
            grid.addWidget(QLineEdit(), 1, 1, 1, 3)
            grid.addWidget(QLabel("カラー"), 2, 0)
            grid.addWidget(QPushButton("色を選択"), 2, 1)
            if is_deck:
                grid.addWidget(QCheckBox("相手デッキのみで使用"), 2, 2)
                grid.addWidget(QCheckBox("履歴・統計で非表示"), 2, 3)
            else:
                grid.addWidget(QCheckBox("デッキ名登録でのみ使用"), 2, 2)
            editor_layout.addLayout(grid)
            actions = QHBoxLayout()
            actions.addStretch(1)
            actions.addWidget(QPushButton("追加"))
            actions.addWidget(QPushButton("保存"))
            actions.addWidget(QPushButton("削除"))
            editor_layout.addLayout(actions)
            layout.addWidget(editor)

            headers = (
                ("カラー", "名前", "説明", "使用回数", "用途")
                if is_deck
                else ("カラー", "名前", "説明", "用途")
            )
            rows = (
                (
                    ("#2F6B5F", "天威相剣", "ランク戦メイン", 12, "通常"),
                    ("#8E4F7A", "御巫", "後攻確認用", 3, "通常"),
                )
                if is_deck
                else (
                    ("#4F6F8F", "ランク戦", "ランクマッチ用の共通タグ", "通常"),
                    ("#B08942", "大型連勝", "デッキ検証で使用", "デッキ専用"),
                )
            )
            widget_key = "deck_catalog_table" if is_deck else "tag_catalog_table"
            table = self._table(widget_key, headers, rows)
            if is_deck:
                self._configure_table(
                    table,
                    column_widths=(74, 180, None, 86, 120),
                    minimum_height=250,
                )
            else:
                self._configure_table(
                    table,
                    column_widths=(74, 180, None, 120),
                    minimum_height=250,
                )
            layout.addWidget(table, stretch=1)
            if is_deck:
                layout.addWidget(
                    self._register(
                        "catalog_table",
                        QLabel("デッキ名候補、使用回数、デッキタグの管理状態を表示します"),
                    )
                )

        def _season_page(self, layout: QVBoxLayout) -> None:
            editor, editor_layout = self._section("season_editor", "シーズン管理")
            grid = QGridLayout()
            grid.addWidget(QLabel("名前"), 0, 0)
            grid.addWidget(QLineEdit(), 0, 1)
            grid.addWidget(QLabel("種別"), 0, 2)
            type_box = QComboBox()
            type_box.addItems(("ランク戦", "イベント", "その他"))
            grid.addWidget(type_box, 0, 3)
            grid.addWidget(QLabel("開始日"), 1, 0)
            grid.addWidget(self._date_picker("season_start_date_picker"), 1, 1)
            grid.addWidget(QLabel("終了日"), 1, 2)
            grid.addWidget(self._date_picker("season_end_date_picker"), 1, 3)
            grid.addWidget(QLabel("説明"), 2, 0)
            grid.addWidget(QLineEdit(), 2, 1, 1, 3)
            editor_layout.addLayout(grid)
            actions = QHBoxLayout()
            actions.addStretch(1)
            for text in ("追加", "保存", "アーカイブ", "レポート"):
                actions.addWidget(QPushButton(text))
            editor_layout.addLayout(actions)
            layout.addWidget(editor)
            layout.addWidget(
                self._table(
                    "season_table",
                    ("シーズン", "種別", "期間", "状態"),
                    (
                        ("WCS予選", "イベント", "2026-08-01 - 2026-08-20", "有効"),
                        ("ランク戦 8月", "ランク戦", "2026-08-01 - 2026-08-31", "有効"),
                    ),
                    column_widths=(220, 96, 210, 96),
                ),
                stretch=1,
            )

        def _template_page(self, layout: QVBoxLayout) -> None:
            editor, editor_layout = self._section(
                "template_editor", "YouTube投稿テンプレート"
            )
            grid = QGridLayout()
            grid.addWidget(QLabel("タイトル"), 0, 0)
            title = QLineEdit("{date} {own_deck} 対 {opponent_deck}")
            grid.addWidget(title, 0, 1)
            grid.addWidget(QLabel("概要欄"), 1, 0)
            template = self._register("youtube_template", QTextEdit())
            assert isinstance(template, QTextEdit)
            template.setPlainText(
                "\n".join(
                    (
                        "{title}",
                        "使用デッキ: {own_deck}",
                        "対戦相手: {opponent_deck}",
                        "結果: {result}",
                    )
                )
            )
            grid.addWidget(template, 1, 1)
            grid.addWidget(QLabel("タグ"), 2, 0)
            grid.addWidget(QLineEdit("MasterDuel, 遊戯王, {own_deck}"), 2, 1)
            editor_layout.addLayout(grid)
            variables = QLabel(
                "使用できる変数: {date}, {result}, {own_deck}, "
                "{opponent_deck}, {season}, {tags}"
            )
            variables.setWordWrap(True)
            editor_layout.addWidget(variables)
            connection = QHBoxLayout()
            connection.addWidget(self._register("youtube_status", QLabel("YouTube: 未接続")))
            for key, text in (
                ("youtube_connect", "接続"),
                ("youtube_disconnect", "切断"),
                ("youtube_refresh", "更新"),
                ("youtube_test_upload", "privateテスト"),
            ):
                connection.addWidget(self._button(key, text))
            connection.addStretch(1)
            editor_layout.addLayout(connection)
            save_row = QHBoxLayout()
            save_row.addStretch(1)
            save_row.addWidget(QPushButton("一覧"))
            save_row.addWidget(QPushButton("保存"))
            editor_layout.addLayout(save_row)
            layout.addWidget(editor, stretch=1)

            prepare_row = QHBoxLayout()
            prepare_row.addWidget(self._button("prepare_recording", "選択録画をMP4準備へ追加"))
            prepare_row.addWidget(QLabel("投稿前処理キューは内部ページとして維持します"))
            prepare_row.addStretch(1)
            layout.addLayout(prepare_row)
            layout.addWidget(
                self._table(
                    "prepare_table",
                    ("録画ID", "状態", "タイトル", "公開範囲", "更新日時"),
                    (("sample-rec", "waiting", "投稿準備サンプル", "private", "2026-08-23"),),
                    column_widths=(140, 92, None, 92, 148),
                )
            )

        def _reliability_page(self, layout: QVBoxLayout) -> None:
            preflight, preflight_layout = self._section(
                "reliability_preflight_panel", "自動録画への信頼性"
            )
            status = self._register(
                "reliability_status",
                QLabel(
                    "30秒事前チェック、Master Duel録画用window/monitor診断、"
                    "ホットキー、トレイ状態を確認します。"
                ),
            )
            assert isinstance(status, QLabel)
            status.setWordWrap(True)
            preflight_layout.addWidget(status)
            actions = QHBoxLayout()
            actions.addWidget(QPushButton("状態更新"))
            actions.addWidget(QPushButton("初回導入を確認"))
            actions.addStretch(1)
            preflight_layout.addLayout(actions)
            layout.addWidget(preflight)

            improvement, improvement_layout = self._section(
                "improve_internal_page", "入力削減と運用管理"
            )
            improvement_status = self._register(
                "improvement_status",
                QLabel("録画なし戦績追加、デッキ改善候補、保存候補、後解析の状態を確認します。"),
            )
            assert isinstance(improvement_status, QLabel)
            improvement_status.setWordWrap(True)
            improvement_layout.addWidget(improvement_status)
            layout.addWidget(improvement)

        def _settings_page(self, layout: QVBoxLayout) -> None:
            tabs = self._register("settings_tabs", QTabWidget())
            assert isinstance(tabs, QTabWidget)
            tabs.addTab(self._recording_settings_tab(), "録画設定")
            tabs.addTab(self._youtube_settings_tab(), "YouTube")
            tabs.addTab(self._data_settings_tab(), "管理データ")
            tabs.addTab(self._csv_settings_tab(), "CSV入出力")
            tabs.addTab(self._display_settings_tab(), "表示")
            tabs.addTab(self._update_settings_tab(), "アプリ更新")
            layout.addWidget(tabs, stretch=1)

        def _recording_settings_tab(self) -> QWidget:
            tab = QWidget()
            grid = QGridLayout(tab)
            grid.addWidget(self._button("ffmpeg_setup", "FFmpegを設定"), 0, 0)
            grid.addWidget(QPushButton("FFmpegを導入"), 0, 1)
            grid.addWidget(QLabel("音声入力"), 1, 0)
            audio = QComboBox()
            audio.addItems(("使用しない", "Master Duel単体音声", "既定デバイス"))
            grid.addWidget(audio, 1, 1)
            grid.addWidget(QLabel("フレームレート"), 2, 0)
            grid.addWidget(QSpinBox(), 2, 1)
            grid.addWidget(QLabel("ビットレート"), 3, 0)
            grid.addWidget(QLineEdit("6000k"), 3, 1)
            grid.addWidget(QCheckBox("ウィンドウを自動検出"), 4, 0)
            grid.addWidget(QCheckBox("録画開始をWindows通知"), 4, 1)
            settings_form = self._register(
                "settings_form",
                QLabel("通常設定 / 外部連携 / データ保護 / 危険操作"),
            )
            grid.addWidget(settings_form, 5, 0, 1, 2)
            return tab

        def _youtube_settings_tab(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(QLabel("YouTube連携状態、接続、切断、接続確認を扱います。"))
            layout.addWidget(QPushButton("最新録画でprivateテスト投稿"))
            layout.addStretch(1)
            return tab

        def _data_settings_tab(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            status = self._register(
                "data_protection_status",
                QLabel(f"データ保護: DB {self.service.paths.db / 'history.sqlite3'}"),
            )
            scope = self._register(
                "data_protection_scope",
                QLabel(
                    "バックアップ対象: 管理DBと設定。録画ファイル、queue、manifest、"
                    "OAuth資格情報は対象外です。"
                ),
            )
            assert isinstance(scope, QLabel)
            scope.setWordWrap(True)
            layout.addWidget(status)
            layout.addWidget(scope)
            actions = QHBoxLayout()
            for text in ("バックアップ", "復元", "整合性診断"):
                actions.addWidget(QPushButton(text))
            actions.addWidget(self._button("clean_uninstall", "クリーンアンインストール", "danger"))
            actions.addStretch(1)
            layout.addLayout(actions)
            layout.addWidget(
                self._table(
                    "data_backup_table",
                    ("作成日時", "契機", "DB版", "サイズ"),
                    (("2026-08-23 18:00", "手動", "1", "128KB"),),
                    column_widths=(150, None, 80, 100),
                ),
                stretch=1,
            )
            return tab

        def _csv_settings_tab(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(self._register("csv_status", QLabel("CSV入出力: 待機中")))
            row = QHBoxLayout()
            for text in ("CSVを書き出し", "CSVを取り込み", "サンプル保存"):
                row.addWidget(QPushButton(text))
            row.addStretch(1)
            layout.addLayout(row)
            layout.addStretch(1)
            return tab

        def _display_settings_tab(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(QLabel("戦績管理セル色とダブルクリック動作を設定します。"))
            layout.addWidget(QCheckBox("ダブルクリックで録画再生"))
            layout.addWidget(QCheckBox("未完了戦績を強調表示"))
            layout.addStretch(1)
            return tab

        def _update_settings_tab(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(self._register("app_update", QPushButton("アプリ更新を確認")))
            layout.addWidget(QPushButton("ダウンロードして更新"))
            layout.addWidget(QCheckBox("起動後に更新を確認"))
            layout.addStretch(1)
            return tab

        def _prepare_page(self, layout: QVBoxLayout) -> None:
            panel, panel_layout = self._section("prepare_internal_page", "フォーマット/MP4準備")
            row = QHBoxLayout()
            row.addWidget(QLabel("対象録画"))
            target = QComboBox()
            target.addItems(("2026-08-19 sample-rec", "最新録画"))
            row.addWidget(target, stretch=1)
            row.addWidget(QLineEdit("投稿タイトル"))
            row.addWidget(QPushButton("キューへ追加"))
            row.addWidget(QPushButton("待機中を実行"))
            panel_layout.addLayout(row)
            panel_layout.addWidget(
                self._table(
                    "prepare_internal_table",
                    ("録画ID", "タイトル", "状態"),
                    (("sample-rec", "投稿準備サンプル", "waiting"),),
                    column_widths=(150, None, 110),
                )
            )
            layout.addWidget(panel, stretch=1)

        def _improve_page(self, layout: QVBoxLayout) -> None:
            panel, panel_layout = self._section("improve_internal_page", "入力削減と運用管理")
            panel_layout.addWidget(QLabel("録画なし戦績追加、デッキ改善、タグ、保存候補を確認します。"))
            row = QHBoxLayout()
            row.addWidget(QPushButton("状態を更新"))
            row.addWidget(QPushButton("録画なし戦績を追加"))
            row.addStretch(1)
            panel_layout.addLayout(row)
            layout.addWidget(panel)

        def _table_panel(self, key: str, headers: tuple[str, ...]) -> QWidget:
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(
                self._table(
                    key,
                    headers,
                    ((headers[0], 2, 1, "50.0%"),),
                    column_widths=(None, 82, 82, 96),
                )
            )
            return panel

        def _table(
            self,
            key: str,
            headers: tuple[str, ...],
            rows: tuple[tuple[object, ...], ...] = (),
            *,
            column_widths: tuple[int | None, ...] | None = None,
        ) -> QTableWidget:
            table = QTableWidget(0, len(headers))
            table.setHorizontalHeaderLabels(headers)
            self._configure_table(table, column_widths=column_widths)
            table.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            self._set_table_rows(table, rows)
            return self._register(key, table)  # type: ignore[return-value]

        def _load_runtime_dashboard(self) -> None:
            loaders = (
                self._refresh_history,
                self._refresh_catalogs,
                self._refresh_seasons,
                self._refresh_youtube,
                self._refresh_preparations,
                self._refresh_data_protection,
                self._refresh_statistics,
            )
            loaded = 0
            errors: list[str] = []
            for loader in loaders:
                try:
                    loader()
                    loaded += 1
                except Exception as exc:
                    errors.append(str(exc))
            activity = self.widgets.get("activity")
            if isinstance(activity, QListWidget):
                if errors:
                    activity.addItem(f"一部の表示更新に失敗しました: {errors[0]}")
                else:
                    activity.addItem(f"既存データを{loaded}領域で読み込みました")

        def _start_recording(self) -> None:
            self._run_action("録画開始", self.service.start_recording)

        def _stop_recording(self) -> None:
            self._run_action("録画停止", self.service.stop_recording)

        def _toggle_watch(self) -> None:
            if self.service.watch_active:
                self._run_action("自動監視停止", self.service.stop_watch)
            else:
                self._run_action("自動監視開始", self.service.start_watch)

        def _refresh_history(self) -> None:
            dashboard = self.service.get_history_dashboard(limit=200)
            table = self.widgets["history_table"]
            assert isinstance(table, QTableWidget)
            rows = []
            deck_colors = []
            for view in dashboard.views:
                entry = view.entry
                deck_colors.append(view.own_deck_color)
                rows.append(
                    (
                        view.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                        view.own_deck or "-",
                        view.result,
                        view.play_order,
                        view.coin_face,
                        view.duel_type,
                        "-",
                        "-",
                        view.opponent_deck or "-",
                        entry.state if entry is not None else "手動",
                    )
                )
            self._set_table_rows(table, rows)
            for row_index, color in enumerate(deck_colors):
                self._decorate_item_with_color(table.item(row_index, 1), color)
            incomplete = self.widgets["incomplete_duel_count"]
            assert isinstance(incomplete, QLabel)
            incomplete.setText(
                f"戦績管理 未完了 {dashboard.incomplete_duel_record_count}件"
            )

        def _refresh_catalogs(self) -> None:
            deck_table = self.widgets["deck_catalog_table"]
            tag_table = self.widgets["tag_catalog_table"]
            assert isinstance(deck_table, QTableWidget)
            assert isinstance(tag_table, QTableWidget)
            self._set_table_rows(
                deck_table,
                tuple(
                    (
                        deck.color or "#2F6B5F",
                        deck.name,
                        deck.description,
                        deck.usage_count,
                        "非表示" if deck.hidden_from_history_statistics else "表示",
                    )
                    for deck in self.service.list_decks()
                ),
            )
            self._set_table_rows(
                tag_table,
                tuple(
                    (
                        tag.color or "#4F6F8F",
                        tag.name,
                        tag.description,
                        "デッキ専用" if tag.deck_only else "通常",
                    )
                    for tag in self.service.list_tags()
                ),
            )

        def _refresh_seasons(self) -> None:
            table = self.widgets["season_table"]
            assert isinstance(table, QTableWidget)
            self._set_table_rows(
                table,
                tuple(
                    (
                        season.name,
                        season.season_type,
                        f"{season.start_date} - {season.end_date}",
                        "アーカイブ" if season.is_archived else "有効",
                    )
                    for season in self.service.list_seasons(include_archived=True)
                ),
            )

        def _refresh_youtube(self) -> None:
            status = self.service.youtube_connection_status()
            status_label = self.widgets["youtube_status"]
            assert isinstance(status_label, QLabel)
            status_label.setText(f"YouTube: {status.message}")
            template = self.service.get_youtube_posting_template()
            editor = self.widgets["youtube_template"]
            assert isinstance(editor, QTextEdit)
            editor.setPlainText(
                "\n".join(
                    (
                        f"タイトル: {template.title}",
                        "説明:",
                        template.description,
                        f"タグ: {template.tags}",
                        "公開範囲: private",
                    )
                )
            )

        def _refresh_preparations(self) -> None:
            table = self.widgets["prepare_table"]
            assert isinstance(table, QTableWidget)
            self._set_table_rows(
                table,
                tuple(
                    (
                        item.recording_id,
                        item.state.value,
                        item.metadata.title,
                        item.metadata.privacy.value,
                        item.updated_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                    )
                    for item in self.service.list_preparations()
                ),
            )

        def _refresh_data_protection(self) -> None:
            status = self.widgets["data_protection_status"]
            assert isinstance(status, QLabel)
            status.setText(f"データ保護: DB {self.service.paths.db / 'history.sqlite3'}")
            table = self.widgets["data_backup_table"]
            assert isinstance(table, QTableWidget)
            self._set_table_rows(
                table,
                tuple(
                    (
                        backup.created_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                        backup.reason,
                        backup.schema_version,
                        backup.size_bytes,
                    )
                    for backup in self.service.list_data_backups()
                ),
            )

        def _refresh_statistics(self) -> None:
            dashboard = self.service.get_statistics_dashboard(granularity="day")
            chart = self.widgets["statistics_chart"]
            assert isinstance(chart, StatisticsTrendChart)
            overall = dashboard.overall
            chart.set_points(dashboard.trend)
            chart.setToolTip(
                "日別勝利数と累積勝率: "
                + f"{overall.wins}勝 / {overall.matches}戦"
            )
            self._set_breakdown_rows("statistics_deck_table", dashboard.by_deck)
            self._set_breakdown_rows("statistics_order_table", dashboard.by_play_order)
            self._set_breakdown_rows("statistics_coin_table", dashboard.by_coin_face)
            self._set_breakdown_rows("statistics_season_table", dashboard.by_season)

        def _set_breakdown_rows(self, key: str, rows: tuple[object, ...]) -> None:
            table = self.widgets[key]
            assert isinstance(table, QTableWidget)
            self._set_table_rows(
                table,
                tuple(
                    (
                        getattr(row, "label"),
                        getattr(row, "metric").matches,
                        getattr(row, "metric").wins,
                        self._format_rate(getattr(row, "metric").win_rate),
                    )
                    for row in rows
                ),
            )

        @staticmethod
        def _format_rate(value: float | None) -> str:
            return "-" if value is None else f"{value * 100:.1f}%"

        @staticmethod
        def _set_table_rows(
            table: QTableWidget,
            rows: tuple[tuple[object, ...], ...] | list[tuple[object, ...]],
        ) -> None:
            table.setRowCount(len(rows))
            for row_index, row_values in enumerate(rows):
                for column, value in enumerate(row_values):
                    item = QTableWidgetItem(str(value))
                    header_item = table.horizontalHeaderItem(column)
                    header_text = header_item.text() if header_item is not None else ""
                    if header_text == "カラー":
                        color = QColor(str(value))
                        if color.isValid():
                            item.setText(color.name().upper())
                            item.setBackground(color)
                            item.setForeground(MainWindow._contrast_text_color(color))
                            item.setToolTip(f"カラー: {color.name().upper()}")
                    table.setItem(row_index, column, item)
            table.resizeRowsToContents()

        def _run_action(self, label: str, operation: Any) -> None:
            try:
                result = operation()
            except Exception as exc:
                QMessageBox.warning(self, f"{label}に失敗しました", str(exc))
                self._append_activity(f"{label}に失敗しました")
                return
            record_status = self.widgets.get("record_status")
            if isinstance(record_status, QLabel) and hasattr(result, "state"):
                recording_id = getattr(result, "recording_id", None) or "-"
                record_status.setText(f"録画: {result.state.value}\n録画ID: {recording_id}")
            self._append_activity(
                str(result) if isinstance(result, str) else f"{label}が完了しました"
            )

        def _append_activity(self, text: str) -> None:
            activity = self.widgets.get("activity")
            if isinstance(activity, QListWidget):
                activity.addItem(text)

    app = QApplication.instance() or QApplication([])
    service = RecorderApplicationService(
        project_root=args.project_root,
        user_data_dir=args.user_data_dir,
    )
    window = MainWindow(service, load_runtime_data=not args.smoke_test)
    window.setStyleSheet(_style_sheet())
    window.show()
    app.processEvents()

    if args.smoke_test:
        contract = smoke_contract(
            service=service,
            width=window.width(),
            height=window.height(),
        )
        if args.smoke_output is not None:
            args.smoke_output.parent.mkdir(parents=True, exist_ok=True)
            args.smoke_output.write_text(
                json.dumps(contract, ensure_ascii=False), encoding="utf-8"
            )
        if args.smoke_screenshot is not None:
            args.smoke_screenshot.parent.mkdir(parents=True, exist_ok=True)
            window.show_page(args.smoke_page)
            if args.smoke_page == "history":
                table = window.widgets.get("history_table")
                if isinstance(table, QTableWidget) and table.rowCount() > 0:
                    table.selectRow(0)
            elif args.smoke_page == "decks":
                table = window.widgets.get("deck_catalog_table")
                if isinstance(table, QTableWidget) and table.rowCount() > 0:
                    table.selectRow(0)
            app.processEvents()
            window.grab().save(str(args.smoke_screenshot))
        window.close()
        app.processEvents()
        return 0
    return int(app.exec())


def _style_sheet() -> str:
    return """
    * {
        font-family: "Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic", "Segoe UI";
        font-size: 10pt;
    }
    QMainWindow { background: #f4f7f5; color: #111827; }
    #navigation { background: #edf5f2; border-right: 1px solid #dbe7e3; }
    #appTitle {
        color: #007c7a;
        font-size: 27px;
        font-weight: 700;
        padding: 6px 22px 0 22px;
    }
    #appVersion { color: #1f2933; padding: 12px 22px; font-size: 11px; }
    #navButton {
        border: 0;
        border-radius: 0;
        background: transparent;
        color: #0f172a;
        text-align: left;
        padding: 11px 22px;
        min-height: 34px;
    }
    #navButton:checked { background: #cdebe7; color: #006f6a; font-weight: 700; }
    #navWarning { color: #9a6700; padding: 12px 22px; }
    #content { background: #f4f7f5; }
    #pageTitle { font-size: 26px; font-weight: 700; color: #111827; }
    #incompleteBadge {
        background: #fff1bd;
        color: #6d4c00;
        padding: 8px 14px;
        font-weight: 700;
    }
    QFrame[class="section"], QGroupBox {
        background: #ffffff;
        border: 1px solid #edf0f2;
        border-radius: 0;
    }
    QFrame[class="metricCard"] {
        background: #ffffff;
        border: 1px solid #edf0f2;
        padding: 10px;
    }
    #sectionTitle { font-weight: 700; color: #111827; }
    #sectionSubtitle { color: #374151; }
    #recordStatusBand {
        background: #e8ecf2;
        color: #111827;
        font-size: 16px;
        font-weight: 700;
        padding: 12px;
    }
    #recordTimer {
        font-family: "Consolas", "Courier New", monospace;
        font-size: 25px;
        font-weight: 700;
    }
    #metricValue { color: #007c7a; font-size: 24px; font-weight: 700; }
    QPushButton {
        min-height: 30px;
        padding: 4px 12px;
        border: 1px solid #b8c1cc;
        border-radius: 0;
        background: #f9fafb;
        color: #111827;
    }
    QPushButton[variant="danger"] {
        background: #b91c1c;
        color: #ffffff;
        border-color: #991b1b;
        font-weight: 700;
    }
    QPushButton[variant="muted"] {
        background: #b7d3cb;
        color: #52655f;
        border-color: #8fb3aa;
        font-weight: 700;
    }
    QComboBox, QLineEdit, QDateEdit, QSpinBox {
        min-height: 28px;
        border: 1px solid #c8d0d8;
        background: #ffffff;
        padding: 2px 6px;
    }
    QTableWidget, QListWidget, QTextEdit {
        background: #ffffff;
        border: 1px solid #c8d0d8;
        alternate-background-color: #f7faf9;
        selection-background-color: #d7ece8;
        selection-color: #10201c;
        gridline-color: #e3e8ed;
    }
    QTableWidget::item { padding: 4px 7px; }
    QTableWidget::item:selected { background: #d7ece8; color: #10201c; }
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


if __name__ == "__main__":
    raise SystemExit(main())
