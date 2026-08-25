from __future__ import annotations

import argparse
import concurrent.futures
from dataclasses import dataclass
from datetime import date
import importlib.util
import json
from pathlib import Path
from typing import Any

from . import __version__
from .app_update import AppUpdateService, UpdateRelease, launch_update_after_exit
from .application import DuelManagementQuery, RecorderApplicationService
from .config_management import config_values
from .duel_records import duel_choice_label
from .gui_feature_parity import (
    STANDARD_GUI_FEATURES,
    evaluate_standard_operation_checks,
    required_standard_widget_keys,
    satisfied_standard_feature_keys,
)
from .pyside_review import REVIEW_WIDGETS
from .ui_preferences import load_ui_preferences, save_ui_preferences
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
    "nav_health_status",
    "active_season_status",
    "history_table",
    "history_date_from_picker",
    "history_date_to_picker",
    "statistics_chart",
    "statistics_date_from_picker",
    "statistics_date_to_picker",
    "deck_catalog_table",
    "tag_catalog_table",
    "season_table",
    "deck_name_input",
    "tag_name_input",
    "season_name_input",
    "reliability_refresh",
    "reliability_setup_check",
    "youtube_background_status",
    "youtube_upload_progress",
)

SETTINGS_PARITY_WIDGETS: tuple[str, ...] = (
    "settings_ffmpeg_path",
    "settings_ffmpeg_select",
    "settings_audio_mode",
    "settings_audio_input",
    "settings_audio_refresh",
    "settings_audio_test",
    "settings_audio_status",
    "settings_frame_rate",
    "settings_video_bitrate",
    "settings_audio_gain",
    "settings_capture_width",
    "settings_capture_height",
    "settings_audio_sample_rate",
    "settings_audio_channels",
    "settings_auto_start",
    "settings_auto_stop",
    "settings_visual_detection",
    "settings_windows_notifications",
    "settings_visual_fps",
    "settings_visual_language",
    "settings_visual_confidence",
    "settings_runtime_path",
    "settings_runtime_change",
    "settings_reload",
    "settings_save",
    "settings_youtube_status",
    "settings_youtube_scope",
    "settings_youtube_connect",
    "settings_youtube_disconnect",
    "settings_youtube_refresh",
    "settings_youtube_test_upload",
    "settings_managed_export",
    "settings_managed_import",
    "settings_reset_history",
    "settings_reset_decks",
    "settings_reset_tags",
    "settings_reset_seasons",
    "settings_data_backup",
    "settings_data_restore",
    "settings_data_diagnosis",
    "settings_csv_export",
    "settings_csv_import",
    "settings_csv_sample",
    "settings_display_colors",
    "settings_double_click_play",
    "settings_double_click_edit",
    "app_update_status",
    "app_update_auto_check",
    "app_update_download",
)

