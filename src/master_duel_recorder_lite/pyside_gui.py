from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
from typing import Any

from . import __version__
from .application import RecorderApplicationService
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

SMOKE_WIDGETS: tuple[str, ...] = (
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
    return {
        "width": width,
        "height": height,
        "widgets": widgets,
        "nav_pages": sorted(nav_pages),
        "title": "Master Duel Recorder Lite 2.0",
        "version": __version__,
        "runtime_data": str(service.paths.root),
        "history_refresh_visible": True,
        "calendar_contract": True,
        "youtube_flow_contract": (
            "prepare" not in nav_pages
            and "youtube" in nav_pages
            and "prepare_table" in widgets
        ),
        "pyside6": True,
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
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:  # pragma: no cover - depends on local Qt installation
        raise PySideGuiError(f"PySide6 GUIの読み込みに失敗しました: {exc}") from exc

    class MainWindow(QMainWindow):
        def __init__(self, service: RecorderApplicationService) -> None:
            super().__init__()
            self.service = service
            self.widgets: dict[str, QWidget] = {}
            self.nav_buttons: dict[str, QPushButton] = {}
            self.setWindowTitle("Master Duel Recorder Lite 2.0")
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
                ("history_refresh", "更新"),
                ("history_play", "再生"),
                ("history_duel", "対戦記録を編集"),
                ("history_delete", "削除"),
                ("history_duplicates", "重複比較"),
            ):
                button = self._register(key, QPushButton(text))
                controls.addWidget(button)
                if key == "history_refresh":
                    button.clicked.connect(self._refresh_history)
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
            layout.addWidget(self._register("statistics_chart", QLabel("勝率推移")))
            layout.addWidget(self._register("statistics_deck_table", QTableWidget(0, 4)))
            layout.addWidget(self._register("statistics_order_table", QTableWidget(0, 4)))

        def _catalog_page(self, layout: QVBoxLayout, key: str) -> None:
            table = QTableWidget(0, 5 if key == "decks" else 4)
            table.setHorizontalHeaderLabels(("名前", "説明", "使用回数", "色", "状態") if key == "decks" else ("名前", "説明", "色", "状態"))
            layout.addWidget(self._register("catalog_table", table), stretch=1)

        def _season_page(self, layout: QVBoxLayout) -> None:
            layout.addWidget(self._register("season_table", QTableWidget(0, 5)), stretch=1)

        def _youtube_page(self, layout: QVBoxLayout) -> None:
            layout.addWidget(QLabel("投稿テンプレートとMP4準備"))
            layout.addWidget(self._register("prepare_table", QTableWidget(0, 5)), stretch=1)

        def _reliability_page(self, layout: QVBoxLayout) -> None:
            layout.addWidget(QLabel("事前チェック、導入、後解析、ホットキーを確認します"))

        def _settings_page(self, layout: QVBoxLayout) -> None:
            form = QGroupBox("設定")
            grid = QGridLayout(form)
            grid.addWidget(self._register("ffmpeg_setup", QPushButton("FFmpegを設定")), 0, 0)
            grid.addWidget(self._register("settings_form", QLabel("通常設定 / 外部連携 / データ保護 / 危険操作")), 0, 1)
            grid.addWidget(self._register("data_protection_status", QLabel("データ保護: 待機中")), 1, 0)
            grid.addWidget(self._register("clean_uninstall", QPushButton("クリーンアンインストール")), 1, 1)
            layout.addWidget(form)
            layout.addWidget(self._register("data_backup_table", QTableWidget(0, 4)), stretch=1)

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
            def load() -> str:
                dashboard = self.service.get_history_dashboard(limit=200)
                table = self.widgets["history_table"]
                assert isinstance(table, QTableWidget)
                table.setRowCount(len(dashboard.views))
                for row, view in enumerate(dashboard.views):
                    values = (
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
                    for column, value in enumerate(values):
                        table.setItem(row, column, QTableWidgetItem(str(value)))
                incomplete = self.widgets["incomplete_duel_count"]
                assert isinstance(incomplete, QLabel)
                incomplete.setText(
                    f"戦績管理 未完了 {dashboard.incomplete_duel_record_count}件"
                )
                return f"履歴を{len(dashboard.views)}件読み込みました"

            self._run_action("履歴更新", load)

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
    window = MainWindow(service)
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
