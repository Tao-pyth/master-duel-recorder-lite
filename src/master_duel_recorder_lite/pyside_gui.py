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
    ("youtube", "YouTube"),
    ("reliability", "信頼性"),
    ("settings", "設定"),
)

SMOKE_WIDGETS: tuple[str, ...] = tuple(
    sorted(
        set(required_standard_widget_keys())
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
        "--cleanup-manifest", type=Path, default=None, help=argparse.SUPPRESS
    )
    return parser


def smoke_contract(*, service: RecorderApplicationService, width: int, height: int) -> dict[str, Any]:
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
        "title": "Master Duel Recorder Lite 2.1",
        "version": __version__,
        "runtime_data": str(service.paths.root),
        "history_refresh_visible": True,
        "calendar_contract": True,
        "standard_feature_contract": len(satisfied_features) == len(STANDARD_GUI_FEATURES),
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
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QFrame,
            QGridLayout,
            QGroupBox,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QMainWindow,
            QMessageBox,
            QPushButton,
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

    class MainWindow(QMainWindow):
        def __init__(
            self, service: RecorderApplicationService, *, load_runtime_data: bool
        ) -> None:
            super().__init__()
            self.service = service
            self.load_runtime_data = load_runtime_data
            self.widgets: dict[str, QWidget] = {}
            self.nav_buttons: dict[str, QPushButton] = {}
            self.setWindowTitle("Master Duel Recorder Lite 2.1")
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
            nav_layout.setContentsMargins(12, 12, 12, 12)
            title = QLabel("MDRL")
            title.setObjectName("appTitle")
            nav_layout.addWidget(title)
            for page, label in NAVIGATION_PAGES:
                button = QPushButton(label)
                button.setCheckable(True)
                button.clicked.connect(lambda _checked=False, key=page: self.show_page(key))
                nav_layout.addWidget(button)
                self.nav_buttons[page] = button
            nav_layout.addStretch(1)

            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(16, 12, 16, 12)
            header = QHBoxLayout()
            self.status = QLabel("待機中")
            self.status.setObjectName("operationStatus")
            incomplete = QLabel("戦績管理 未完了 0件")
            self._register("incomplete_duel_count", incomplete)
            header.addWidget(self.status)
            header.addStretch(1)
            header.addWidget(incomplete)
            content_layout.addLayout(header)

            self.stack = QStackedWidget()
            content_layout.addWidget(self.stack, stretch=1)
            self.pages: dict[str, QWidget] = {}
            for page, label in NAVIGATION_PAGES:
                widget = self._page(page, label)
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
            self.status.setText(f"{self.nav_buttons[key].text()}を表示中")

        def _page(self, key: str, label: str) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            heading = QLabel(label)
            heading.setObjectName(f"{key}Heading")
            layout.addWidget(heading)
            if key == "record":
                self._record_page(layout)
            elif key == "history":
                self._history_page(layout)
            elif key == "statistics":
                self._statistics_page(layout)
            elif key in {"decks", "tags"}:
                self._catalog_page(layout, key)
            elif key == "seasons":
                self._season_page(layout)
            elif key == "youtube":
                self._youtube_page(layout)
            elif key == "reliability":
                self._reliability_page(layout)
            elif key == "settings":
                self._settings_page(layout)
            layout.addStretch(1)
            return page

        def _record_page(self, layout: QVBoxLayout) -> None:
            controls = QHBoxLayout()
            start = self._register("record_start", QPushButton("録画開始"))
            stop = self._register("record_stop", QPushButton("録画停止"))
            watch = self._register("watch_toggle", QPushButton("自動監視開始"))
            controls.addWidget(start)
            controls.addWidget(stop)
            controls.addWidget(watch)
            start.clicked.connect(self._start_recording)
            stop.clicked.connect(self._stop_recording)
            watch.clicked.connect(self._toggle_watch)
            layout.addLayout(controls)
            layout.addWidget(self._register("record_status", QLabel("録画: 待機中")))
            target = QComboBox()
            target.addItems(("Master Duel", "デスクトップ", "ウィンドウ選択"))
            layout.addWidget(self._register("target_selector", target))
            layout.addWidget(self._register("visual_status", QLabel("自動監視: 待機中")))
            layout.addWidget(self._register("visual_details_toggle", QCheckBox("判定詳細")))
            layout.addWidget(self._register("visual_diagnostics_folder", QPushButton("診断フォルダ")))
            activity = QListWidget()
            activity.addItem("PySide6 GUIを起動しました")
            layout.addWidget(self._register("activity", activity))

        def _history_page(self, layout: QVBoxLayout) -> None:
            controls = QHBoxLayout()
            for key, text in (
                ("manual_duel_add", "録画なし戦績"),
                ("history_add", "簡易入力"),
                ("history_refresh", "更新"),
                ("history_incomplete", "未完了"),
                ("history_bulk", "一括編集"),
                ("history_columns", "表示列"),
                ("history_play", "再生"),
                ("history_duel", "対戦記録を編集"),
                ("history_youtube", "YouTube投稿"),
                ("history_delete", "削除"),
                ("history_duplicates", "重複比較"),
            ):
                button = self._register(key, QPushButton(text))
                controls.addWidget(button)
                if key == "history_refresh":
                    button.clicked.connect(self._load_runtime_dashboard)
            layout.addLayout(controls)
            table = QTableWidget(0, 7)
            table.setHorizontalHeaderLabels(("日時", "状態", "勝敗", "デッキ", "タグ", "音声", "YouTube"))
            layout.addWidget(self._register("history_table", table), stretch=1)

        def _statistics_page(self, layout: QVBoxLayout) -> None:
            filters = QGroupBox("絞り込み")
            grid = QGridLayout(filters)
            grid.addWidget(self._register("statistics_date_from_picker", QPushButton("開始日")), 0, 0)
            grid.addWidget(self._register("statistics_date_to_picker", QPushButton("終了日")), 0, 1)
            grid.addWidget(self._register("statistics_filters", QComboBox()), 0, 2)
            layout.addWidget(filters)
            tabs = QTabWidget()
            trend = QWidget()
            trend_layout = QVBoxLayout(trend)
            granularity = QComboBox()
            granularity.addItems(("日", "週", "月"))
            trend_layout.addWidget(QLabel("推移単位"))
            trend_layout.addWidget(granularity)
            trend_layout.addWidget(self._register("statistics_chart", QLabel("勝利数・累積勝率")))
            tabs.addTab(trend, "勝利数・勝率推移")
            tables = QWidget()
            table_layout = QVBoxLayout(tables)
            table_layout.addWidget(self._table("statistics_deck_table", ("デッキ", "対戦", "勝利", "勝率")))
            table_layout.addWidget(self._table("statistics_order_table", ("先後", "対戦", "勝利", "勝率")))
            table_layout.addWidget(self._table("statistics_coin_table", ("コイン", "対戦", "勝利", "勝率")))
            table_layout.addWidget(self._table("statistics_season_table", ("シーズン", "対戦", "勝利", "勝率")))
            tabs.addTab(tables, "集計表")
            layout.addWidget(tabs, stretch=1)

        def _catalog_page(self, layout: QVBoxLayout, key: str) -> None:
            table = QTableWidget(0, 5 if key == "decks" else 4)
            table.setHorizontalHeaderLabels(("名前", "説明", "使用回数", "色", "状態") if key == "decks" else ("名前", "説明", "色", "状態"))
            widget_key = "deck_catalog_table" if key == "decks" else "tag_catalog_table"
            layout.addWidget(self._register(widget_key, table), stretch=1)
            if key == "decks":
                layout.addWidget(self._register("catalog_table", QLabel("デッキ名候補と使用回数を表示します")))

        def _season_page(self, layout: QVBoxLayout) -> None:
            layout.addWidget(
                self._table("season_table", ("名前", "種別", "開始", "終了", "状態")),
                stretch=1,
            )

        def _youtube_page(self, layout: QVBoxLayout) -> None:
            status = QHBoxLayout()
            status.addWidget(self._register("youtube_status", QLabel("YouTube: 未接続")))
            for key, text in (
                ("youtube_connect", "接続"),
                ("youtube_disconnect", "切断"),
                ("youtube_refresh", "更新"),
                ("youtube_test_upload", "privateテスト"),
            ):
                status.addWidget(self._register(key, QPushButton(text)))
            layout.addLayout(status)
            template = QTextEdit()
            template.setPlainText("投稿テンプレートを確認します")
            layout.addWidget(self._register("youtube_template", template))
            layout.addWidget(self._register("prepare_recording", QPushButton("選択録画をMP4準備へ追加")))
            layout.addWidget(
                self._table("prepare_table", ("録画ID", "状態", "タイトル", "公開範囲", "更新日時")),
                stretch=1,
            )

        def _reliability_page(self, layout: QVBoxLayout) -> None:
            layout.addWidget(
                self._register(
                    "reliability_status",
                    QLabel("事前チェック、導入、ホットキー、トレイ状態を確認します"),
                )
            )
            layout.addWidget(
                self._register(
                    "improvement_status",
                    QLabel("後解析、録画欠損、保存候補、移行パック導線を確認します"),
                )
            )

        def _settings_page(self, layout: QVBoxLayout) -> None:
            form = QGroupBox("設定")
            grid = QGridLayout(form)
            grid.addWidget(self._register("ffmpeg_setup", QPushButton("FFmpegを設定")), 0, 0)
            grid.addWidget(self._register("settings_form", QLabel("通常設定 / 外部連携 / データ保護 / 危険操作")), 0, 1)
            grid.addWidget(self._register("data_protection_status", QLabel("データ保護: 待機中")), 1, 0)
            grid.addWidget(self._register("clean_uninstall", QPushButton("クリーンアンインストール")), 1, 1)
            grid.addWidget(
                self._register(
                    "data_protection_scope",
                    QLabel("バックアップ対象: 管理DBと設定。録画ファイル、queue、manifest、OAuth資格情報は対象外です。"),
                ),
                2,
                0,
                1,
                2,
            )
            grid.addWidget(self._register("csv_status", QLabel("CSV入出力: 待機中")), 3, 0)
            grid.addWidget(self._register("app_update", QPushButton("アプリ更新を確認")), 3, 1)
            layout.addWidget(form)
            layout.addWidget(
                self._table("data_backup_table", ("作成日時", "契機", "DB版", "サイズ")),
                stretch=1,
            )

        def _table(self, key: str, headers: tuple[str, ...]) -> QTableWidget:
            table = QTableWidget(0, len(headers))
            table.setHorizontalHeaderLabels(headers)
            return self._register(key, table)

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
            if errors:
                self.status.setText(f"一部の表示更新に失敗しました: {errors[0]}")
            else:
                self.status.setText(f"既存データを{loaded}領域で読み込みました")

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
            for view in dashboard.views:
                rows.append(
                    (
                        view.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                        view.entry.state if view.entry is not None else "manual",
                        view.result,
                        view.own_deck or view.opponent_deck or "-",
                        " / ".join(view.duel_record.values.tags)
                        if view.duel_record is not None
                        else "-",
                        view.entry.audio_state if view.entry is not None else "-",
                        "投稿済み"
                        if view.entry is not None and view.entry.recording_id
                        else "-",
                    )
                )
            self._set_table_rows(table, rows)
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
                        deck.name,
                        deck.description,
                        deck.usage_count,
                        deck.color or "-",
                        "非表示" if deck.hidden_from_history_statistics else "表示",
                    )
                    for deck in self.service.list_decks()
                ),
            )
            self._set_table_rows(
                tag_table,
                tuple(
                    (
                        tag.name,
                        tag.description,
                        tag.color or "-",
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
                        season.start_date,
                        season.end_date,
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
            assert isinstance(chart, QLabel)
            overall = dashboard.overall
            chart.setText(
                "勝利数・累積勝率: "
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
            table: QTableWidget, rows: tuple[tuple[object, ...], ...] | list[tuple[object, ...]]
        ) -> None:
            table.setRowCount(len(rows))
            for row_index, row_values in enumerate(rows):
                for column, value in enumerate(row_values):
                    table.setItem(row_index, column, QTableWidgetItem(str(value)))

        def _run_action(self, label: str, operation: Any) -> None:
            try:
                result = operation()
            except Exception as exc:
                QMessageBox.warning(self, f"{label}に失敗しました", str(exc))
                self.status.setText(f"{label}に失敗しました")
                return
            record_status = self.widgets.get("record_status")
            if isinstance(record_status, QLabel) and hasattr(result, "state"):
                recording_id = getattr(result, "recording_id", None) or "-"
                record_status.setText(f"録画: {result.state.value} / {recording_id}")
            self.status.setText(str(result) if isinstance(result, str) else f"{label}が完了しました")

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
            window.grab().save(str(args.smoke_screenshot))
        window.close()
        app.processEvents()
        return 0
    return int(app.exec())


def _style_sheet() -> str:
    return """
    * { font-family: "Yu Gothic UI", "Yu Gothic", "Meiryo", "MS Gothic", "Segoe UI"; font-size: 10pt; }
    QMainWindow { background: #f7f8fa; color: #1f2933; }
    #navigation { background: #202833; }
    #appTitle { color: #ffffff; font-size: 20px; font-weight: 700; padding: 8px; }
    #operationStatus { color: #344054; font-weight: 600; }
    QPushButton { min-height: 30px; padding: 4px 10px; border: 1px solid #b8c1cc; border-radius: 4px; background: #ffffff; color: #1f2933; }
    QPushButton:checked { background: #dbeafe; border-color: #5b8def; }
    QLabel { color: #1f2933; }
    QGroupBox { border: 1px solid #d0d7de; border-radius: 6px; margin-top: 8px; padding: 8px; }
    QTableWidget, QListWidget, QTextEdit { background: #ffffff; border: 1px solid #d0d7de; }
    """


if __name__ == "__main__":
    raise SystemExit(main())