SMOKE_WIDGETS: tuple[str, ...] = tuple(
    sorted(
        set(required_standard_widget_keys())
        | set(RICH_UI_SECTION_WIDGETS)
        | set(UI_USABILITY_WIDGETS)
        | set(SETTINGS_PARITY_WIDGETS)
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


def history_entry_origin_label(value: str) -> str:
    return {
        "recording": "録画",
        "manual": "手動",
        "import": "取込",
    }.get(value, value or "-")


def history_table_display_row(view: object) -> tuple[str, str, str, str, str, str, str, str, str, str]:
    return (
        view.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M"),
        view.own_deck or "-",
        duel_choice_label("result", view.result),
        duel_choice_label("play_order", view.play_order),
        duel_choice_label("coin_face", view.coin_face),
        duel_choice_label("duel_type", view.duel_type),
        "-",
        "-",
        view.opponent_deck or "-",
        history_entry_origin_label(view.entry_origin),
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
                "history_date_from_picker",
                "history_date_to_picker",
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
            "catalog_color_codes_hidden": True,
            "selection_color_independent": True,
        },
        "history_hub_operation_contract": {
            "connected_buttons": [
                "history_incomplete",
                "history_bulk",
                "manual_duel_add",
                "history_add",
                "history_play",
                "history_duel",
                "history_delete",
                "history_duplicates",
                "history_refresh",
                "history_columns",
                "history_youtube",
            ],
            "selection_required_buttons": [
                "history_play",
                "history_duel",
                "history_delete",
                "history_youtube",
            ],
            "japanese_display_columns": [
                "勝敗",
                "先後",
                "コイン",
                "対戦種別",
                "登録元",
            ],
            "internal_values_not_displayed": [
                "win",
                "loss",
                "first",
                "second",
                "heads",
                "tails",
                "ranked",
                "event",
                "other",
            ],
            "date_filter_widgets": [
                "history_date_from_picker",
                "history_date_to_picker",
                "history_filter_apply",
                "history_filter_clear",
            ],
            "danger_button": "history_delete",
        },
        "review_video_contract": {
            "entry_button": "history_play",
            "widgets": list(REVIEW_WIDGETS),
            "supported_extensions": [".mp4", ".mkv"],
            "fallback": "external_player",
            "timeline_columns": ["経過", "種別", "状態", "ラベル", "由来"],
            "marker_source": "RecorderApplicationService.add_review_marker",
            "clip_export_source": "RecorderApplicationService.export_review_clip",
        },
        "settings_parity_widgets": list(SETTINGS_PARITY_WIDGETS),
        "settings_parity_contract": all(
            widget in widget_keys for widget in SETTINGS_PARITY_WIDGETS
        ),
        "app_update_state_contract": {
            "status_widget": "app_update_status",
            "check_button": "app_update",
            "download_button": "app_update_download",
            "download_enabled_only_after_candidate": True,
            "latest_without_candidate_disables_download": True,
        },
        "active_season_contract": {
            "status_widget": "active_season_status",
            "service_method": "RecorderApplicationService.active_season_summaries",
            "fixed_loading_text_removed": True,
        },
        "health_status_contract": {
            "status_widget": "nav_health_status",
            "service_method": "RecorderApplicationService.diagnose",
            "fixed_warning_removed": True,
        },
        "catalog_edit_contract": {
            "deck_widgets": [
                "deck_name_input",
                "deck_add",
                "deck_save",
                "deck_delete",
            ],
            "tag_widgets": [
                "tag_name_input",
                "tag_add",
                "tag_save",
                "tag_delete",
            ],
        },
        "season_edit_contract": {
            "widgets": [
                "season_name_input",
                "season_add",
                "season_save",
                "season_archive",
                "season_report",
                "season_start_date_picker",
                "season_end_date_picker",
            ],
            "date_picker": True,
        },
        "template_screen_contract": {
            "editor_widgets": [
                "youtube_template_title",
                "youtube_template",
                "youtube_template_tags",
                "youtube_template_save",
                "youtube_background_status",
                "youtube_upload_progress",
            ],
            "connection_buttons_removed": all(
                widget not in widget_keys
                for widget in (
                    "youtube_connect",
                    "youtube_disconnect",
                    "youtube_refresh",
                    "youtube_test_upload",
                )
            ),
            "connection_management_page": "settings",
        },
        "reliability_action_contract": {
            "buttons": ["reliability_refresh", "reliability_setup_check"],
            "click_updates_status": True,
        },
        "background_operation_contract": {
            "executor": "ThreadPoolExecutor",
            "youtube_upload_worker": True,
            "progress_widget": "youtube_upload_progress",
            "double_submit_guard": True,
        },
        "control_height_contract": {
            "button_min_height": 36,
            "input_min_height": 36,
            "combo_min_height": 36,
            "date_picker_min_height": 36,
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
        from PySide6.QtCore import QDate, QPointF, Qt, QTimer
        from PySide6.QtGui import QColor, QPainter, QPen
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QApplication,
            QCheckBox,
            QComboBox,
            QColorDialog,
            QDateEdit,
            QFileDialog,
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
            QProgressBar,
            QPushButton,
            QScrollArea,
            QSizePolicy,
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
            self.review_windows: list[QWidget] = []
            self.available_update: UpdateRelease | None = None
            self.history_views_by_row_id: dict[str, object] = {}
            self.catalog_entries_by_id: dict[int, object] = {}
            self.seasons_by_id: dict[int, object] = {}
            self.selected_catalog_entry_ids: dict[str, int | None] = {
                "decks": None,
                "tags": None,
            }
            self.selected_season_id: int | None = None
            self.background_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="mdrl-gui"
            )
            self.background_tasks: list[
                tuple[str, concurrent.futures.Future[object], Any]
            ] = []
            self.youtube_upload_running = False
            self.background_timer = QTimer(self)
            self.background_timer.setInterval(300)
            self.background_timer.timeout.connect(self._poll_background_tasks)
            self.setting_fields: dict[str, QLineEdit] = {}
            self.setting_checks: dict[str, QCheckBox] = {}
            self.setting_field_keys: dict[str, str] = {}
            self.setting_check_keys: dict[str, str] = {}
            self.ui_preferences = load_ui_preferences(self.service.paths.config)
            self.setWindowTitle(f"Master Duel Recorder Lite {__version__}")
            self.resize(1180, 760)
            self.setMinimumSize(980, 640)
            self._build()

        def _register(self, key: str, widget: QWidget) -> QWidget:
            self.widgets[key] = widget
            widget.setObjectName(key)
            return widget

        def closeEvent(self, event: object) -> None:
            self.background_timer.stop()
            self.background_executor.shutdown(wait=False, cancel_futures=True)
            super().closeEvent(event)

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
            health = self._register("nav_health_status", QLabel("状態: 未確認"))
            assert isinstance(health, QLabel)
            health.setObjectName("navWarning")
            health.setWordWrap(True)
            nav_layout.addWidget(health)

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
            if key == "settings":
                self.load_settings()
                self._refresh_youtube_settings()
                self._refresh_data_protection()

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

        def _plain_button(self, text: str, variant: str = "secondary") -> QPushButton:
            button = QPushButton(text)
            button.setProperty("variant", variant)
            return button

        def _date_picker(self, key: str) -> QDateEdit:
            picker = self._register(key, QDateEdit())
            assert isinstance(picker, QDateEdit)
            picker.setCalendarPopup(True)
            picker.setDisplayFormat("yyyy-MM-dd")
            picker.setDate(QDate.currentDate())
            picker.setMinimumWidth(128)
            picker.setToolTip("カレンダーから日付を選択できます")
            calendar = picker.calendarWidget()
            if calendar is not None:
                calendar.setGridVisible(True)
            return picker

        @staticmethod
        def _date_to_qdate(value: date) -> QDate:
            return QDate(value.year, value.month, value.day)

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
            active_season = self._register(
                "active_season_status", QLabel("開催中のシーズン: 未確認")
            )
            assert isinstance(active_season, QLabel)
            active_season.setWordWrap(True)
            manual_row.addWidget(active_season)
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
            for key, text, variant, tooltip in (
                ("history_incomplete", "未完了処理", "primary", "未入力・下書きの戦績を確認します"),
                ("history_bulk", "一括編集", "secondary", "複数戦績の一括編集を開きます"),
                ("manual_duel_add", "手動追加", "primary", "録画なしの戦績を追加します"),
                ("history_play", "▶", "icon", "選択した録画を再生します"),
                ("history_duel", "✎", "icon", "選択した戦績を編集します"),
                ("history_delete", "削除", "danger", "選択した履歴または手動戦績を削除します"),
                ("history_duplicates", "重複", "secondary", "重複候補を確認します"),
                ("history_refresh", "更新", "secondary", "一覧を再読み込みします"),
                ("history_columns", "表示列", "secondary", "表示列を確認します"),
                ("history_youtube", "YouTube", "secondary", "選択した録画のYouTube投稿導線を確認します"),
            ):
                button = self._button(key, text, variant)
                button.setToolTip(tooltip)
                toolbar_layout.addWidget(button)
                self._connect_history_button(key, button)
            toolbar_layout.addStretch(1)
            layout.addWidget(toolbar)

            filters = self._register("history_filter_bar", QFrame())
            assert isinstance(filters, QFrame)
            filter_layout = QHBoxLayout(filters)
            filter_layout.setContentsMargins(0, 0, 0, 0)
            filter_layout.addWidget(QLabel("フィルター"))
            period = self._register("history_period_mode", QComboBox())
            assert isinstance(period, QComboBox)
            period.addItems(("すべて", "期間指定"))
            filter_layout.addWidget(period)
            filter_layout.addWidget(self._date_picker("history_date_from_picker"))
            filter_layout.addWidget(self._date_picker("history_date_to_picker"))
            apply_filter = self._button("history_filter_apply", "適用")
            clear_filter = self._button("history_filter_clear", "解除")
            apply_filter.clicked.connect(self._refresh_history)
            clear_filter.clicked.connect(self._clear_history_filters)
            filter_layout.addWidget(apply_filter)
            filter_layout.addWidget(clear_filter)
            for text in ("デッキ", "タグ", "登録元"):
                box = QComboBox()
                box.addItems((text, "すべて"))
                filter_layout.addWidget(box)
            history_add = self._button("history_add", "簡易入力")
            history_add.clicked.connect(self._show_manual_duel_entry)
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
            table.itemSelectionChanged.connect(self._update_history_action_states)
            layout.addWidget(self._register("history_table", table), stretch=1)
            self._update_history_action_states()

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
            prefix = "deck" if is_deck else "tag"
            editor, editor_layout = self._section(editor_key, title)
            grid = QGridLayout()
            grid.addWidget(QLabel("名前"), 0, 0)
            name = self._register(f"{prefix}_name_input", QLineEdit())
            assert isinstance(name, QLineEdit)
            grid.addWidget(name, 0, 1, 1, 3)
            grid.addWidget(QLabel("説明"), 1, 0)
            description = self._register(f"{prefix}_description_input", QLineEdit())
            assert isinstance(description, QLineEdit)
            grid.addWidget(description, 1, 1, 1, 3)
            grid.addWidget(QLabel("カラー"), 2, 0)
            color_button = self._register(f"{prefix}_color_button", QPushButton("色を選択"))
            assert isinstance(color_button, QPushButton)
            color_button.clicked.connect(lambda _checked=False, area=key: self._choose_catalog_color(area))
            self._set_color_button(color_button, "#2F6B5F" if is_deck else "#4F6F8F")
            grid.addWidget(color_button, 2, 1)
            if is_deck:
                opponent = self._register("deck_opponent_only", QCheckBox("相手デッキのみで使用"))
                hidden = self._register("deck_hidden_from_history", QCheckBox("履歴・統計で非表示"))
                assert isinstance(opponent, QCheckBox)
                assert isinstance(hidden, QCheckBox)
                grid.addWidget(opponent, 2, 2)
                grid.addWidget(hidden, 2, 3)
            else:
                deck_only = self._register("tag_deck_only", QCheckBox("デッキ名登録でのみ使用"))
                assert isinstance(deck_only, QCheckBox)
                grid.addWidget(deck_only, 2, 2)
            editor_layout.addLayout(grid)
            actions = QHBoxLayout()
            actions.addStretch(1)
            add_button = self._register(f"{prefix}_add", QPushButton("追加"))
            save_button = self._register(f"{prefix}_save", QPushButton("保存"))
            delete_button = self._register(f"{prefix}_delete", QPushButton("削除"))
            assert isinstance(add_button, QPushButton)
            assert isinstance(save_button, QPushButton)
            assert isinstance(delete_button, QPushButton)
            add_button.clicked.connect(lambda _checked=False, area=key: self._add_catalog_entry(area))
            save_button.clicked.connect(lambda _checked=False, area=key: self._save_catalog_entry(area))
            delete_button.clicked.connect(lambda _checked=False, area=key: self._delete_catalog_entry(area))
            actions.addWidget(add_button)
            actions.addWidget(save_button)
            actions.addWidget(delete_button)
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
            table.itemSelectionChanged.connect(
                lambda area=key: self._catalog_selection_changed(area)
            )
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
            name = self._register("season_name_input", QLineEdit())
            assert isinstance(name, QLineEdit)
            grid.addWidget(name, 0, 1)
            grid.addWidget(QLabel("種別"), 0, 2)
            type_box = self._register("season_type_select", QComboBox())
            assert isinstance(type_box, QComboBox)
            type_box.addItems(("ランク戦", "イベント", "カスタム"))
            grid.addWidget(type_box, 0, 3)
            grid.addWidget(QLabel("開始日"), 1, 0)
            grid.addWidget(self._date_picker("season_start_date_picker"), 1, 1)
            grid.addWidget(QLabel("終了日"), 1, 2)
            grid.addWidget(self._date_picker("season_end_date_picker"), 1, 3)
            grid.addWidget(QLabel("説明"), 2, 0)
            description = self._register("season_description_input", QLineEdit())
            assert isinstance(description, QLineEdit)
            grid.addWidget(description, 2, 1, 1, 3)
            editor_layout.addLayout(grid)
            actions = QHBoxLayout()
            actions.addStretch(1)
            for key, text, action in (
                ("season_add", "追加", self._add_season),
                ("season_save", "保存", self._save_selected_season),
                ("season_archive", "アーカイブ", self._archive_selected_season),
                ("season_report", "レポート", self._show_selected_season_report),
            ):
                button = self._register(key, QPushButton(text))
                assert isinstance(button, QPushButton)
                button.clicked.connect(action)
                actions.addWidget(button)
            editor_layout.addLayout(actions)
            layout.addWidget(editor)
            table = self._table(
                "season_table",
                ("シーズン", "種別", "期間", "状態"),
                (
                    ("WCS予選", "イベント", "2026-08-01 - 2026-08-20", "有効"),
                    ("ランク戦 8月", "ランク戦", "2026-08-01 - 2026-08-31", "有効"),
                ),
                column_widths=(220, 96, 210, 96),
            )
            table.itemSelectionChanged.connect(self._season_selection_changed)
            layout.addWidget(table, stretch=1)

        def _template_page(self, layout: QVBoxLayout) -> None:
            editor, editor_layout = self._section(
                "template_editor", "YouTube投稿テンプレート"
            )
            grid = QGridLayout()
            grid.addWidget(QLabel("タイトル"), 0, 0)
            title = self._register("youtube_template_title", QLineEdit("{date} {own_deck} 対 {opponent_deck}"))
            assert isinstance(title, QLineEdit)
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
            tags = self._register("youtube_template_tags", QLineEdit("MasterDuel, 遊戯王, {own_deck}"))
            assert isinstance(tags, QLineEdit)
            grid.addWidget(tags, 2, 1)
            editor_layout.addLayout(grid)
            variables = QLabel(
                "使用できる変数: {date}, {result}, {own_deck}, "
                "{opponent_deck}, {season}, {tags}"
            )
            variables.setWordWrap(True)
            editor_layout.addWidget(variables)
            status = self._register(
                "youtube_status",
                QLabel("YouTube連携は設定画面で管理します。テンプレートは投稿時の初期値です。"),
            )
            assert isinstance(status, QLabel)
            status.setWordWrap(True)
            editor_layout.addWidget(status)
            background_status = self._register(
                "youtube_background_status", QLabel("バックグラウンド処理: 待機中")
            )
            assert isinstance(background_status, QLabel)
            background_status.setWordWrap(True)
            editor_layout.addWidget(background_status)
            progress = self._register("youtube_upload_progress", QProgressBar())
            assert isinstance(progress, QProgressBar)
            progress.setRange(0, 0)
            progress.setTextVisible(False)
            progress.setVisible(False)
            editor_layout.addWidget(progress)
            save_row = QHBoxLayout()
            save_row.addStretch(1)
            list_button = self._register("youtube_template_list", QPushButton("準備一覧を更新"))
            save_button = self._register("youtube_template_save", QPushButton("保存"))
            assert isinstance(list_button, QPushButton)
            assert isinstance(save_button, QPushButton)
            list_button.clicked.connect(self._refresh_preparations)
            save_button.clicked.connect(self._save_youtube_template)
            save_row.addWidget(list_button)
            save_row.addWidget(save_button)
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
            refresh = self._register("reliability_refresh", QPushButton("状態更新"))
            setup = self._register("reliability_setup_check", QPushButton("初回導入を確認"))
            assert isinstance(refresh, QPushButton)
            assert isinstance(setup, QPushButton)
            refresh.clicked.connect(self.refresh_reliability_status)
            setup.clicked.connect(self.show_initial_setup_status)
            actions.addWidget(refresh)
            actions.addWidget(setup)
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

        def _settings_field(
            self,
            widget_key: str,
            config_key: str,
            default: object = "",
        ) -> QLineEdit:
            field = self._register(widget_key, QLineEdit(str(default)))
            assert isinstance(field, QLineEdit)
            self.setting_fields[widget_key] = field
            self.setting_field_keys[widget_key] = config_key
            return field

        def _settings_check(
            self,
            widget_key: str,
            config_key: str,
            text: str,
            default: bool = False,
        ) -> QCheckBox:
            check = self._register(widget_key, QCheckBox(text))
            assert isinstance(check, QCheckBox)
            check.setChecked(default)
            self.setting_checks[widget_key] = check
            self.setting_check_keys[widget_key] = config_key
            return check

        def _setting_label(self, text: str) -> QLabel:
            label = QLabel(text)
            label.setWordWrap(True)
            return label

        def _recording_settings_tab(self) -> QWidget:
            tab = QWidget()
            grid = QGridLayout(tab)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(2, 1)
            grid.addWidget(self._setting_label("録画設定"), 0, 0)
            select = self._register("settings_ffmpeg_select", QPushButton("既存FFmpegを選択"))
            assert isinstance(select, QPushButton)
            select.clicked.connect(self.select_existing_ffmpeg)
            grid.addWidget(select, 0, 1)
            setup = self._button("ffmpeg_setup", "FFmpegを導入")
            setup.clicked.connect(self.show_ffmpeg_setup)
            grid.addWidget(setup, 0, 2)
            grid.addWidget(QLabel("FFmpeg"), 1, 0, 1, 3)
            grid.addWidget(
                self._settings_field("settings_ffmpeg_path", "recorder.ffmpeg_path", "ffmpeg"),
                2,
                0,
                1,
                3,
            )

            grid.addWidget(QLabel("音声入力"), 3, 0, 1, 3)
            audio_mode = self._register("settings_audio_mode", QComboBox())
            assert isinstance(audio_mode, QComboBox)
            audio_mode.addItems(("Master Duelのみ（推奨）", "PC全体", "入力デバイス", "音声なし"))
            grid.addWidget(audio_mode, 4, 0)
            audio_input = self._register("settings_audio_input", QComboBox())
            assert isinstance(audio_input, QComboBox)
            audio_input.addItems(("Master Duel単体音声", "音声なし"))
            grid.addWidget(audio_input, 4, 1)
            refresh = self._register("settings_audio_refresh", QPushButton("候補更新"))
            test = self._register("settings_audio_test", QPushButton("テスト"))
            assert isinstance(refresh, QPushButton)
            assert isinstance(test, QPushButton)
            refresh.clicked.connect(self.refresh_audio_inputs)
            test.clicked.connect(self.test_selected_audio_input)
            audio_actions = QWidget()
            audio_actions_layout = QHBoxLayout(audio_actions)
            audio_actions_layout.setContentsMargins(0, 0, 0, 0)
            audio_actions_layout.addWidget(refresh)
            audio_actions_layout.addWidget(test)
            grid.addWidget(audio_actions, 4, 2)
            audio_status = self._register(
                "settings_audio_status",
                QLabel("音声入力候補を更新すると、DirectShow入力またはMaster Duel単体音声の状態を確認できます。"),
            )
            assert isinstance(audio_status, QLabel)
            audio_status.setWordWrap(True)
            grid.addWidget(audio_status, 5, 0, 1, 3)

            for column, (widget_key, config_key, label, default) in enumerate(
                (
                    ("settings_frame_rate", "recorder.frame_rate", "フレームレート", 30),
                    (
                        "settings_video_bitrate",
                        "recorder.video_bitrate_kbps",
                        "映像ビットレート(kbps)",
                        6000,
                    ),
                    ("settings_audio_gain", "recorder.audio_gain_db", "音声ゲイン(dB)", 0.0),
                )
            ):
                grid.addWidget(QLabel(label), 6, column)
                grid.addWidget(
                    self._settings_field(widget_key, config_key, default),
                    7,
                    column,
                )
            for column, (widget_key, config_key, label, default) in enumerate(
                (
                    ("settings_capture_width", "recorder.capture_width", "出力幅(0で元サイズ)", 0),
                    ("settings_capture_height", "recorder.capture_height", "出力高さ(0で元サイズ)", 0),
                    (
                        "settings_audio_sample_rate",
                        "recorder.audio_sample_rate",
                        "音声サンプルレート(Hz)",
                        48000,
                    ),
                )
            ):
                grid.addWidget(QLabel(label), 8, column)
                grid.addWidget(
                    self._settings_field(widget_key, config_key, default),
                    9,
                    column,
                )
            grid.addWidget(QLabel("音声チャンネル"), 10, 0)
            grid.addWidget(
                self._settings_field("settings_audio_channels", "recorder.audio_channels", 2),
                11,
                0,
            )
            grid.addWidget(
                self._settings_check(
                    "settings_auto_start",
                    "detection.auto_start_recording",
                    "ウィンドウ検出時に自動開始",
                    True,
                ),
                12,
                0,
            )
            grid.addWidget(
                self._settings_check(
                    "settings_auto_stop",
                    "detection.auto_stop_recording",
                    "ウィンドウ消失時に自動停止",
                    True,
                ),
                12,
                1,
            )
            grid.addWidget(
                self._settings_check(
                    "settings_visual_detection",
                    "detection.visual_events_enabled",
                    "対戦イベントを自動判定",
                    True,
                ),
                12,
                2,
            )
            grid.addWidget(
                self._settings_check(
                    "settings_windows_notifications",
                    "detection.windows_notifications_enabled",
                    "録画イベントをWindows通知",
                    True,
                ),
                13,
                0,
                1,
                3,
            )
            for column, (widget_key, config_key, label, default) in enumerate(
                (
                    (
                        "settings_visual_fps",
                        "detection.visual_maximum_fps",
                        "自動判定fps(最大2)",
                        2.0,
                    ),
                    (
                        "settings_visual_language",
                        "detection.visual_language",
                        "UI言語(auto / ja / en)",
                        "auto",
                    ),
                    (
                        "settings_visual_confidence",
                        "detection.visual_minimum_confidence",
                        "候補閾値(0.70以上)",
                        0.7,
                    ),
                )
            ):
                grid.addWidget(QLabel(label), 14, column)
                grid.addWidget(
                    self._settings_field(widget_key, config_key, default),
                    15,
                    column,
                )
            grid.addWidget(QLabel("データ保存先"), 16, 0)
            runtime = self._register(
                "settings_runtime_path", QLabel(str(self.service.runtime_data_directory()))
            )
            assert isinstance(runtime, QLabel)
            runtime.setWordWrap(True)
            grid.addWidget(runtime, 17, 0, 1, 2)
            runtime_change = self._register("settings_runtime_change", QPushButton("保存先を変更"))
            assert isinstance(runtime_change, QPushButton)
            runtime_change.clicked.connect(self.change_runtime_data_directory)
            grid.addWidget(runtime_change, 17, 2)
            settings_form = self._register(
                "settings_form",
                QLabel("通常設定 / 外部連携 / データ保護 / 危険操作をV1.x相当の密度で確認できます。"),
            )
            assert isinstance(settings_form, QLabel)
            settings_form.setWordWrap(True)
            grid.addWidget(settings_form, 18, 0, 1, 2)
            reload_button = self._register("settings_reload", QPushButton("設定を再読込"))
            save_button = self._register("settings_save", QPushButton("設定を保存"))
            assert isinstance(reload_button, QPushButton)
            assert isinstance(save_button, QPushButton)
            reload_button.clicked.connect(self.load_settings)
            save_button.clicked.connect(self.save_settings)
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.addWidget(reload_button)
            actions_layout.addWidget(save_button)
            grid.addWidget(actions, 18, 2)
            settings_form = self._register(
                "settings_status",
                QLabel("設定を読み込みました"),
            )
            assert isinstance(settings_form, QLabel)
            grid.addWidget(settings_form, 19, 0, 1, 3)
            return tab

        def _youtube_settings_tab(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(self._setting_label("YouTube連携"))
            status = self._register(
                "settings_youtube_status",
                QLabel("YouTube連携状態を確認していません"),
            )
            scope = self._register("settings_youtube_scope", QLabel(""))
            assert isinstance(status, QLabel)
            assert isinstance(scope, QLabel)
            status.setWordWrap(True)
            scope.setWordWrap(True)
            layout.addWidget(status)
            layout.addWidget(scope)
            row = QHBoxLayout()
            for key, text, action in (
                ("settings_youtube_connect", "連携する", self.connect_youtube),
                ("settings_youtube_disconnect", "切断する", self.disconnect_youtube),
                ("settings_youtube_refresh", "接続確認", self.refresh_youtube_status),
                (
                    "settings_youtube_test_upload",
                    "最新録画でprivateテスト投稿",
                    self.open_latest_youtube_test_upload,
                ),
            ):
                button = self._register(key, QPushButton(text))
                assert isinstance(button, QPushButton)
                button.clicked.connect(action)
                row.addWidget(button)
            row.addStretch(1)
            layout.addLayout(row)
            note = QLabel("OAuth資格情報、refresh token、認可コードは画面・設定・DB・ログへ保存しません。")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addStretch(1)
            return tab

        def _data_settings_tab(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(self._setting_label("履歴・デッキ・タグ・シーズン"))
            managed = QHBoxLayout()
            for key, text, action in (
                ("settings_managed_export", "管理データを書き出し", self.export_managed_data),
                ("settings_managed_import", "管理データを読み込み", self.import_managed_data),
            ):
                button = self._register(key, QPushButton(text))
                assert isinstance(button, QPushButton)
                button.clicked.connect(action)
                managed.addWidget(button)
            managed.addStretch(1)
            layout.addLayout(managed)
            reset = QHBoxLayout()
            for key, scope_name, text in (
                ("settings_reset_history", "history", "履歴情報を初期化"),
                ("settings_reset_decks", "decks", "デッキを初期化"),
                ("settings_reset_tags", "tags", "タグを初期化"),
                ("settings_reset_seasons", "seasons", "シーズンを初期化"),
            ):
                button = self._register(key, QPushButton(text))
                assert isinstance(button, QPushButton)
                button.clicked.connect(
                    lambda _checked=False, selected=scope_name, label=text: self.reset_managed_data(
                        selected, label
                    )
                )
                reset.addWidget(button)
            reset.addStretch(1)
            layout.addLayout(reset)
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
            for key, text, action in (
                ("settings_data_backup", "バックアップ", self.create_data_backup),
                ("settings_data_restore", "復元", self.restore_data_backup),
                ("settings_data_diagnosis", "整合性診断", self.run_data_integrity_diagnosis),
            ):
                button = self._register(key, QPushButton(text))
                assert isinstance(button, QPushButton)
                button.clicked.connect(action)
                actions.addWidget(button)
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
            layout.addWidget(self._setting_label("戦績CSV入出力"))
            layout.addWidget(
                QLabel("スプレッドシートとの移行用です。録画ファイルや設定のバックアップには管理データを使用してください。")
            )
            status = self._register(
                "csv_status",
                QLabel("取込時は全行を検証し、適用前に追加・更新件数を表示します。"),
            )
            assert isinstance(status, QLabel)
            status.setWordWrap(True)
            layout.addWidget(status)
            row = QHBoxLayout()
            for key, text, action in (
                ("settings_csv_export", "CSVを書き出し", self.export_duel_csv),
                ("settings_csv_import", "CSVを取り込み", self.import_duel_csv),
                ("settings_csv_sample", "サンプルCSVを保存", self.export_duel_csv_sample),
            ):
                button = self._register(key, QPushButton(text))
                assert isinstance(button, QPushButton)
                button.clicked.connect(action)
                row.addWidget(button)
            row.addStretch(1)
            layout.addLayout(row)
            layout.addStretch(1)
            return tab

        def _display_settings_tab(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(self._setting_label("戦績管理の表示色"))
            colors = self._register(
                "settings_display_colors",
                QLabel(
                    "勝敗、先後、コイン、登録元のセル色を管理します。色だけに依存せず、文字表記も維持します。"
                ),
            )
            assert isinstance(colors, QLabel)
            colors.setWordWrap(True)
            layout.addWidget(colors)
            color_table = QTableWidget(0, 2)
            color_table.setHorizontalHeaderLabels(("対象", "色"))
            self._configure_table(color_table, column_widths=(240, 120), minimum_height=220)
            rows = tuple((key, self.ui_preferences.history_cell_colors.get(key, "#FFFFFF")) for key in self.ui_preferences.history_cell_colors)
            self._set_table_rows(color_table, rows)
            layout.addWidget(color_table)
            layout.addWidget(self._setting_label("戦績管理のダブルクリック"))
            play = self._register("settings_double_click_play", QCheckBox("録画再生"))
            edit = self._register("settings_double_click_edit", QCheckBox("戦績編集"))
            assert isinstance(play, QCheckBox)
            assert isinstance(edit, QCheckBox)
            play.setChecked(self.ui_preferences.history_double_click_action == "play")
            edit.setChecked(self.ui_preferences.history_double_click_action == "edit")
            play.clicked.connect(lambda _checked=False: self._set_history_double_click_action("play"))
            edit.clicked.connect(lambda _checked=False: self._set_history_double_click_action("edit"))
            row = QHBoxLayout()
            row.addWidget(play)
            row.addWidget(edit)
            row.addStretch(1)
            layout.addLayout(row)
            layout.addStretch(1)
            return tab

        def _update_settings_tab(self) -> QWidget:
            tab = QWidget()
            layout = QVBoxLayout(tab)
            layout.addWidget(self._setting_label("アプリ更新"))
            auto = self._register("app_update_auto_check", QCheckBox("起動後に新しい正式版を確認する"))
            assert isinstance(auto, QCheckBox)
            auto.setChecked(self.ui_preferences.automatic_update_check)
            auto.clicked.connect(self._save_ui_preferences)
            layout.addWidget(auto)
            status = self._register(
                "app_update_status",
                QLabel(f"現在のバージョン: {__version__} / まだ更新を確認していません"),
            )
            assert isinstance(status, QLabel)
            status.setWordWrap(True)
            layout.addWidget(status)
            row = QHBoxLayout()
            check = self._register("app_update", QPushButton("更新を確認"))
            download = self._register("app_update_download", QPushButton("ダウンロードして更新"))
            assert isinstance(check, QPushButton)
            assert isinstance(download, QPushButton)
            check.clicked.connect(self.check_for_updates)
            download.clicked.connect(self.download_and_apply_update)
            download.setEnabled(False)
            row.addWidget(check)
            row.addWidget(download)
            row.addStretch(1)
            layout.addLayout(row)
            note = QLabel(
                "更新候補は、GUI EXE・updater EXE・SHA-256が揃った通常Releaseだけを対象にします。"
            )
            note.setWordWrap(True)
            layout.addWidget(note)
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
                self._refresh_active_seasons,
                self._refresh_youtube,
                self._refresh_preparations,
                self._refresh_data_protection,
                self._refresh_statistics,
                self._refresh_health_status,
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

        def _refresh_active_seasons(self) -> None:
            label = self.widgets.get("active_season_status")
            if not isinstance(label, QLabel):
                return
            try:
                summaries = self.service.active_season_summaries()
            except Exception as exc:
                label.setText(f"開催中のシーズンを確認できません: {exc}")
                return
            if not summaries:
                label.setText("開催中のシーズン: なし")
                return
            names = " / ".join(summary.season.name for summary in summaries)
            label.setText(f"開催中のシーズン: {names}")

        def _refresh_health_status(self) -> None:
            label = self.widgets.get("nav_health_status")
            if not isinstance(label, QLabel):
                return
            try:
                report = self.service.diagnose()
            except Exception as exc:
                label.setText(f"△ 状態確認失敗: {exc}")
                return
            errors = [check for check in report.checks if getattr(check.status, "value", "") == "error"]
            warnings = [check for check in report.checks if getattr(check.status, "value", "") == "warning"]
            if errors:
                label.setText(f"△ 要確認: {errors[0].label}")
            elif warnings:
                label.setText(f"△ 注意: {warnings[0].label}")
            else:
                label.setText("✓ 利用可能")

        def _start_recording(self) -> None:
            self._run_action("録画開始", self.service.start_recording)

        def _stop_recording(self) -> None:
            self._run_action("録画停止", self.service.stop_recording)

        def _toggle_watch(self) -> None:
            if self.service.watch_active:
                self._run_action("自動監視停止", self.service.stop_watch)
            else:
                self._run_action("自動監視開始", self.service.start_watch)

        def _connect_history_button(self, key: str, button: QPushButton) -> None:
            actions = {
                "history_incomplete": self._show_incomplete_duels,
                "history_bulk": self._show_bulk_duel_editor,
                "manual_duel_add": self._show_manual_duel_entry,
                "history_play": self._play_selected_history,
                "history_duel": self._show_selected_duel_editor,
                "history_delete": self._delete_selected_history,
                "history_duplicates": self._show_duplicate_candidates,
                "history_refresh": self._load_runtime_dashboard,
                "history_columns": self._show_history_columns,
                "history_youtube": self._show_selected_youtube_flow,
            }
            button.clicked.connect(actions[key])

        def _history_query(self) -> DuelManagementQuery:
            period = self.widgets.get("history_period_mode")
            if not isinstance(period, QComboBox) or period.currentText() != "期間指定":
                return DuelManagementQuery(limit=200)
            date_from = self.widgets.get("history_date_from_picker")
            date_to = self.widgets.get("history_date_to_picker")
            return DuelManagementQuery(
                limit=200,
                occurred_from=date_from.date().toPython()
                if isinstance(date_from, QDateEdit)
                else None,
                occurred_to=date_to.date().toPython()
                if isinstance(date_to, QDateEdit)
                else None,
            )

        def _clear_history_filters(self) -> None:
            period = self.widgets.get("history_period_mode")
            if isinstance(period, QComboBox):
                period.setCurrentText("すべて")
            self._refresh_history()

        def _selected_history_view(self) -> object | None:
            table = self.widgets.get("history_table")
            if not isinstance(table, QTableWidget):
                return None
            row = table.currentRow()
            if row < 0:
                return None
            item = table.item(row, 0)
            if item is None:
                return None
            row_id = item.data(Qt.ItemDataRole.UserRole)
            return self.history_views_by_row_id.get(str(row_id))

        def _update_history_action_states(self) -> None:
            selected = self._selected_history_view()
            has_selection = selected is not None
            has_recording = bool(getattr(selected, "recording_id", None))
            write_blocked = self.service.duel_write_block_reason() is not None
            button_states = {
                "history_play": has_recording,
                "history_duel": has_selection,
                "history_delete": has_selection and not write_blocked,
                "history_youtube": has_recording,
            }
            for key, enabled in button_states.items():
                button = self.widgets.get(key)
                if isinstance(button, QPushButton):
                    button.setEnabled(enabled)

        def _show_incomplete_duels(self) -> None:
            try:
                items = self.service.list_incomplete_duels()
            except Exception as exc:
                self._show_warning("未完了処理を確認できません", str(exc))
                return
            self._show_information("未完了処理", f"未完了の戦績は{len(items)}件です。")

        def _show_bulk_duel_editor(self) -> None:
            self._show_information(
                "一括編集",
                "一括編集は対象行の複数選択と適用前確認を前提にします。"
                "この画面では操作入口を確認できます。",
            )

        def _show_manual_duel_entry(self) -> None:
            self._show_information(
                "手動追加",
                "録画なし戦績の追加は、保存前確認つきの入力画面として扱います。"
                "既存データはこの案内だけでは変更しません。",
            )

        def _play_selected_history(self) -> None:
            selected = self._selected_history_view()
            recording_id = getattr(selected, "recording_id", None)
            if not recording_id:
                self._show_information("録画再生", "録画がある行を選択してください。")
                return
            try:
                from .pyside_review import PySideReviewError, create_review_window

                review_window = create_review_window(
                    service=self.service,
                    recording_id=recording_id,
                    parent=self,
                )
            except PySideReviewError as exc:
                self._fallback_to_external_player(recording_id, reason=str(exc))
                return
            except Exception as exc:
                self._show_warning("レビュー画面を開けません", str(exc))
                self._append_activity("レビュー画面の起動に失敗しました")
                return
            self.review_windows.append(review_window)
            review_window.destroyed.connect(
                lambda _object=None, window=review_window: self._forget_review_window(window)
            )
            review_window.show()
            self._append_activity(f"レビュー画面を開きました: {recording_id}")

        def _fallback_to_external_player(self, recording_id: str, *, reason: str) -> None:
            try:
                self.service.play_recording(recording_id)
            except Exception as exc:
                self._show_warning(
                    "録画再生に失敗しました",
                    f"{reason}\n外部プレイヤーでも開けませんでした: {exc}",
                )
                self._append_activity("録画再生に失敗しました")
                return
            self._append_activity(f"外部プレイヤーで開きました: {recording_id}")

        def _forget_review_window(self, window: QWidget) -> None:
            if window in self.review_windows:
                self.review_windows.remove(window)

        def _show_selected_duel_editor(self) -> None:
            selected = self._selected_history_view()
            if selected is None:
                self._show_information("戦績編集", "編集する行を選択してください。")
                return
            self._show_information(
                "戦績編集",
                f"選択中の戦績を編集します: {getattr(selected, 'row_id', '-')}",
            )

        def _delete_selected_history(self) -> None:
            selected = self._selected_history_view()
            if selected is None:
                self._show_information("削除", "削除する行を選択してください。")
                return
            if self.service.duel_write_block_reason() is not None:
                self._show_warning("削除できません", self.service.duel_write_block_reason() or "")
                return
            if QMessageBox.question(
                self,
                "削除",
                "選択した履歴または手動戦績を削除します。録画または自動監視中は実行できません。",
            ) != QMessageBox.StandardButton.Yes:
                self._append_activity("削除をキャンセルしました")
                return
            recording_id = getattr(selected, "recording_id", None)
            duel_record = getattr(selected, "duel_record", None)
            try:
                if recording_id:
                    result = self.service.delete_history(recording_id)
                elif duel_record is not None:
                    result = self.service.delete_duel_record(duel_record.duel_id)
                else:
                    self._show_warning("削除できません", "削除対象を確認できません。")
                    return
            except Exception as exc:
                self._show_warning("削除できません", str(exc))
                return
            self._append_activity(f"削除しました: {result}")
            self._refresh_history()

        def _show_duplicate_candidates(self) -> None:
            try:
                candidates = self.service.duplicate_duel_candidates()
            except Exception as exc:
                self._show_warning("重複候補を確認できません", str(exc))
                return
            self._show_information("重複候補", f"重複候補は{len(candidates)}件です。")

        def _show_history_columns(self) -> None:
            self._show_information(
                "表示列",
                "開始日時、デッキ名、勝敗、先後、コイン、対戦種別、時間、サイズ、相手デッキ、登録元を表示します。",
            )

        def _show_selected_youtube_flow(self) -> None:
            selected = self._selected_history_view()
            recording_id = getattr(selected, "recording_id", None)
            if not recording_id:
                self._show_information("YouTube", "録画がある行を選択してください。")
                return
            if self.youtube_upload_running:
                self._show_information(
                    "YouTube", "YouTube投稿をバックグラウンドで実行中です。"
                )
                return
            try:
                status = self.service.youtube_preparation_status(recording_id)
                dialog = self.service.get_youtube_upload_dialog_data(recording_id)
            except Exception as exc:
                self._show_warning("YouTube投稿を確認できません", str(exc))
                return
            if dialog.youtube_watch_url:
                self._show_information(
                    "YouTube",
                    f"投稿済みです。\n{dialog.youtube_watch_url}",
                )
                return
            if QMessageBox.question(
                self,
                "YouTube投稿",
                f"{status.message}\n\n{dialog.title}をバックグラウンドで投稿しますか？",
            ) != QMessageBox.StandardButton.Yes:
                return
            self._start_youtube_upload(dialog)

        def _start_youtube_upload(self, dialog: object) -> None:
            self.youtube_upload_running = True
            self._set_youtube_background_status(
                True, f"YouTube投稿中: {getattr(dialog, 'title')}"
            )

            def operation() -> object:
                return self.service.upload_history_to_youtube(
                    recording_id=str(getattr(dialog, "recording_id")),
                    title=str(getattr(dialog, "title")),
                    description=str(getattr(dialog, "description")),
                    tags=tuple(getattr(dialog, "tags")),
                    privacy=str(getattr(dialog, "privacy")),
                )

            self._submit_background_task(
                "YouTube投稿",
                operation,
                self._youtube_upload_completed,
            )

        def _youtube_upload_completed(self, result: object) -> None:
            upload = getattr(result, "upload", None)
            url = getattr(upload, "watch_url", None)
            message = getattr(result, "message", "YouTube投稿が完了しました")
            self.youtube_upload_running = False
            self._set_youtube_background_status(
                False,
                f"YouTube投稿完了: {url or message}",
            )
            self._refresh_history()
            self._refresh_youtube()

        def _submit_background_task(
            self, label: str, operation: Any, on_success: Any | None = None
        ) -> None:
            future = self.background_executor.submit(operation)
            self.background_tasks.append((label, future, on_success))
            if not self.background_timer.isActive():
                self.background_timer.start()

        def _poll_background_tasks(self) -> None:
            remaining: list[tuple[str, concurrent.futures.Future[object], Any]] = []
            for label, future, on_success in self.background_tasks:
                if not future.done():
                    remaining.append((label, future, on_success))
                    continue
                try:
                    result = future.result()
                except Exception as exc:
                    if label == "YouTube投稿":
                        self.youtube_upload_running = False
                        self._set_youtube_background_status(
                            False, f"YouTube投稿に失敗しました: {exc}"
                        )
                    self._append_activity(f"{label}に失敗しました")
                    continue
                if on_success is not None:
                    on_success(result)
                else:
                    self._append_activity(f"{label}が完了しました")
            self.background_tasks = remaining
            if not self.background_tasks:
                self.background_timer.stop()

        def _set_youtube_background_status(self, busy: bool, message: str) -> None:
            status = self.widgets.get("youtube_background_status")
            if isinstance(status, QLabel):
                status.setText(message)
            progress = self.widgets.get("youtube_upload_progress")
            if isinstance(progress, QProgressBar):
                progress.setVisible(busy)
            button = self.widgets.get("history_youtube")
            if isinstance(button, QPushButton):
                button.setEnabled(not busy)
            self._append_activity(message)

        def _refresh_history(self) -> None:
            dashboard = self.service.get_history_dashboard(query=self._history_query())
            table = self.widgets["history_table"]
            assert isinstance(table, QTableWidget)
            rows = []
            deck_colors = []
            self.history_views_by_row_id = {view.row_id: view for view in dashboard.views}
            for view in dashboard.views:
                deck_colors.append(view.own_deck_color)
                rows.append(history_table_display_row(view))
            self._set_table_rows(table, rows)
            for row_index, view in enumerate(dashboard.views):
                item = table.item(row_index, 0)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole, view.row_id)
            for row_index, color in enumerate(deck_colors):
                self._decorate_item_with_color(table.item(row_index, 1), color)
            incomplete = self.widgets["incomplete_duel_count"]
            assert isinstance(incomplete, QLabel)
            incomplete.setText(
                f"戦績管理 未完了 {dashboard.incomplete_duel_record_count}件"
            )
            self._update_history_action_states()

        def _refresh_catalogs(self) -> None:
            deck_table = self.widgets["deck_catalog_table"]
            tag_table = self.widgets["tag_catalog_table"]
            assert isinstance(deck_table, QTableWidget)
            assert isinstance(tag_table, QTableWidget)
            decks = self.service.list_decks()
            tags = self.service.list_tags()
            self.catalog_entries_by_id = {
                entry.entry_id: entry for entry in (*decks, *tags)
            }
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
                    for deck in decks
                ),
            )
            for row_index, deck in enumerate(decks):
                item = deck_table.item(row_index, 0)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole, deck.entry_id)
            self._select_row_by_identifier(
                deck_table, self.selected_catalog_entry_ids.get("decks")
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
                    for tag in tags
                ),
            )
            for row_index, tag in enumerate(tags):
                item = tag_table.item(row_index, 0)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole, tag.entry_id)
            self._select_row_by_identifier(
                tag_table, self.selected_catalog_entry_ids.get("tags")
            )
            self._catalog_selection_changed("decks")
            self._catalog_selection_changed("tags")

        def _refresh_seasons(self) -> None:
            table = self.widgets["season_table"]
            assert isinstance(table, QTableWidget)
            seasons = self.service.list_seasons(include_archived=True)
            self.seasons_by_id = {season.season_id: season for season in seasons}
            self._set_table_rows(
                table,
                tuple(
                    (
                        season.name,
                        season.season_type,
                        f"{season.start_date} - {season.end_date}",
                        "アーカイブ" if season.is_archived else "有効",
                    )
                    for season in seasons
                ),
            )
            for row_index, season in enumerate(seasons):
                item = table.item(row_index, 0)
                if item is not None:
                    item.setData(Qt.ItemDataRole.UserRole, season.season_id)
            self._select_row_by_identifier(table, self.selected_season_id)
            self._season_selection_changed()

        def _select_row_by_identifier(
            self, table: QTableWidget, identifier: int | None
        ) -> None:
            if identifier is None:
                table.clearSelection()
                return
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item is not None and item.data(Qt.ItemDataRole.UserRole) == identifier:
                    table.selectRow(row)
                    return
            table.clearSelection()

        def _selected_table_identifier(self, table_key: str) -> int | None:
            table = self.widgets.get(table_key)
            if not isinstance(table, QTableWidget):
                return None
            row = table.currentRow()
            if row < 0:
                return None
            item = table.item(row, 0)
            if item is None:
                return None
            value = item.data(Qt.ItemDataRole.UserRole)
            return int(value) if value is not None else None

        def _catalog_selection_changed(self, key: str) -> None:
            table_key = "deck_catalog_table" if key == "decks" else "tag_catalog_table"
            selected_id = self._selected_table_identifier(table_key)
            self.selected_catalog_entry_ids[key] = selected_id
            entry = self.catalog_entries_by_id.get(selected_id) if selected_id else None
            self._fill_catalog_form(key, entry)

        def _fill_catalog_form(self, key: str, entry: object | None) -> None:
            prefix = "deck" if key == "decks" else "tag"
            name = self.widgets.get(f"{prefix}_name_input")
            description = self.widgets.get(f"{prefix}_description_input")
            color_button = self.widgets.get(f"{prefix}_color_button")
            if isinstance(name, QLineEdit):
                name.setText(str(getattr(entry, "name", "")) if entry is not None else "")
            if isinstance(description, QLineEdit):
                description.setText(
                    str(getattr(entry, "description", "")) if entry is not None else ""
                )
            color = str(
                getattr(entry, "color", "#2F6B5F" if key == "decks" else "#4F6F8F")
                or ("#2F6B5F" if key == "decks" else "#4F6F8F")
            )
            if isinstance(color_button, QPushButton):
                self._set_color_button(color_button, color)
            if key == "decks":
                opponent = self.widgets.get("deck_opponent_only")
                hidden = self.widgets.get("deck_hidden_from_history")
                if isinstance(opponent, QCheckBox):
                    opponent.setChecked(bool(getattr(entry, "opponent_only", False)))
                if isinstance(hidden, QCheckBox):
                    hidden.setChecked(
                        bool(getattr(entry, "hidden_from_history_statistics", False))
                    )
            else:
                deck_only = self.widgets.get("tag_deck_only")
                if isinstance(deck_only, QCheckBox):
                    deck_only.setChecked(bool(getattr(entry, "deck_only", False)))

        def _catalog_values(self, key: str) -> dict[str, object]:
            prefix = "deck" if key == "decks" else "tag"
            name = self.widgets.get(f"{prefix}_name_input")
            description = self.widgets.get(f"{prefix}_description_input")
            color_button = self.widgets.get(f"{prefix}_color_button")
            values: dict[str, object] = {
                "name": name.text().strip() if isinstance(name, QLineEdit) else "",
                "description": description.text().strip()
                if isinstance(description, QLineEdit)
                else "",
                "color": color_button.property("catalogColor")
                if isinstance(color_button, QPushButton)
                else ("#2F6B5F" if key == "decks" else "#4F6F8F"),
            }
            if key == "decks":
                opponent = self.widgets.get("deck_opponent_only")
                hidden = self.widgets.get("deck_hidden_from_history")
                values["opponent_only"] = (
                    opponent.isChecked() if isinstance(opponent, QCheckBox) else False
                )
                values["hidden_from_history_statistics"] = (
                    hidden.isChecked() if isinstance(hidden, QCheckBox) else False
                )
            else:
                deck_only = self.widgets.get("tag_deck_only")
                values["deck_only"] = (
                    deck_only.isChecked() if isinstance(deck_only, QCheckBox) else False
                )
            return values

        def _add_catalog_entry(self, key: str) -> None:
            values = self._catalog_values(key)
            try:
                if key == "decks":
                    saved = self.service.add_deck(
                        str(values["name"]),
                        description=str(values["description"]),
                        color=str(values["color"]),
                    )
                    saved = self.service.update_deck(saved.entry_id, **values)
                else:
                    saved = self.service.add_tag(
                        str(values["name"]),
                        description=str(values["description"]),
                        color=str(values["color"]),
                        deck_only=bool(values["deck_only"]),
                    )
            except Exception as exc:
                self._show_warning("追加できません", str(exc))
                return
            self.selected_catalog_entry_ids[key] = saved.entry_id
            self._append_activity(f"{saved.name}を追加しました")
            self._refresh_catalogs()

        def _save_catalog_entry(self, key: str) -> None:
            entry_id = self.selected_catalog_entry_ids.get(key)
            if entry_id is None:
                self._show_information("保存", "保存する行を選択してください。")
                return
            values = self._catalog_values(key)
            try:
                if key == "decks":
                    saved = self.service.update_deck(entry_id, **values)
                else:
                    saved = self.service.update_tag(entry_id, **values)
            except Exception as exc:
                self._show_warning("保存できません", str(exc))
                return
            self.selected_catalog_entry_ids[key] = saved.entry_id
            self._append_activity(f"{saved.name}を保存しました")
            self._refresh_catalogs()

        def _delete_catalog_entry(self, key: str) -> None:
            entry_id = self.selected_catalog_entry_ids.get(key)
            if entry_id is None:
                self._show_information("削除", "削除する行を選択してください。")
                return
            entry = self.catalog_entries_by_id.get(entry_id)
            name = getattr(entry, "name", str(entry_id))
            if QMessageBox.question(
                self,
                "削除",
                f"{name}を削除またはアーカイブします。参照中の戦績は保持します。",
            ) != QMessageBox.StandardButton.Yes:
                return
            try:
                removed = self.service.delete_duel_catalog_entry(entry_id)
            except Exception as exc:
                self._show_warning("削除できません", str(exc))
                return
            self.selected_catalog_entry_ids[key] = None
            self._append_activity(f"{removed.name}を削除しました")
            self._refresh_catalogs()

        def _choose_catalog_color(self, key: str) -> None:
            prefix = "deck" if key == "decks" else "tag"
            button = self.widgets.get(f"{prefix}_color_button")
            if not isinstance(button, QPushButton):
                return
            current = QColor(str(button.property("catalogColor") or "#2F6B5F"))
            selected = QColorDialog.getColor(current, self, "色を選択")
            if selected.isValid():
                self._set_color_button(button, selected.name().upper())

        def _set_color_button(self, button: QPushButton, color: str) -> None:
            qcolor = QColor(color)
            if not qcolor.isValid():
                qcolor = QColor("#2F6B5F")
            button.setProperty("catalogColor", qcolor.name().upper())
            button.setText("色を選択")
            button.setToolTip("現在の色をスウォッチで表示しています")
            button.setStyleSheet(
                "QPushButton {"
                f"border-left: 28px solid {qcolor.name()};"
                "text-align: center;"
                "}"
            )

        def _season_selection_changed(self) -> None:
            selected_id = self._selected_table_identifier("season_table")
            self.selected_season_id = selected_id
            season = self.seasons_by_id.get(selected_id) if selected_id else None
            name = self.widgets.get("season_name_input")
            type_box = self.widgets.get("season_type_select")
            start = self.widgets.get("season_start_date_picker")
            end = self.widgets.get("season_end_date_picker")
            description = self.widgets.get("season_description_input")
            if isinstance(name, QLineEdit):
                name.setText(str(getattr(season, "name", "")) if season else "")
            if isinstance(type_box, QComboBox):
                label = {
                    "ranked": "ランク戦",
                    "event": "イベント",
                    "custom": "カスタム",
                }.get(str(getattr(season, "season_type", "ranked")), "ランク戦")
                type_box.setCurrentText(label)
            if isinstance(start, QDateEdit):
                value = getattr(season, "start_date", date.today())
                start.setDate(self._date_to_qdate(value))
            if isinstance(end, QDateEdit):
                value = getattr(season, "end_date", date.today())
                end.setDate(self._date_to_qdate(value))
            if isinstance(description, QLineEdit):
                description.setText(
                    str(getattr(season, "description", "")) if season else ""
                )

        def _season_values(self) -> dict[str, object]:
            name = self.widgets.get("season_name_input")
            type_box = self.widgets.get("season_type_select")
            start = self.widgets.get("season_start_date_picker")
            end = self.widgets.get("season_end_date_picker")
            description = self.widgets.get("season_description_input")
            season_type = {
                "ランク戦": "ranked",
                "イベント": "event",
                "カスタム": "custom",
            }.get(type_box.currentText() if isinstance(type_box, QComboBox) else "", "ranked")
            return {
                "name": name.text().strip() if isinstance(name, QLineEdit) else "",
                "season_type": season_type,
                "duel_type": {
                    "ranked": "ranked",
                    "event": "event",
                    "custom": "other",
                }[season_type],
                "start_date": start.date().toPython()
                if isinstance(start, QDateEdit)
                else date.today(),
                "end_date": end.date().toPython()
                if isinstance(end, QDateEdit)
                else date.today(),
                "description": description.text().strip()
                if isinstance(description, QLineEdit)
                else "",
                "report_notes": str(
                    getattr(
                        self.seasons_by_id.get(self.selected_season_id or 0),
                        "report_notes",
                        "",
                    )
                ),
            }

        def _add_season(self, *_args: object) -> None:
            try:
                saved = self.service.add_season(**self._season_values())
            except Exception as exc:
                self._show_warning("シーズンを追加できません", str(exc))
                return
            self.selected_season_id = saved.season_id
            self._append_activity(f"{saved.name}を追加しました")
            self._refresh_seasons()
            self._refresh_active_seasons()

        def _save_selected_season(self, *_args: object) -> None:
            if self.selected_season_id is None:
                self._show_information("保存", "保存するシーズンを選択してください。")
                return
            try:
                saved = self.service.update_season(
                    self.selected_season_id, **self._season_values()
                )
            except Exception as exc:
                self._show_warning("シーズンを保存できません", str(exc))
                return
            self.selected_season_id = saved.season_id
            self._append_activity(f"{saved.name}を保存しました")
            self._refresh_seasons()
            self._refresh_active_seasons()

        def _archive_selected_season(self, *_args: object) -> None:
            if self.selected_season_id is None:
                self._show_information("アーカイブ", "シーズンを選択してください。")
                return
            season = self.seasons_by_id.get(self.selected_season_id)
            name = getattr(season, "name", str(self.selected_season_id))
            if QMessageBox.question(
                self,
                "シーズンをアーカイブ",
                f"{name}をアーカイブします。戦績データは保持します。",
            ) != QMessageBox.StandardButton.Yes:
                return
            try:
                saved = self.service.archive_season_report(self.selected_season_id)
            except Exception as exc:
                self._show_warning("シーズンをアーカイブできません", str(exc))
                return
            self._append_activity(f"{saved.name}をアーカイブしました")
            self._refresh_seasons()
            self._refresh_active_seasons()

        def _show_selected_season_report(self, *_args: object) -> None:
            if self.selected_season_id is None:
                self._show_information("レポート", "シーズンを選択してください。")
                return
            try:
                report = self.service.get_season_report(self.selected_season_id)
            except Exception as exc:
                self._show_warning("シーズンレポートを表示できません", str(exc))
                return
            metric = report.summary.filtered
            self._show_information(
                "シーズンレポート",
                f"{report.season.name}\n"
                f"{metric.matches}戦 {metric.wins}勝 / 勝率 {self._format_rate(metric.win_rate)}",
            )

        def _refresh_youtube(self) -> None:
            status = self.service.youtube_connection_status()
            status_label = self.widgets["youtube_status"]
            assert isinstance(status_label, QLabel)
            status_label.setText(
                f"YouTube連携: {status.message}。接続管理は設定画面で行います。"
            )
            settings_status = self.widgets.get("settings_youtube_status")
            if isinstance(settings_status, QLabel):
                settings_status.setText(f"YouTube: {status.message}")
            settings_scope = self.widgets.get("settings_youtube_scope")
            if isinstance(settings_scope, QLabel):
                settings_scope.setText(f"scope: {status.scope or '未接続'}")
            template = self.service.get_youtube_posting_template()
            editor = self.widgets["youtube_template"]
            title = self.widgets.get("youtube_template_title")
            tags = self.widgets.get("youtube_template_tags")
            assert isinstance(editor, QTextEdit)
            if isinstance(title, QLineEdit):
                title.setText(template.title)
            editor.setPlainText(template.description)
            if isinstance(tags, QLineEdit):
                tags.setText(template.tags)

        def _save_youtube_template(self, *_args: object) -> None:
            title = self.widgets.get("youtube_template_title")
            description = self.widgets.get("youtube_template")
            tags = self.widgets.get("youtube_template_tags")
            try:
                template = self.service.save_youtube_posting_template(
                    title=title.text().strip() if isinstance(title, QLineEdit) else "",
                    description=description.toPlainText()
                    if isinstance(description, QTextEdit)
                    else "",
                    tags=tags.text().strip() if isinstance(tags, QLineEdit) else "",
                )
            except Exception as exc:
                self._show_warning("テンプレートを保存できません", str(exc))
                return
            self._append_activity("YouTube投稿テンプレートを保存しました")
            status = self.widgets.get("youtube_status")
            if isinstance(status, QLabel):
                status.setText(f"テンプレートを保存しました: {template.title}")

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

        def load_settings(self, *_args: object) -> None:
            try:
                config = self.service.load_config().config
            except Exception as exc:
                self._show_warning("設定を読み込めません", str(exc))
                return
            current_values = config_values(config)
            for widget_key, config_key in self.setting_field_keys.items():
                field = self.setting_fields.get(widget_key)
                if field is None:
                    continue
                field.setText(str(current_values[config_key]))
            for widget_key, config_key in self.setting_check_keys.items():
                check = self.setting_checks.get(widget_key)
                if check is None:
                    continue
                check.setChecked(bool(current_values[config_key]))
            mode_labels = {
                "process": "Master Duelのみ（推奨）",
                "system": "PC全体",
                "device": "入力デバイス",
                "none": "音声なし",
            }
            mode = self.widgets.get("settings_audio_mode")
            if isinstance(mode, QComboBox):
                mode.setCurrentText(mode_labels.get(config.audio_mode, "音声なし"))
            audio = self.widgets.get("settings_audio_input")
            if isinstance(audio, QComboBox):
                label = config.audio_input or (
                    "Master Duel単体音声"
                    if config.audio_mode == "process"
                    else "音声なし"
                )
                if audio.findText(label) < 0:
                    audio.addItem(label)
                audio.setCurrentText(label)
            runtime = self.widgets.get("settings_runtime_path")
            if isinstance(runtime, QLabel):
                runtime.setText(str(self.service.runtime_data_directory()))
            status = self.widgets.get("settings_status")
            if isinstance(status, QLabel):
                status.setText("設定を読み込みました")

        def save_settings(self, *_args: object) -> None:
            values: dict[str, str] = {}
            for widget_key, config_key in self.setting_field_keys.items():
                field = self.setting_fields.get(widget_key)
                if field is not None:
                    values[config_key] = field.text().strip()
            for widget_key, config_key in self.setting_check_keys.items():
                check = self.setting_checks.get(widget_key)
                if check is not None:
                    values[config_key] = "true" if check.isChecked() else "false"
            mode = self.widgets.get("settings_audio_mode")
            if isinstance(mode, QComboBox):
                values["recorder.audio_mode"] = {
                    "Master Duelのみ（推奨）": "process",
                    "PC全体": "system",
                    "入力デバイス": "device",
                    "音声なし": "none",
                }.get(mode.currentText(), "none")
            audio = self.widgets.get("settings_audio_input")
            if isinstance(audio, QComboBox):
                selected_audio = audio.currentText().strip()
                values["recorder.audio_input"] = (
                    "" if selected_audio in {"Master Duel単体音声", "音声なし"} else selected_audio
                )
            try:
                self.service.save_settings(values)
            except Exception as exc:
                self._show_warning("設定を保存できません", str(exc))
                return
            status = self.widgets.get("settings_status")
            if isinstance(status, QLabel):
                status.setText("設定を保存しました")

        def show_ffmpeg_setup(self, *_args: object) -> None:
            self._show_information(
                "FFmpegを導入",
                "PySide6版では導入先と公開SHA-256を確認してから導入します。"
                "自動導入は既存サービス経由で実行します。",
            )

        def select_existing_ffmpeg(self, *_args: object) -> None:
            path, _filter = QFileDialog.getOpenFileName(
                self,
                "既存FFmpegを選択",
                "",
                "ffmpeg.exe (ffmpeg.exe);;Executable (*.exe)",
            )
            if not path:
                return
            try:
                selected = self.service.select_ffmpeg_executable(Path(path))
            except Exception as exc:
                self._show_warning("FFmpegを選択できません", str(exc))
                return
            field = self.widgets.get("settings_ffmpeg_path")
            if isinstance(field, QLineEdit):
                field.setText(str(selected))

        def change_runtime_data_directory(self, *_args: object) -> None:
            self._show_information(
                "保存先を変更",
                "保存先変更は既存データの保護と移行設計が必要なため、"
                "Hotfixでは現在の保存先表示までを復旧しています。",
            )

        def refresh_audio_inputs(self, *_args: object) -> None:
            status = self.widgets.get("settings_audio_status")
            if isinstance(status, QLabel):
                status.setText("音声入力候補を検索中です")
            try:
                result = self.service.list_audio_inputs()
            except Exception as exc:
                if isinstance(status, QLabel):
                    status.setText(f"音声入力候補を取得できません: {exc}")
                return
            audio = self.widgets.get("settings_audio_input")
            if isinstance(audio, QComboBox):
                audio.clear()
                audio.addItem("音声なし")
                audio.addItem("Master Duel単体音声")
                for item in result.inputs:
                    audio.addItem(item.identifier)
            if isinstance(status, QLabel):
                status.setText(f"音声入力候補: {len(result.inputs)}件")

        def test_selected_audio_input(self, *_args: object) -> None:
            mode = self.widgets.get("settings_audio_mode")
            if isinstance(mode, QComboBox) and mode.currentText() == "Master Duelのみ（推奨）":
                operation = self.service.test_process_audio
            else:
                audio = self.widgets.get("settings_audio_input")
                identifier = audio.currentText() if isinstance(audio, QComboBox) else ""

                def operation() -> object:
                    return self.service.test_audio_input(identifier)
            try:
                result = operation()
            except Exception as exc:
                self._show_warning("音声テストに失敗しました", str(exc))
                return
            status = self.widgets.get("settings_audio_status")
            if isinstance(status, QLabel):
                status.setText(result.message)

        def _refresh_youtube_settings(self) -> None:
            try:
                status = self.service.youtube_connection_status()
            except Exception as exc:
                label = self.widgets.get("settings_youtube_status")
                if isinstance(label, QLabel):
                    label.setText(f"YouTube連携状態を確認できません: {exc}")
                return
            label = self.widgets.get("settings_youtube_status")
            if isinstance(label, QLabel):
                label.setText(f"YouTube: {status.message}")
            scope = self.widgets.get("settings_youtube_scope")
            if isinstance(scope, QLabel):
                scope.setText(f"scope: {status.scope or '未接続'}")

        def refresh_youtube_status(self, *_args: object) -> None:
            self._refresh_youtube_settings()
            self._refresh_youtube()

        def connect_youtube(self, *_args: object) -> None:
            self._show_information(
                "YouTube連携",
                "ブラウザ認証を開始します。認可コードやtokenは画面・設定・DBへ保存しません。",
            )

        def disconnect_youtube(self, *_args: object) -> None:
            try:
                status = self.service.disconnect_youtube()
            except Exception as exc:
                self._show_warning("YouTube連携を切断できません", str(exc))
                return
            self._show_information("YouTube連携", status.message)
            self._refresh_youtube_settings()

        def open_latest_youtube_test_upload(self, *_args: object) -> None:
            self._show_information(
                "privateテスト投稿",
                "最新録画のprivateテスト投稿は、戦績管理またはテンプレート画面の投稿導線から実行します。",
            )

        def refresh_reliability_status(self, *_args: object) -> None:
            status = self.widgets.get("reliability_status")
            if isinstance(status, QLabel):
                status.setText("事前チェックを実行しています")
            try:
                report = self.service.diagnose()
            except Exception as exc:
                if isinstance(status, QLabel):
                    status.setText(f"信頼性チェックに失敗しました: {exc}")
                return
            errors = [
                check for check in report.checks if getattr(check.status, "value", "") == "error"
            ]
            warnings = [
                check
                for check in report.checks
                if getattr(check.status, "value", "") == "warning"
            ]
            if errors:
                summary = f"要確認: {errors[0].label} - {errors[0].message}"
            elif warnings:
                summary = f"注意: {warnings[0].label} - {warnings[0].message}"
            else:
                summary = "利用可能: 録画前チェックに重大な問題はありません"
            if isinstance(status, QLabel):
                status.setText(summary)
            self._refresh_health_status()

        def show_initial_setup_status(self, *_args: object) -> None:
            try:
                report = self.service.diagnose()
            except Exception as exc:
                self._show_warning("初回導入を確認できません", str(exc))
                return
            messages = "\n".join(
                f"- {check.label}: {check.message}" for check in report.checks[:6]
            )
            self._show_information(
                "初回導入の確認",
                "録画前に必要な設定と保存先を確認しました。\n" + messages,
            )

        def export_managed_data(self, *_args: object) -> None:
            path, _filter = QFileDialog.getSaveFileName(
                self, "管理データを書き出し", "", "JSON (*.json)"
            )
            if not path:
                return
            self._run_action("管理データ書き出し", lambda: self.service.export_managed_data(Path(path)))

        def import_managed_data(self, *_args: object) -> None:
            path, _filter = QFileDialog.getOpenFileName(
                self, "管理データを読み込み", "", "JSON (*.json)"
            )
            if not path:
                return
            self._run_action("管理データ読み込み", lambda: self.service.import_managed_data(Path(path)))

        def reset_managed_data(self, scope: str, label: str) -> None:
            if QMessageBox.question(
                self,
                "管理データを初期化",
                f"{label}を初期化します。録画ファイル、queue、manifest、OAuth資格情報は変更しません。",
            ) != QMessageBox.StandardButton.Yes:
                return
            self._run_action(label, lambda: self.service.reset_managed_data(scope))

        def create_data_backup(self, *_args: object) -> None:
            self._run_action("データバックアップ", self.service.create_data_backup)
            self._refresh_data_protection()

        def restore_data_backup(self, *_args: object) -> None:
            path, _filter = QFileDialog.getOpenFileName(
                self,
                "バックアップを選択",
                str(self.service.paths.data / "backups"),
                "SQLite (*.sqlite3);;All files (*)",
            )
            if not path:
                return
            if QMessageBox.question(
                self,
                "バックアップを復元",
                "管理DBと設定を選択したバックアップへ戻します。録画ファイル、queue、manifest、OAuth資格情報は変更しません。",
            ) != QMessageBox.StandardButton.Yes:
                return
            self._run_action("バックアップ復元", lambda: self.service.restore_data_backup(Path(path)))
            self._refresh_data_protection()

        def run_data_integrity_diagnosis(self, *_args: object) -> None:
            try:
                report = self.service.diagnose_data_integrity()
            except Exception as exc:
                self._show_warning("整合性診断に失敗しました", str(exc))
                return
            status = self.widgets.get("data_protection_status")
            if isinstance(status, QLabel):
                result = "OK" if report.healthy else "要確認"
                status.setText(
                    f"データ保護: DB {self.service.paths.db / 'history.sqlite3'} / "
                    f"診断 {result} / 検出 {len(report.findings)}件"
                )

        def export_duel_csv(self, *_args: object) -> None:
            path, _filter = QFileDialog.getSaveFileName(
                self, "CSVを書き出し", "", "CSV (*.csv)"
            )
            if not path:
                return
            self._run_action("CSV書き出し", lambda: self.service.export_duel_csv(Path(path)))

        def export_duel_csv_sample(self, *_args: object) -> None:
            path, _filter = QFileDialog.getSaveFileName(
                self, "サンプルCSVを保存", "", "CSV (*.csv)"
            )
            if not path:
                return
            self._run_action(
                "サンプルCSV保存", lambda: self.service.export_duel_csv_sample(Path(path))
            )

        def import_duel_csv(self, *_args: object) -> None:
            path, _filter = QFileDialog.getOpenFileName(
                self, "CSVを取り込み", "", "CSV (*.csv)"
            )
            if not path:
                return
            try:
                preview = self.service.preview_duel_csv(Path(path))
                result = self.service.import_duel_csv(preview)
            except Exception as exc:
                self._show_warning("CSVを取り込めません", str(exc))
                return
            status = self.widgets.get("csv_status")
            if isinstance(status, QLabel):
                status.setText(str(result))

        def _set_history_double_click_action(self, action: str) -> None:
            play = self.widgets.get("settings_double_click_play")
            edit = self.widgets.get("settings_double_click_edit")
            if isinstance(play, QCheckBox):
                play.setChecked(action == "play")
            if isinstance(edit, QCheckBox):
                edit.setChecked(action == "edit")
            self.ui_preferences = self.ui_preferences.__class__(
                self.ui_preferences.history_visible_columns,
                self.ui_preferences.history_cell_colors,
                self.ui_preferences.automatic_update_check,
                action,
            ).normalized()
            save_ui_preferences(self.service.paths.config, self.ui_preferences)

        def _save_ui_preferences(self, *_args: object) -> None:
            auto = self.widgets.get("app_update_auto_check")
            automatic = auto.isChecked() if isinstance(auto, QCheckBox) else True
            self.ui_preferences = self.ui_preferences.__class__(
                self.ui_preferences.history_visible_columns,
                self.ui_preferences.history_cell_colors,
                automatic,
                self.ui_preferences.history_double_click_action,
            ).normalized()
            save_ui_preferences(self.service.paths.config, self.ui_preferences)

        def check_for_updates(self, *_args: object) -> None:
            status = self.widgets.get("app_update_status")
            download = self.widgets.get("app_update_download")
            if isinstance(status, QLabel):
                status.setText("新しい正式版を確認しています")
            if isinstance(download, QPushButton):
                download.setEnabled(False)
            try:
                result = AppUpdateService().check(__version__)
            except Exception as exc:
                self.available_update = None
                if isinstance(status, QLabel):
                    status.setText(f"更新を確認できません: {exc}")
                return
            self._update_check_completed(result)

        def _update_check_completed(self, result: object) -> None:
            release = getattr(result, "release", None)
            self.available_update = release
            status = self.widgets.get("app_update_status")
            download = self.widgets.get("app_update_download")
            if release is None:
                if isinstance(status, QLabel):
                    status.setText(f"現在のバージョン {__version__} は最新です")
                if isinstance(download, QPushButton):
                    download.setEnabled(False)
                return
            if isinstance(status, QLabel):
                status.setText(
                    f"新しい正式版 {release.version} を利用できます / "
                    f"{release.size_bytes:,} bytes"
                )
            if isinstance(download, QPushButton):
                download.setEnabled(True)

        def download_and_apply_update(self, *_args: object) -> None:
            release = self.available_update
            if release is None:
                return
            if self.service.operation_snapshot().state.value != "idle":
                self._show_information(
                    "更新を適用できません",
                    "録画または自動監視を停止してから更新してください。",
                )
                return
            if QMessageBox.question(
                self,
                "アプリを更新",
                f"V{release.version}を取得して、アプリ終了後に更新しますか？",
            ) != QMessageBox.StandardButton.Yes:
                return
            destination = self.service.paths.data / "updates" / f"mdrl-gui-{release.version}.exe"
            status = self.widgets.get("app_update_status")
            if isinstance(status, QLabel):
                status.setText("更新EXEを取得して起動検証しています")
            try:
                path = AppUpdateService().download_and_verify(release, destination)
                launch_update_after_exit(path, expected_version=release.version)
            except Exception as exc:
                if isinstance(status, QLabel):
                    status.setText(f"更新を適用できません: {exc}")
                self._show_warning("更新を適用できません", str(exc))
                return
            if isinstance(status, QLabel):
                status.setText("アプリ終了後に更新します")
            self.close()

        def _show_information(self, title: str, message: str) -> None:
            QMessageBox.information(self, title, message)

        def _show_warning(self, title: str, message: str) -> None:
            QMessageBox.warning(self, title, message)

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
                            item.setText("色")
                            item.setData(Qt.ItemDataRole.DecorationRole, color)
                            item.setToolTip("登録色")
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
        min-height: 36px;
        padding: 6px 14px;
        border: 1px solid #b8c1cc;
        border-radius: 6px;
        background: #f9fafb;
        color: #111827;
    }
    QPushButton[variant="primary"] {
        background: #007c7a;
        color: #ffffff;
        border-color: #00605e;
        font-weight: 700;
    }
    QPushButton[variant="secondary"] {
        background: #eef2f0;
        color: #111827;
        border-color: #aeb9c4;
    }
    QPushButton[variant="icon"] {
        min-width: 40px;
        padding: 6px 8px;
        font-weight: 700;
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
    QPushButton:disabled {
        background: #edf2f1;
        color: #7a8691;
        border-color: #d0d7de;
    }
    QComboBox, QLineEdit, QDateEdit, QSpinBox {
        min-height: 36px;
        border: 1px solid #c8d0d8;
        border-radius: 4px;
        background: #ffffff;
        padding: 4px 8px;
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
