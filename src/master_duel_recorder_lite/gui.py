from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
import calendar
import json
import os
from pathlib import Path
import queue
import sys
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Callable, TypeVar
import webbrowser

from . import __version__
from .application import (
    ActiveSeasonSummary,
    ApplicationEvent,
    DuelManagementQuery,
    DuelEditorData,
    RecorderApplicationService,
    RecordingHistoryDashboard,
    RecordingSnapshot,
)
from .capture_targets import CaptureTarget
from .duel_catalog import DuelCatalogEntry
from .duel_records import (
    DuelRecordValues,
    duel_choice_label,
    duel_choice_labels,
    duel_choice_value,
)
from .duel_statistics import (
    StatisticsDashboard,
    StatisticsFilter,
    StatisticsMetric,
    StatisticsTrendPoint,
)
from .duel_timeline import DuelEvent
from .duel_workflow import BulkDuelUpdate, DuelFilterCriteria
from .ffmpeg_setup import (
    FFMPEG_DOWNLOAD_URL,
    FFMPEG_LICENSE,
    FFMPEG_PROVIDER_PAGE,
    FfmpegInstallResult,
    FfmpegInstallProgress,
)
from .preflight import CheckStatus, PreflightReport
from .operation_state import OperationAction
from .recording_browsing import RecordingReference
from .recording_session import RecordingState


T = TypeVar("T")
WAITING_ACTIVITY_PREFIX = "対戦開始を判定中です"

ICON_GLYPHS = {
    "add": "\ue710",
    "calendar": "\ue787",
    "delete": "\ue74d",
    "diagnostic": "\ue946",
    "edit": "\ue70f",
    "folder": "\ue838",
    "filter": "\ue71c",
    "clear_filter": "\ue711",
    "report": "\ue9f9",
    "export": "\ue898",
    "import": "\ue896",
    "reset": "\ue72c",
    "play": "\ue768",
    "refresh": "\ue72c",
    "save": "\ue74e",
    "test": "\ue9f9",
    "timeline": "\ue81c",
    "available": "\ue73e",
    "warning": "\ue7ba",
    "unavailable": "\ue783",
    "expand": "\ue70d",
    "collapse": "\ue70e",
    "link": "\ue71b",
    "duplicates": "\ue8ef",
}
HISTORY_ROW_ACTIONS = (
    ("play", "再生", "Enter"),
    ("edit", "対戦記録を編集", "Ctrl+E"),
    ("folder", "保存場所を開く", "Ctrl+O"),
    ("delete", "削除", "Delete"),
)


@dataclass(frozen=True)
class UiResult:
    callback: Callable[[object], None] | None
    error_callback: Callable[[BaseException], None] | None
    value: object | None = None
    error: BaseException | None = None


@dataclass(frozen=True)
class RecordStatusPresentation:
    text: str
    background: str
    foreground: str


RECORD_STATUS_PRESENTATIONS = {
    "idle": RecordStatusPresentation("● 停止中", "#e8ebef", "#202124"),
    "starting": RecordStatusPresentation("● 開始処理中", "#f2c94c", "#202124"),
    "manual_recording": RecordStatusPresentation("● 手動録画中", "#b3261e", "#ffffff"),
    "watch_waiting": RecordStatusPresentation(
        "● 自動監視中 | 録画待機",
        "#006a6a",
        "#ffffff",
    ),
    "candidate_recording": RecordStatusPresentation(
        "● 自動監視中 | 録画中（対戦確認中）",
        "#f2c94c",
        "#202124",
    ),
    "automatic_recording": RecordStatusPresentation(
        "● 自動監視中 | 録画中（対戦記録中）",
        "#b3261e",
        "#ffffff",
    ),
    "stopping": RecordStatusPresentation("● 停止処理中", "#9a6700", "#ffffff"),
    "failed": RecordStatusPresentation("● 録画失敗", "#7f1d1d", "#ffffff"),
}


def record_status_presentation(status: str) -> RecordStatusPresentation:
    return RECORD_STATUS_PRESENTATIONS[status]


def incomplete_duel_count_presentation(count: int) -> RecordStatusPresentation:
    if count < 0:
        raise ValueError("未完了件数は0以上である必要があります")
    if count == 0:
        return RecordStatusPresentation("戦績管理 未完了 0件", "#dff5e8", "#0f6651")
    return RecordStatusPresentation(
        f"戦績管理 未完了 {count}件",
        "#fff0c2",
        "#6b4600",
    )


class WidgetTooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<FocusIn>", self.show, add="+")
        widget.bind("<FocusOut>", self.hide, add="+")

    def show(self, _event: object | None = None) -> None:
        if self.tip is not None:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(
            self.tip,
            text=self.text,
            background="#20242a",
            foreground="#ffffff",
            padx=7,
            pady=4,
            font=("Segoe UI", 9),
        ).pack()
        x = self.widget.winfo_rootx() + max(8, self.widget.winfo_width() // 2)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip.geometry(f"+{x}+{y}")

    def hide(self, _event: object | None = None) -> None:
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class BackgroundTasks:
    def __init__(self, root: tk.Misc, *, max_workers: int = 3) -> None:
        self.root = root
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="mdrl-gui"
        )
        self.results: queue.Queue[UiResult] = queue.Queue()
        self.closed = False
        self.root.after(80, self._drain)

    def submit(
        self,
        operation: Callable[[], T],
        *,
        callback: Callable[[T], None] | None = None,
        error_callback: Callable[[BaseException], None] | None = None,
    ) -> Future[T]:
        if self.closed:
            raise RuntimeError("バックグラウンド処理は終了しています")
        future = self.executor.submit(operation)

        def completed(done: Future[T]) -> None:
            try:
                value = done.result()
            except BaseException as exc:
                self.results.put(UiResult(None, error_callback, error=exc))
            else:
                self.results.put(UiResult(callback, None, value=value))

        future.add_done_callback(completed)
        return future

    def close(self) -> None:
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)

    def _drain(self) -> None:
        while True:
            try:
                item = self.results.get_nowait()
            except queue.Empty:
                break
            if item.error is not None:
                if item.error_callback is not None:
                    item.error_callback(item.error)
                continue
            if item.callback is not None:
                item.callback(item.value)
        if not self.closed:
            self.root.after(80, self._drain)


class StatisticsTrendChart(tk.Canvas):
    def __init__(self, parent: tk.Misc, *, colors: dict[str, str]) -> None:
        super().__init__(
            parent,
            background=colors["surface"],
            highlightthickness=0,
            borderwidth=0,
            height=300,
        )
        self.colors = colors
        self.points: tuple[StatisticsTrendPoint, ...] = ()
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_points(self, points: tuple[StatisticsTrendPoint, ...]) -> None:
        self.points = points
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 320)
        height = max(self.winfo_height(), 220)
        if not self.points:
            self.create_text(
                width / 2,
                height / 2,
                text="表示できる確定済み対戦がありません",
                fill=self.colors["muted"],
                font=("Segoe UI", 10),
            )
            return
        left, top, right, bottom = 50, 32, width - 22, height - 42
        chart_width = max(1, right - left)
        chart_height = max(1, bottom - top)
        for rate in (0, 50, 100):
            y = bottom - (chart_height * rate / 100)
            self.create_line(left, y, right, y, fill=self.colors["border"], dash=(2, 4))
            self.create_text(
                left - 9,
                y,
                text=f"{rate}%",
                anchor="e",
                fill=self.colors["muted"],
                font=("Segoe UI", 8),
            )
        maximum_wins = max(1, *(point.metric.wins for point in self.points))
        slot = chart_width / max(1, len(self.points))
        bar_width = max(2, min(18, slot * 0.48))
        line_points: list[float] = []
        for index, point in enumerate(self.points):
            x = left + (slot * index) + (slot / 2)
            bar_height = chart_height * point.metric.wins / maximum_wins
            self.create_rectangle(
                x - bar_width / 2,
                bottom - bar_height,
                x + bar_width / 2,
                bottom,
                fill=self.colors["tertiary"],
                outline="",
            )
            if point.metric.win_rate is not None:
                line_points.extend((x, bottom - chart_height * point.metric.win_rate))
            label_stride = max(1, (len(self.points) + 7) // 8)
            if index % label_stride == 0 or index == len(self.points) - 1:
                self.create_text(
                    x,
                    bottom + 14,
                    text=point.label,
                    fill=self.colors["muted"],
                    font=("Segoe UI", 8),
                )
        if len(line_points) >= 4:
            self.create_line(
                *line_points, fill=self.colors["primary"], width=3, smooth=False
            )
        for index in range(0, len(line_points), 2):
            self.create_oval(
                line_points[index] - 3,
                line_points[index + 1] - 3,
                line_points[index] + 3,
                line_points[index + 1] + 3,
                fill=self.colors["primary"],
                outline=self.colors["surface"],
            )
        self.create_rectangle(
            left, 8, left + 12, 20, fill=self.colors["tertiary"], outline=""
        )
        self.create_text(
            left + 18,
            14,
            text="勝利数",
            anchor="w",
            fill=self.colors["text"],
            font=("Segoe UI", 9),
        )
        self.create_line(
            left + 78, 14, left + 96, 14, fill=self.colors["primary"], width=3
        )
        self.create_text(
            left + 102,
            14,
            text="勝率",
            anchor="w",
            fill=self.colors["text"],
            font=("Segoe UI", 9),
        )


class RecorderGui:
    COLORS = {
        "canvas": "#f7f9f8",
        "surface": "#ffffff",
        "surface_container": "#edf2f0",
        "surface_high": "#e3e9e7",
        "sidebar": "#f0f5f3",
        "sidebar_active": "#cce8e5",
        "text": "#191c1c",
        "muted": "#3f4948",
        "border": "#bec9c7",
        "outline": "#6f7978",
        "primary": "#006a6a",
        "primary_hover": "#005c5c",
        "on_primary": "#ffffff",
        "tertiary": "#4f635f",
        "green": "#147d64",
        "red": "#b3261e",
        "amber": "#9a6700",
        "blue": "#006a6a",
    }

    def __init__(
        self,
        root: tk.Tk,
        service: RecorderApplicationService,
        *,
        smoke_mode: bool = False,
    ) -> None:
        self.root = root
        self.service = service
        self.smoke_mode = smoke_mode
        self.tasks = BackgroundTasks(root)
        self.pages: dict[str, ttk.Frame] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.widgets: dict[str, tk.Widget] = {}
        self.targets_by_label: dict[str, CaptureTarget] = {}
        self.catalog_entries_by_id: dict[str, DuelCatalogEntry] = {}
        self.catalog_trees: dict[str, ttk.Treeview] = {}
        self.catalog_name_vars: dict[str, tk.StringVar] = {}
        self.catalog_description_vars: dict[str, tk.StringVar] = {}
        self.catalog_color_vars: dict[str, tk.StringVar] = {}
        self.catalog_color_buttons: dict[str, tk.Button] = {}
        self.catalog_color_images: dict[str, tk.PhotoImage] = {}
        self.catalog_update_buttons: dict[str, ttk.Button] = {}
        self.catalog_delete_buttons: dict[str, ttk.Button] = {}
        self.history_views_by_id: dict[str, object] = {}
        self.history_action_buttons: dict[str, ttk.Button] = {}
        self.history_color_lines: list[tk.Widget] = []
        self.seasons_by_id: dict[str, object] = {}
        self.season_color_images: dict[str, tk.PhotoImage] = {}
        self.statistics_decks_by_label: dict[str, str | None] = {"すべて": None}
        self.statistics_tags_by_label: dict[str, int | None] = {"すべて": None}
        self.statistics_seasons_by_label: dict[str, int | None] = {
            "すべて": None,
            "未設定": None,
        }
        self.catalog_opponent_only_var = tk.BooleanVar(value=False)
        self.catalog_hidden_var = tk.BooleanVar(value=False)
        self.history_query = DuelManagementQuery(limit=200)
        self.active_saved_filter_id: str | None = None
        self.active_season_buttons: list[ttk.Button] = []
        self.current_page = "record"
        self.watch_events: queue.Queue[ApplicationEvent] = queue.Queue()
        self.busy_operations = 0
        self.closing = False
        self.automatic_recording_confirmed = False
        self.ffmpeg_setup_prompted = False
        self.ffmpeg_setup_dialog: tk.Toplevel | None = None
        self.tooltips: list[WidgetTooltip] = []

        self._configure_window()
        self._configure_styles()
        self._build_shell()
        self._build_record_page()
        self._build_history_page()
        self._build_statistics_page()
        self._build_catalog_pages()
        self._build_seasons_page()
        self._build_prepare_page()
        self._build_settings_page()
        self.show_page("record")
        self.root.protocol("WM_DELETE_WINDOW", self.request_close)
        self.root.after(300, self._poll_runtime)
        if smoke_mode:
            self._populate_smoke_data()
        else:
            self.refresh_all()

    def _configure_window(self) -> None:
        self.root.title(f"Master Duel Recorder Lite {__version__}")
        self.root.geometry("1180x760")
        self.root.minsize(980, 640)
        self.root.configure(background=self.COLORS["canvas"])

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("App.TFrame", background=self.COLORS["canvas"])
        style.configure("Surface.TFrame", background=self.COLORS["surface"])
        style.configure("Container.TFrame", background=self.COLORS["surface_container"])
        style.configure(
            "Title.TLabel",
            background=self.COLORS["canvas"],
            foreground=self.COLORS["text"],
            font=("Segoe UI Semibold", 20),
        )
        style.configure(
            "Heading.TLabel",
            background=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            font=("Segoe UI Semibold", 11),
        )
        style.configure(
            "Body.TLabel",
            background=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "Muted.TLabel",
            background=self.COLORS["surface"],
            foreground=self.COLORS["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "TButton",
            font=("Segoe UI Semibold", 9),
            padding=(12, 8),
            background=self.COLORS["surface_container"],
            foreground=self.COLORS["text"],
            bordercolor=self.COLORS["border"],
        )
        style.map(
            "TButton",
            background=[
                ("pressed", self.COLORS["surface_high"]),
                ("active", self.COLORS["surface_high"]),
                ("disabled", "#e1e5e4"),
            ],
            foreground=[("disabled", "#8b9392")],
        )
        style.configure("Icon.TButton", font=("Segoe MDL2 Assets", 13), padding=(10, 7))
        style.configure(
            "DatePicker.TEntry",
            padding=(8, 7, 36, 7),
            fieldbackground=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            bordercolor=self.COLORS["border"],
            insertcolor=self.COLORS["text"],
        )
        style.configure(
            "DatePicker.Icon.TButton",
            font=("Segoe MDL2 Assets", 12),
            padding=(5, 7),
        )
        style.configure(
            "TEntry",
            padding=(8, 7),
            fieldbackground=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            bordercolor=self.COLORS["border"],
            insertcolor=self.COLORS["text"],
        )
        style.configure(
            "TCombobox",
            padding=(8, 7),
            fieldbackground=self.COLORS["surface"],
            background=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            bordercolor=self.COLORS["border"],
            arrowcolor=self.COLORS["muted"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", self.COLORS["surface"]),
                ("disabled", self.COLORS["surface_container"]),
            ],
            selectbackground=[("readonly", self.COLORS["surface"])],
            selectforeground=[("readonly", self.COLORS["text"])],
            background=[("readonly", self.COLORS["surface"])],
        )
        style.configure(
            "TCheckbutton",
            background=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            padding=(0, 3),
        )
        style.map(
            "TCheckbutton",
            background=[("active", self.COLORS["surface"])],
        )
        self.root.option_add("*TCombobox*Listbox.background", self.COLORS["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", self.COLORS["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.COLORS["sidebar_active"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", self.COLORS["text"])
        style.configure(
            "Primary.TButton",
            foreground=self.COLORS["on_primary"],
            background=self.COLORS["primary"],
        )
        style.map(
            "Primary.TButton",
            background=[
                ("pressed", "#004f4f"),
                ("active", self.COLORS["primary_hover"]),
                ("disabled", "#9da8a6"),
            ],
        )
        style.configure(
            "Record.TButton", foreground="#ffffff", background=self.COLORS["red"]
        )
        style.map(
            "Record.TButton",
            background=[("active", "#8f1f19"), ("disabled", "#c6a09d")],
        )
        style.configure(
            "Stop.TButton", foreground="#ffffff", background=self.COLORS["green"]
        )
        style.map(
            "Stop.TButton", background=[("active", "#0f6651"), ("disabled", "#9ebdb5")]
        )
        style.configure(
            "Treeview",
            rowheight=31,
            font=("Segoe UI", 9),
            borderwidth=0,
            background=self.COLORS["surface"],
            fieldbackground=self.COLORS["surface"],
        )
        style.configure(
            "Treeview.Heading",
            font=("Segoe UI Semibold", 9),
            padding=(7, 8),
            background=self.COLORS["surface_container"],
            foreground=self.COLORS["text"],
        )
        style.map(
            "Treeview",
            background=[("selected", self.COLORS["sidebar_active"])],
            foreground=[("selected", self.COLORS["text"])],
        )
        style.configure(
            "Metric.TLabel",
            background=self.COLORS["surface"],
            foreground=self.COLORS["primary"],
            font=("Segoe UI Semibold", 25),
        )
        style.configure(
            "MetricLabel.TLabel",
            background=self.COLORS["surface"],
            foreground=self.COLORS["muted"],
            font=("Segoe UI Semibold", 9),
        )
        style.configure("TNotebook", background=self.COLORS["canvas"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            font=("Segoe UI Semibold", 9),
            padding=(16, 9),
            background=self.COLORS["surface_container"],
        )
        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", self.COLORS["sidebar_active"]),
                ("active", self.COLORS["surface_high"]),
            ],
            foreground=[("selected", self.COLORS["primary"])],
        )

    def _build_shell(self) -> None:
        shell = tk.Frame(self.root, background=self.COLORS["canvas"])
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, width=188, background=self.COLORS["sidebar"])
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Label(
            sidebar,
            text="MDRL",
            anchor="w",
            padx=20,
            pady=22,
            background=self.COLORS["sidebar"],
            foreground=self.COLORS["primary"],
            font=("Segoe UI Semibold", 20),
        )
        brand.pack(fill="x")
        tk.Label(
            sidebar,
            text=f"Master Duel Recorder\nVersion {__version__}",
            justify="left",
            anchor="w",
            padx=20,
            background=self.COLORS["sidebar"],
            foreground=self.COLORS["muted"],
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(0, 18))
        for key, label in (
            ("record", "録画"),
            ("history", "戦績管理"),
            ("statistics", "統計"),
            ("decks", "デッキ名"),
            ("tags", "タグ"),
            ("seasons", "シーズン"),
            ("prepare", "MP4準備"),
            ("settings", "設定"),
        ):
            button = tk.Button(
                sidebar,
                text=label,
                anchor="w",
                padx=20,
                pady=11,
                relief="flat",
                borderwidth=0,
                background=self.COLORS["sidebar"],
                foreground=self.COLORS["text"],
                activebackground=self.COLORS["sidebar_active"],
                activeforeground=self.COLORS["primary"],
                font=("Segoe UI Semibold", 10),
                command=lambda page=key: self.show_page(page),
            )
            button.pack(fill="x")
            self.nav_buttons[key] = button
        status_panel = tk.Frame(sidebar, background=self.COLORS["sidebar"])
        status_panel.pack(side="bottom", fill="x", padx=20, pady=16)
        self.connection_icon_label = tk.Label(
            status_panel,
            text=ICON_GLYPHS["warning"],
            background=self.COLORS["sidebar"],
            foreground=self.COLORS["muted"],
            font=("Segoe MDL2 Assets", 13),
        )
        self.connection_icon_label.pack(side="left", padx=(0, 8))
        self.connection_label = tk.Label(
            status_panel,
            text="準備中",
            anchor="w",
            background=self.COLORS["sidebar"],
            foreground=self.COLORS["muted"],
            font=("Segoe UI Semibold", 9),
        )
        self.connection_label.pack(side="left")

        content = ttk.Frame(shell, style="App.TFrame", padding=(24, 18, 24, 20))
        content.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(content, style="App.TFrame")
        header.pack(fill="x", pady=(0, 14))
        self.page_title = ttk.Label(header, text="録画", style="Title.TLabel")
        self.page_title.pack(side="left")
        self.busy_label = ttk.Label(
            header, text="", style="Title.TLabel", font=("Segoe UI", 9)
        )
        self.busy_label.pack(side="right")
        self.incomplete_duel_count_var = tk.StringVar(value="戦績管理 集計中")
        self.incomplete_duel_count_button = tk.Button(
            header,
            textvariable=self.incomplete_duel_count_var,
            command=lambda: self.show_page("history"),
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=6,
            cursor="hand2",
            font=("Segoe UI Semibold", 9),
            background="#e8ebef",
            foreground=self.COLORS["text"],
            activebackground="#e8ebef",
            activeforeground=self.COLORS["text"],
        )
        self.incomplete_duel_count_button.pack(side="right", padx=(0, 12))
        self.widgets["incomplete_duel_count"] = self.incomplete_duel_count_button
        self.tooltips.append(
            WidgetTooltip(
                self.incomplete_duel_count_button,
                "未完了の対戦記録を録画履歴で確認",
            )
        )
        self.page_host = ttk.Frame(content, style="App.TFrame")
        self.page_host.pack(fill="both", expand=True)

    def _new_page(self, key: str) -> ttk.Frame:
        page = ttk.Frame(self.page_host, style="App.TFrame")
        self.pages[key] = page
        return page

    def _surface(
        self, parent: tk.Misc, *, padding: tuple[int, int] = (16, 14)
    ) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Surface.TFrame", padding=padding)
        return frame

    def _icon_button(
        self,
        parent: tk.Misc,
        icon: str,
        accessible_name: str,
        command: Callable[[], object],
        *,
        state: str = "normal",
        style: str = "Icon.TButton",
    ) -> ttk.Button:
        button = ttk.Button(
            parent,
            text=ICON_GLYPHS[icon],
            width=3,
            command=command,
            state=state,
            style=style,
            takefocus=True,
        )
        button.accessible_name = accessible_name  # type: ignore[attr-defined]
        self.tooltips.append(WidgetTooltip(button, accessible_name))
        return button

    def _date_picker(
        self,
        parent: tk.Misc,
        variable: tk.StringVar,
        accessible_name: str,
    ) -> tuple[ttk.Frame, ttk.Button]:
        holder = ttk.Frame(parent, style="Surface.TFrame")
        ttk.Entry(
            holder,
            textvariable=variable,
            width=10,
            style="DatePicker.TEntry",
        ).pack(fill="both", expand=True)
        button = ttk.Button(
            holder,
            text=ICON_GLYPHS["calendar"],
            width=2,
            command=lambda: self.open_calendar_picker(variable),
            style="DatePicker.Icon.TButton",
            takefocus=True,
        )
        button.place(relx=1.0, rely=0.0, relheight=1.0, anchor="ne")
        button.accessible_name = accessible_name  # type: ignore[attr-defined]
        self.tooltips.append(WidgetTooltip(button, accessible_name))
        return holder, button

    def _build_record_page(self) -> None:
        page = self._new_page("record")
        target_panel = self._surface(page)
        target_panel.pack(fill="x", pady=(0, 12))
        ttk.Label(target_panel, text="録画対象", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            target_panel,
            text="選択したウィンドウ、モニター、またはデスクトップを実際のFFmpeg入力に使用します。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 10))
        self.target_var = tk.StringVar()
        self.target_combo = ttk.Combobox(
            target_panel, textvariable=self.target_var, state="readonly", width=74
        )
        self.target_combo.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        refresh = self._icon_button(
            target_panel, "refresh", "録画対象を更新", self.refresh_targets
        )
        refresh.grid(row=2, column=1, padx=(0, 8))
        save = ttk.Button(
            target_panel,
            text="選択を保存",
            style="Primary.TButton",
            command=self.save_selected_target,
        )
        save.grid(row=2, column=2)
        target_panel.columnconfigure(0, weight=1)
        self.widgets["target_selector"] = self.target_combo

        controls = self._surface(page, padding=(18, 18))
        controls.pack(fill="x", pady=(0, 12))
        self.record_state_var = tk.StringVar()
        self.elapsed_var = tk.StringVar(value="00:00:00")
        self.record_status_label = tk.Label(
            controls,
            textvariable=self.record_state_var,
            anchor="w",
            padx=12,
            pady=9,
            font=("Segoe UI Semibold", 12),
        )
        self.record_status_label.grid(row=0, column=0, sticky="ew", padx=(0, 18))
        self._set_record_status("idle")
        ttk.Label(
            controls,
            textvariable=self.elapsed_var,
            style="Heading.TLabel",
            font=("Consolas", 20),
        ).grid(row=1, column=0, sticky="w", pady=(5, 2))
        self.record_detail_var = tk.StringVar(value="録画ID: -\n保存先: -")
        ttk.Label(
            controls,
            textvariable=self.record_detail_var,
            style="Muted.TLabel",
            justify="left",
        ).grid(row=2, column=0, sticky="w")
        visual_header = ttk.Frame(controls, style="Surface.TFrame")
        visual_header.grid(row=3, column=0, sticky="ew", pady=(5, 0))
        self.visual_status_var = tk.StringVar(value="自動監視: 待機中")
        ttk.Label(
            visual_header,
            textvariable=self.visual_status_var,
            style="Muted.TLabel",
            justify="left",
        ).pack(side="left", fill="x", expand=True)
        self.visual_details_visible = tk.BooleanVar(value=False)
        self.visual_details_button = self._icon_button(
            visual_header,
            "expand",
            "自動判定の詳細を表示",
            self.toggle_visual_details,
        )
        self.visual_details_button.pack(side="right")
        self.widgets["visual_details_toggle"] = self.visual_details_button
        self.visual_details_var = tk.StringVar(value="判定詳細は自動監視中に更新されます")
        self.visual_details_label = ttk.Label(
            controls,
            textvariable=self.visual_details_var,
            style="Muted.TLabel",
            justify="left",
            wraplength=500,
        )
        self.record_audio_status_var = tk.StringVar(
            value="音声: 設定で入力を選択できます"
        )
        ttk.Label(
            controls,
            textvariable=self.record_audio_status_var,
            style="Muted.TLabel",
        ).grid(row=5, column=0, sticky="w", pady=(3, 0))
        button_row = ttk.Frame(controls, style="Surface.TFrame")
        button_row.grid(row=0, column=1, rowspan=6, sticky="e")
        self.start_button = ttk.Button(
            button_row,
            text="録画開始",
            style="Record.TButton",
            command=self.start_recording,
        )
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(
            button_row,
            text="停止",
            style="Stop.TButton",
            command=self.stop_recording,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(0, 18))
        self.watch_button = ttk.Button(
            button_row, text="自動監視開始", command=self.toggle_watch
        )
        self.watch_button.pack(side="left")
        controls.columnconfigure(0, weight=1)
        self.widgets["record_start"] = self.start_button
        self.widgets["record_stop"] = self.stop_button
        self.widgets["record_status"] = self.record_status_label
        self.widgets["watch_toggle"] = self.watch_button
        self.widgets["visual_status"] = self.visual_status_var

        duel_summary = self._surface(page, padding=(14, 10))
        duel_summary.pack(fill="x", pady=(0, 12))
        self.manual_duel_button = ttk.Button(
            duel_summary,
            text=f"{ICON_GLYPHS['add']}  戦績を追加",
            command=self._open_manual_quick_duel_editor,
        )
        self.manual_duel_button.pack(side="left", padx=(0, 14))
        ttk.Separator(duel_summary, orient="vertical").pack(
            side="left", fill="y", padx=(0, 14)
        )
        self.active_season_host = ttk.Frame(duel_summary, style="Surface.TFrame")
        self.active_season_host.pack(side="left", fill="x", expand=True)
        ttk.Label(
            self.active_season_host,
            text="開催中のシーズンを読み込み中",
            style="Muted.TLabel",
        ).pack(side="left")
        self.widgets["manual_duel_add"] = self.manual_duel_button

        lower = ttk.Frame(page, style="App.TFrame")
        lower.pack(fill="both", expand=True)
        diagnosis = self._surface(lower)
        diagnosis.pack(side="left", fill="both", expand=True, padx=(0, 6))
        header = ttk.Frame(diagnosis, style="Surface.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="環境診断", style="Heading.TLabel").pack(side="left")
        ttk.Button(header, text="診断実行", command=self.run_diagnosis).pack(
            side="right"
        )
        diagnostic_folder = self._icon_button(
            header,
            "folder",
            "自動監視の数値診断フォルダを開く",
            self.open_visual_diagnostics,
        )
        diagnostic_folder.pack(side="right", padx=(0, 8))
        diagnostic_export = self._icon_button(
            header,
            "save",
            "自動監視の数値診断をZIPで保存",
            self.export_visual_diagnostics,
        )
        diagnostic_export.pack(side="right", padx=(0, 8))
        self.widgets["visual_diagnostics_folder"] = diagnostic_folder
        self.diagnosis_tree = ttk.Treeview(
            diagnosis, columns=("state", "message"), show="headings", height=8
        )
        self.diagnosis_tree.heading("state", text="状態")
        self.diagnosis_tree.heading("message", text="項目と結果")
        self.diagnosis_tree.column("state", width=76, anchor="center", stretch=False)
        self.diagnosis_tree.column("message", width=400)
        self.diagnosis_tree.pack(fill="both", expand=True, pady=(10, 0))

        activity = self._surface(lower)
        activity.pack(side="left", fill="both", expand=True, padx=(6, 0))
        ttk.Label(activity, text="アクティビティ", style="Heading.TLabel").pack(
            anchor="w"
        )
        self.activity_list = tk.Listbox(
            activity,
            borderwidth=0,
            highlightthickness=0,
            background=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            selectbackground="#d9e7f7",
            font=("Segoe UI", 9),
        )
        self.activity_list.pack(fill="both", expand=True, pady=(10, 0))
        self.widgets["activity"] = self.activity_list

    def _build_history_page(self) -> None:
        page = self._new_page("history")
        toolbar = self._surface(page, padding=(14, 10))
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="戦績管理", style="Heading.TLabel").pack(side="left")
        self.history_add_button = self._icon_button(
            toolbar, "add", "録画を伴わない戦績を追加", self._open_manual_quick_duel_editor
        )
        self.history_add_button.pack(side="left", padx=(16, 6))
        self.history_incomplete_button = ttk.Button(
            toolbar,
            text="未完了を処理",
            command=self._open_incomplete_duel_queue,
        )
        self.history_incomplete_button.pack(side="left", padx=(0, 6))
        self.history_bulk_button = ttk.Button(
            toolbar,
            text="一括編集",
            command=self._open_bulk_duel_editor,
        )
        self.history_bulk_button.pack(side="left", padx=(0, 6))
        self.history_filter_button = self._icon_button(
            toolbar, "filter", "録画履歴を絞り込む", self.open_history_filter
        )
        self.history_filter_button.pack(side="left", padx=(0, 6))
        self.history_filter_count_var = tk.StringVar(value="")
        ttk.Label(
            toolbar,
            textvariable=self.history_filter_count_var,
            style="Muted.TLabel",
        ).pack(side="left", padx=(0, 6))
        self._icon_button(
            toolbar, "clear_filter", "録画履歴の絞り込みを解除", self.clear_history_filter
        ).pack(side="left")
        action_bar = ttk.Frame(toolbar, style="Surface.TFrame")
        action_bar.pack(side="right")
        commands = {
            "play": self.play_selected_history,
            "edit": self.edit_selected_duel_record,
            "folder": self.reveal_selected_history,
            "delete": self.delete_selected_history,
        }
        for icon, label, shortcut in HISTORY_ROW_ACTIONS:
            button = self._icon_button(
                action_bar,
                icon,
                f"{label} ({shortcut})",
                commands[icon],
                state="disabled",
            )
            button.pack(side="left", padx=(0, 6))
            self.history_action_buttons[icon] = button
        ttk.Separator(action_bar, orient="vertical").pack(
            side="left", fill="y", padx=(2, 8)
        )
        self.history_timeline_button = self._icon_button(
            action_bar,
            "timeline",
            "タイムラインを表示",
            command=self.show_selected_timeline,
            state="disabled",
        )
        self.history_timeline_button.pack(side="left", padx=(0, 6))
        self.history_diagnostic_button = self._icon_button(
            action_bar,
            "diagnostic",
            "録画診断を表示",
            command=self.show_selected_history_diagnostic,
            state="disabled",
        )
        self.history_diagnostic_button.pack(side="left", padx=(0, 6))
        self.history_relink_button = self._icon_button(
            action_bar,
            "link",
            "欠損した録画ファイルを再関連付け",
            command=self.relink_selected_history,
            state="disabled",
        )
        self.history_relink_button.pack(side="left", padx=(0, 6))
        self.history_duplicates_button = self._icon_button(
            action_bar,
            "duplicates",
            "重複戦績候補を比較",
            command=self.open_duplicate_candidates,
        )
        self.history_duplicates_button.pack(side="left", padx=(0, 6))
        self._icon_button(
            action_bar,
            "refresh",
            "録画履歴を更新",
            self.refresh_history,
        ).pack(side="left", padx=(0, 6))
        self._icon_button(
            action_bar,
            "test",
            "録画履歴の整合性を確認",
            self.check_history,
        ).pack(side="left")
        panel = self._surface(page, padding=(0, 0))
        panel.pack(fill="both", expand=True)
        columns = (
            "started",
            "deck",
            "result",
            "order",
            "coin_face",
            "coin_outcome",
            "duel_type",
            "duration",
            "size",
            "origin",
        )
        self.history_tree = ttk.Treeview(
            panel, columns=columns, show="headings", selectmode="extended"
        )
        history_scrollbar = ttk.Scrollbar(
            panel, orient="horizontal", command=self.history_tree.xview
        )
        self.history_tree.configure(xscrollcommand=history_scrollbar.set)
        for key, label, width in (
            ("started", "開始日時", 155),
            ("deck", "デッキ名", 170),
            ("result", "勝敗", 90),
            ("order", "先後", 75),
            ("coin_face", "コイン", 65),
            ("coin_outcome", "トス勝敗", 80),
            ("duel_type", "対戦種別", 105),
            ("duration", "時間", 85),
            ("size", "サイズ", 100),
            ("origin", "登録元", 80),
        ):
            self.history_tree.heading(key, text=label)
            self.history_tree.column(key, width=width, stretch=key == "started")
        history_scrollbar.pack(side="bottom", fill="x")
        self.history_tree.pack(fill="both", expand=True)
        self.history_tree.bind("<<TreeviewSelect>>", self._history_selection_changed)
        self.history_tree.bind(
            "<Double-Button-1>", lambda _event: self.play_selected_history()
        )
        self.history_tree.bind("<Return>", lambda _event: self.play_selected_history())
        self.history_tree.bind(
            "<Control-e>", lambda _event: self.edit_selected_duel_record()
        )
        self.history_tree.bind(
            "<Control-o>", lambda _event: self.reveal_selected_history()
        )
        self.history_tree.bind(
            "<Delete>", lambda _event: self.delete_selected_history()
        )
        self.history_tree.bind(
            "<Configure>", lambda _event: self.root.after_idle(self._draw_history_color_lines)
        )
        self.history_tree.bind(
            "<MouseWheel>", lambda _event: self.root.after_idle(self._draw_history_color_lines), add="+"
        )
        self.widgets["history_table"] = self.history_tree
        self.widgets["history_play"] = self.history_action_buttons["play"]
        self.widgets["history_reveal"] = self.history_action_buttons["folder"]
        self.widgets["history_diagnostic"] = self.history_diagnostic_button
        self.widgets["history_duel"] = self.history_action_buttons["edit"]
        self.widgets["history_timeline"] = self.history_timeline_button
        self.widgets["history_relink"] = self.history_relink_button
        self.widgets["history_duplicates"] = self.history_duplicates_button
        self.widgets["history_delete"] = self.history_action_buttons["delete"]
        self.widgets["history_add"] = self.history_add_button
        self.widgets["history_incomplete"] = self.history_incomplete_button
        self.widgets["history_bulk"] = self.history_bulk_button

    def _build_statistics_page(self) -> None:
        page = self._new_page("statistics")
        summary = self._surface(page, padding=(18, 14))
        summary.pack(fill="x", pady=(0, 10))
        self.statistics_overall_rate_var = tk.StringVar(value="-")
        self.statistics_overall_detail_var = tk.StringVar(value="確定済み対戦を集計中")
        self.statistics_filtered_rate_var = tk.StringVar(value="-")
        self.statistics_filtered_detail_var = tk.StringVar(value="条件を指定できます")
        ttk.Label(summary, text="全体勝率", style="MetricLabel.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            summary,
            textvariable=self.statistics_overall_rate_var,
            style="Metric.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(
            summary,
            textvariable=self.statistics_overall_detail_var,
            style="Muted.TLabel",
        ).grid(row=2, column=0, sticky="w")
        ttk.Separator(summary, orient="vertical").grid(
            row=0, column=1, rowspan=3, sticky="ns", padx=26
        )
        ttk.Label(summary, text="条件適用後", style="MetricLabel.TLabel").grid(
            row=0, column=2, sticky="w"
        )
        ttk.Label(
            summary,
            textvariable=self.statistics_filtered_rate_var,
            style="Metric.TLabel",
        ).grid(row=1, column=2, sticky="w", pady=(2, 0))
        ttk.Label(
            summary,
            textvariable=self.statistics_filtered_detail_var,
            style="Muted.TLabel",
        ).grid(row=2, column=2, sticky="w")
        self.statistics_order_summary_var = tk.StringVar(value="先攻時 -\n後攻時 -")
        ttk.Separator(summary, orient="vertical").grid(
            row=0, column=3, rowspan=3, sticky="ns", padx=26
        )
        ttk.Label(summary, text="先後別勝率", style="MetricLabel.TLabel").grid(
            row=0, column=4, sticky="w"
        )
        ttk.Label(
            summary, textvariable=self.statistics_order_summary_var, style="Body.TLabel"
        ).grid(row=1, column=4, rowspan=2, sticky="w")
        summary.columnconfigure(0, weight=1)
        summary.columnconfigure(2, weight=1)

        filters = self._surface(page, padding=(14, 11))
        filters.pack(fill="x", pady=(0, 10))
        self.statistics_date_from_var = tk.StringVar()
        self.statistics_date_to_var = tk.StringVar()
        self.statistics_deck_var = tk.StringVar(value="すべて")
        self.statistics_tag_var = tk.StringVar(value="すべて")
        self.statistics_order_var = tk.StringVar(value="すべて")
        self.statistics_coin_face_var = tk.StringVar(value="すべて")
        self.statistics_coin_outcome_var = tk.StringVar(value="すべて")
        self.statistics_granularity_var = tk.StringVar(value="月")
        fields = (
            ("開始日", self.statistics_date_from_var),
            ("終了日", self.statistics_date_to_var),
        )
        for column, (label, variable) in enumerate(fields):
            ttk.Label(filters, text=label, style="Muted.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0, 8)
            )
            holder, calendar_button = self._date_picker(
                filters, variable, f"{label}をカレンダーから選択"
            )
            holder.grid(row=1, column=column, sticky="ew", padx=(0, 8))
            self.widgets[
                "statistics_date_from_picker"
                if column == 0
                else "statistics_date_to_picker"
            ] = calendar_button
        self.statistics_season_var = tk.StringVar(value="すべて")
        ttk.Label(filters, text="シーズン", style="Muted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        self.statistics_season_combo = ttk.Combobox(
            filters,
            textvariable=self.statistics_season_var,
            state="readonly",
            values=("すべて", "未設定"),
            width=16,
        )
        self.statistics_season_combo.grid(row=1, column=2, sticky="ew", padx=(0, 8))
        ttk.Label(filters, text="デッキ", style="Muted.TLabel").grid(
            row=0, column=3, sticky="w", padx=(0, 8)
        )
        self.statistics_deck_combo = ttk.Combobox(
            filters,
            textvariable=self.statistics_deck_var,
            state="readonly",
            values=("すべて",),
            width=18,
        )
        self.statistics_deck_combo.grid(row=1, column=3, sticky="ew", padx=(0, 8))
        ttk.Label(filters, text="タグ", style="Muted.TLabel").grid(
            row=0, column=4, sticky="w", padx=(0, 8)
        )
        self.statistics_tag_combo = ttk.Combobox(
            filters,
            textvariable=self.statistics_tag_var,
            state="readonly",
            values=("すべて",),
            width=16,
        )
        self.statistics_tag_combo.grid(row=1, column=4, sticky="ew", padx=(0, 8))
        ttk.Label(filters, text="先後", style="Muted.TLabel").grid(
            row=0, column=5, sticky="w", padx=(0, 8)
        )
        ttk.Combobox(
            filters,
            textvariable=self.statistics_order_var,
            state="readonly",
            values=("すべて", "先攻", "後攻"),
            width=8,
        ).grid(row=1, column=5, sticky="ew", padx=(0, 8))
        ttk.Label(filters, text="推移単位", style="Muted.TLabel").grid(
            row=0, column=6, sticky="w", padx=(0, 8)
        )
        ttk.Combobox(
            filters,
            textvariable=self.statistics_granularity_var,
            state="readonly",
            values=("日", "週", "月"),
            width=7,
        ).grid(row=1, column=6, sticky="ew", padx=(0, 10))
        ttk.Button(
            filters,
            text="条件を適用",
            style="Primary.TButton",
            command=self.refresh_statistics,
        ).grid(row=1, column=7, sticky="ew", padx=(0, 6))
        ttk.Button(filters, text="クリア", command=self.clear_statistics_filters).grid(
            row=1, column=8, sticky="ew"
        )
        ttk.Label(filters, text="日付は YYYY-MM-DD", style="Muted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(5, 0)
        )
        ttk.Label(filters, text="コインの面", style="Muted.TLabel").grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Combobox(
            filters,
            textvariable=self.statistics_coin_face_var,
            state="readonly",
            values=("すべて", "表", "裏", "未設定"),
        ).grid(row=4, column=0, sticky="ew", padx=(0, 8))
        ttk.Label(filters, text="コイントス勝敗", style="Muted.TLabel").grid(
            row=3, column=1, sticky="w", pady=(8, 0)
        )
        ttk.Combobox(
            filters,
            textvariable=self.statistics_coin_outcome_var,
            state="readonly",
            values=("すべて", "勝ち", "負け", "未設定"),
        ).grid(row=4, column=1, sticky="ew", padx=(0, 8))
        self.statistics_filter_status_var = tk.StringVar(value="すべての確定済み対戦")
        ttk.Label(
            filters,
            textvariable=self.statistics_filter_status_var,
            style="Muted.TLabel",
        ).grid(row=2, column=2, columnspan=6, sticky="e", pady=(5, 0))
        for column in range(7):
            filters.columnconfigure(column, weight=1, uniform="statistics-filter")
        filters.columnconfigure(7, weight=0, minsize=106)
        filters.columnconfigure(8, weight=0, minsize=82)

        notebook = ttk.Notebook(page)
        notebook.pack(fill="both", expand=True)
        trend_page = ttk.Frame(notebook, style="Surface.TFrame", padding=(12, 10))
        deck_page = ttk.Frame(notebook, style="Surface.TFrame", padding=(0, 0))
        order_page = ttk.Frame(notebook, style="Surface.TFrame", padding=(0, 0))
        coin_page = ttk.Frame(notebook, style="Surface.TFrame", padding=(0, 0))
        notebook.add(trend_page, text="勝利数・勝率推移")
        notebook.add(deck_page, text="デッキ別全体")
        notebook.add(order_page, text="デッキ先後別")
        notebook.add(coin_page, text="コイントス別")
        self.statistics_chart = StatisticsTrendChart(trend_page, colors=self.COLORS)
        self.statistics_chart.pack(fill="both", expand=True)
        self.statistics_deck_tree = self._build_statistics_tree(deck_page, "デッキ")
        self.statistics_order_tree = self._build_statistics_tree(
            order_page, "デッキ・先後"
        )
        self.statistics_coin_tree = self._build_statistics_tree(
            coin_page, "コイントス"
        )
        self.widgets["statistics_filters"] = filters
        self.widgets["statistics_chart"] = self.statistics_chart
        self.widgets["statistics_deck_table"] = self.statistics_deck_tree
        self.widgets["statistics_order_table"] = self.statistics_order_tree
        self.widgets["statistics_coin_table"] = self.statistics_coin_tree

    def _build_statistics_tree(
        self, parent: tk.Misc, first_heading: str
    ) -> ttk.Treeview:
        columns = ("group", "matches", "wins", "losses", "draws", "rate")
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for key, label, width in (
            ("group", first_heading, 260),
            ("matches", "対戦数", 100),
            ("wins", "勝ち", 90),
            ("losses", "負け", 90),
            ("draws", "引分", 90),
            ("rate", "勝率", 110),
        ):
            tree.heading(key, text=label)
            tree.column(
                key,
                width=width,
                anchor="w" if key == "group" else "center",
                stretch=key == "group",
            )
        tree.pack(fill="both", expand=True)
        return tree

    def _build_catalog_pages(self) -> None:
        self._build_catalog_page("deck")
        self._build_catalog_page("tag")

    def _build_catalog_page(self, kind: str) -> None:
        is_tag = kind == "tag"
        page_key = "tags" if is_tag else "decks"
        title = "タグ" if is_tag else "デッキ名"
        page = self._new_page(page_key)
        toolbar = self._surface(page, padding=(14, 10))
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text=f"{title}管理", style="Heading.TLabel").pack(
            side="left"
        )
        self._icon_button(
            toolbar,
            "refresh",
            f"{title}一覧を更新",
            lambda selected=kind: self.refresh_catalog(selected),
        ).pack(side="right")

        editor = self._surface(page, padding=(14, 12))
        editor.pack(fill="x", pady=(0, 10))
        name_var = tk.StringVar()
        description_var = tk.StringVar()
        color_var = tk.StringVar(value="#4F6F8F" if is_tag else "#2F6B5F")
        self.catalog_name_vars[kind] = name_var
        self.catalog_description_vars[kind] = description_var
        self.catalog_color_vars[kind] = color_var
        ttk.Label(editor, text="名前", style="Body.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(editor, textvariable=name_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 12)
        )
        ttk.Label(editor, text="説明", style="Body.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0)
        )
        ttk.Entry(editor, textvariable=description_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 12), pady=(8, 0)
        )
        ttk.Label(editor, text="カラー", style="Body.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        color_button = tk.Button(
            editor,
            textvariable=color_var,
            width=10,
            relief="flat",
            borderwidth=1,
            highlightthickness=1,
            padx=8,
            pady=5,
            font=("Segoe UI Semibold", 9),
            command=lambda selected=kind: self.choose_catalog_color(selected),
        )
        color_button.grid(row=0, column=3, padx=(0, 12))
        self.catalog_color_buttons[kind] = color_button
        self._set_catalog_color(kind, color_var.get())
        if not is_tag:
            ttk.Checkbutton(
                editor,
                text="相手デッキのみで使用",
                variable=self.catalog_opponent_only_var,
            ).grid(row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))
            ttk.Checkbutton(
                editor,
                text="履歴・統計の選択肢で非表示",
                variable=self.catalog_hidden_var,
            ).grid(row=2, column=2, columnspan=2, sticky="w", pady=(8, 0))
        self._icon_button(
            editor,
            "add",
            f"{title}を追加",
            lambda selected=kind: self.add_catalog_entry(selected),
            style="Primary.TButton",
        ).grid(row=0, column=4, padx=(0, 8))
        update_button = self._icon_button(
            editor,
            "save",
            f"{title}を保存",
            lambda selected=kind: self.update_catalog_entry(selected),
            state="disabled",
        )
        update_button.grid(row=0, column=5, padx=(0, 8))
        delete_button = self._icon_button(
            editor,
            "delete",
            f"{title}を削除",
            lambda selected=kind: self.delete_catalog_entry(selected),
            state="disabled",
        )
        delete_button.grid(row=0, column=6)
        self.catalog_update_buttons[kind] = update_button
        self.catalog_delete_buttons[kind] = delete_button
        editor.columnconfigure(1, weight=1)

        panel = self._surface(page, padding=(0, 0))
        panel.pack(fill="both", expand=True)
        columns = ("name", "description", "flags")
        tree = ttk.Treeview(
            panel,
            columns=columns,
            show="tree headings",
            selectmode="browse",
        )
        tree.heading("name", text="名前")
        tree.heading("description", text="説明")
        tree.heading("flags", text="用途")
        tree.column("name", width=220, stretch=False)
        tree.column("description", width=520, stretch=True)
        tree.column("flags", width=190, stretch=False)
        tree.heading("#0", text="カラー")
        tree.column("#0", width=125, stretch=False, anchor="center")
        tree.pack(fill="both", expand=True)
        tree.bind(
            "<<TreeviewSelect>>",
            lambda _event, selected=kind: self._catalog_selection_changed(selected),
        )
        self.catalog_trees[kind] = tree
        self.widgets[f"{kind}_catalog_table"] = tree
        if kind == "deck":
            self.widgets["catalog_table"] = tree

    def _build_seasons_page(self) -> None:
        page = self._new_page("seasons")
        toolbar = self._surface(page, padding=(14, 10))
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="シーズン管理", style="Heading.TLabel").pack(
            side="left"
        )
        self._icon_button(
            toolbar, "refresh", "シーズン一覧を更新", self.refresh_seasons
        ).pack(side="right")

        editor = self._surface(page, padding=(14, 12))
        editor.pack(fill="x", pady=(0, 10))
        self.season_name_var = tk.StringVar()
        self.season_type_var = tk.StringVar(value="ランク")
        self.season_start_var = tk.StringVar(value=str(date.today()))
        self.season_end_var = tk.StringVar(value=str(date.today()))
        self.season_description_var = tk.StringVar()
        for column, label in enumerate(("名前", "種別", "開始日", "終了日")):
            ttk.Label(editor, text=label, style="Muted.TLabel").grid(
                row=0, column=column, sticky="w", padx=(0, 8)
            )
        ttk.Entry(editor, textvariable=self.season_name_var).grid(
            row=1, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Combobox(
            editor,
            textvariable=self.season_type_var,
            values=("ランク", "イベント", "カスタム"),
            state="readonly",
            width=12,
        ).grid(row=1, column=1, sticky="ew", padx=(0, 8))
        for column, variable, label in (
            (2, self.season_start_var, "開始日"),
            (3, self.season_end_var, "終了日"),
        ):
            holder = ttk.Frame(editor, style="Surface.TFrame")
            holder.grid(row=1, column=column, sticky="ew", padx=(0, 8))
            ttk.Entry(holder, textvariable=variable, width=12).pack(
                side="left", fill="x", expand=True
            )
            self._icon_button(
                holder,
                "calendar",
                f"{label}をカレンダーから選択",
                lambda selected=variable: self.open_calendar_picker(selected),
            ).pack(side="left")
        button_row = ttk.Frame(editor, style="Surface.TFrame")
        button_row.grid(row=1, column=4, sticky="e")
        self._icon_button(
            button_row,
            "add",
            "シーズンを追加",
            self.add_season,
            style="Primary.TButton",
        ).pack(side="left", padx=(0, 6))
        self.season_update_button = self._icon_button(
            button_row,
            "save",
            "選択したシーズンを保存",
            self.update_selected_season,
            state="disabled",
        )
        self.season_update_button.pack(side="left", padx=(0, 6))
        self.season_delete_button = self._icon_button(
            button_row,
            "delete",
            "選択したシーズンを削除またはアーカイブ",
            self.delete_selected_season,
            state="disabled",
        )
        self.season_delete_button.pack(side="left", padx=(0, 6))
        self.season_report_button = self._icon_button(
            button_row,
            "report",
            "選択したシーズンのレポートを開く",
            self.open_selected_season_report,
            state="disabled",
        )
        self.season_report_button.pack(side="left")
        ttk.Label(editor, text="説明", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(editor, textvariable=self.season_description_var).grid(
            row=3, column=0, columnspan=4, sticky="ew", padx=(0, 8)
        )
        editor.columnconfigure(0, weight=2)
        editor.columnconfigure(2, weight=1)
        editor.columnconfigure(3, weight=1)
        panel = self._surface(page, padding=(0, 0))
        panel.pack(fill="both", expand=True)
        self.season_tree = ttk.Treeview(
            panel,
            columns=("name", "type", "period", "status"),
            show="tree headings",
            selectmode="browse",
        )
        for key, label, width in (
            ("name", "シーズン", 240),
            ("type", "種別", 100),
            ("period", "期間", 220),
            ("status", "状態", 90),
        ):
            self.season_tree.heading(key, text=label)
            self.season_tree.column(key, width=width, stretch=key == "name")
        self.season_tree.heading("#0", text="")
        self.season_tree.column("#0", width=16, minwidth=16, stretch=False)
        self.season_tree.pack(fill="both", expand=True)
        self.season_tree.bind(
            "<Double-Button-1>", lambda _event: self.open_selected_season_report()
        )
        self.season_tree.bind(
            "<<TreeviewSelect>>", lambda _event: self._season_selection_changed()
        )
        self.widgets["season_table"] = self.season_tree

    def _build_prepare_page(self) -> None:
        page = self._new_page("prepare")
        form = self._surface(page)
        form.pack(fill="x", pady=(0, 10))
        ttk.Label(form, text="アップロード用MP4準備", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        ttk.Label(form, text="録画ID", style="Body.TLabel").grid(
            row=1, column=0, sticky="w", pady=(12, 4)
        )
        ttk.Label(form, text="タイトル", style="Body.TLabel").grid(
            row=1, column=1, sticky="w", pady=(12, 4)
        )
        self.prepare_recording_var = tk.StringVar()
        self.prepare_title_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.prepare_recording_var, width=44).grid(
            row=2, column=0, sticky="ew", padx=(0, 8)
        )
        ttk.Entry(form, textvariable=self.prepare_title_var, width=42).grid(
            row=2, column=1, sticky="ew", padx=(0, 8)
        )
        ttk.Button(
            form,
            text="キューへ追加",
            style="Primary.TButton",
            command=self.enqueue_preparation,
        ).grid(row=2, column=2, padx=(0, 8))
        ttk.Button(form, text="待機中を実行", command=self.process_preparations).grid(
            row=2, column=3
        )
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        panel = self._surface(page, padding=(0, 0))
        panel.pack(fill="both", expand=True)
        self.prepare_tree = ttk.Treeview(
            panel, columns=("state", "title", "recording", "queue"), show="headings"
        )
        for key, label, width in (
            ("state", "状態", 100),
            ("title", "タイトル", 240),
            ("recording", "録画ID", 260),
            ("queue", "キューID", 260),
        ):
            self.prepare_tree.heading(key, text=label)
            self.prepare_tree.column(
                key, width=width, stretch=key in {"title", "recording", "queue"}
            )
        self.prepare_tree.pack(fill="both", expand=True)
        self.widgets["prepare_table"] = self.prepare_tree

    def _build_settings_page(self) -> None:
        page = self._new_page("settings")
        notebook = ttk.Notebook(page)
        notebook.pack(fill="both", expand=True)
        panel = self._surface(notebook, padding=(20, 18))
        notebook.add(panel, text="録画設定")
        ttk.Label(panel, text="録画設定", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        self.ffmpeg_setup_button = ttk.Button(
            panel,
            text="FFmpegを導入",
            command=self.show_ffmpeg_setup,
        )
        self.ffmpeg_setup_button.grid(row=0, column=2, sticky="e")
        self.setting_vars = {
            "recorder.ffmpeg_path": tk.StringVar(),
            "recorder.audio_input": tk.StringVar(),
            "recorder.audio_gain_db": tk.StringVar(),
            "recorder.audio_sample_rate": tk.StringVar(),
            "recorder.audio_channels": tk.StringVar(),
            "recorder.frame_rate": tk.StringVar(),
            "recorder.video_bitrate_kbps": tk.StringVar(),
            "recorder.capture_width": tk.StringVar(),
            "recorder.capture_height": tk.StringVar(),
            "detection.visual_maximum_fps": tk.StringVar(),
            "detection.visual_language": tk.StringVar(),
            "detection.visual_minimum_confidence": tk.StringVar(),
        }
        fields = (
            ("recorder.ffmpeg_path", "FFmpeg", 1, 0, 3),
            ("recorder.frame_rate", "フレームレート", 5, 0, 1),
            ("recorder.video_bitrate_kbps", "映像ビットレート（kbps）", 5, 1, 1),
            ("recorder.audio_gain_db", "音声ゲイン（dB）", 5, 2, 1),
            ("recorder.capture_width", "出力幅（0で元サイズ）", 7, 0, 1),
            ("recorder.capture_height", "出力高さ（0で元サイズ）", 7, 1, 1),
        )
        for key, label, row, column, span in fields:
            ttk.Label(panel, text=label, style="Body.TLabel").grid(
                row=row, column=column, columnspan=span, sticky="w", pady=(14, 4)
            )
            ttk.Entry(panel, textvariable=self.setting_vars[key]).grid(
                row=row + 1,
                column=column,
                columnspan=span,
                sticky="ew",
                padx=(0, 12 if column == 0 and span == 1 else 0),
            )
        ttk.Label(panel, text="音声形式", style="Body.TLabel").grid(
            row=7, column=2, sticky="w", pady=(14, 4)
        )
        audio_format_row = ttk.Frame(panel, style="Surface.TFrame")
        audio_format_row.grid(row=8, column=2, sticky="ew")
        ttk.Entry(
            audio_format_row,
            textvariable=self.setting_vars["recorder.audio_sample_rate"],
            width=9,
        ).pack(side="left")
        ttk.Label(audio_format_row, text="Hz /", style="Muted.TLabel").pack(
            side="left", padx=(4, 4)
        )
        ttk.Entry(
            audio_format_row,
            textvariable=self.setting_vars["recorder.audio_channels"],
            width=4,
        ).pack(side="left")
        ttk.Label(audio_format_row, text="ch", style="Muted.TLabel").pack(
            side="left", padx=(4, 0)
        )
        ttk.Label(panel, text="音声入力", style="Body.TLabel").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(14, 4)
        )
        audio_row = ttk.Frame(panel, style="Surface.TFrame")
        audio_row.grid(row=4, column=0, columnspan=3, sticky="ew")
        self.audio_choice_var = tk.StringVar(value="音声なし")
        self.audio_inputs_by_label: dict[str, object] = {}
        self.audio_input_combo = ttk.Combobox(
            audio_row,
            textvariable=self.audio_choice_var,
            values=("音声なし",),
            state="readonly",
        )
        self.audio_input_combo.pack(side="left", fill="x", expand=True)
        self.audio_input_combo.bind("<<ComboboxSelected>>", self._audio_input_selected)
        self._icon_button(
            audio_row, "refresh", "音声入力候補を更新", self.refresh_audio_inputs
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            audio_row, text="▷ テスト", command=self.test_selected_audio_input
        ).pack(side="left", padx=(8, 0))
        self.audio_status_var = tk.StringVar(value="")
        ttk.Label(
            audio_row, textvariable=self.audio_status_var, style="Muted.TLabel"
        ).pack(side="left", padx=(10, 0))
        self.auto_start_var = tk.BooleanVar(value=True)
        self.auto_stop_var = tk.BooleanVar(value=True)
        self.visual_detection_var = tk.BooleanVar(value=True)
        self.windows_notifications_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            panel, text="ウィンドウ検出時に自動開始", variable=self.auto_start_var
        ).grid(row=9, column=0, sticky="w", pady=(18, 0))
        ttk.Checkbutton(
            panel, text="ウィンドウ消失時に自動停止", variable=self.auto_stop_var
        ).grid(row=9, column=1, sticky="w", pady=(18, 0))
        ttk.Checkbutton(
            panel, text="対戦イベントを自動判定", variable=self.visual_detection_var
        ).grid(row=9, column=2, sticky="w", pady=(18, 0))
        ttk.Checkbutton(
            panel,
            text="録画イベントをWindows通知",
            variable=self.windows_notifications_var,
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))
        for column, (key, label) in enumerate(
            (
                ("detection.visual_maximum_fps", "自動判定fps（最大2）"),
                ("detection.visual_language", "UI言語（auto / ja / en）"),
                ("detection.visual_minimum_confidence", "候補閾値（0.70以上）"),
            )
        ):
            ttk.Label(panel, text=label, style="Body.TLabel").grid(
                row=11, column=column, sticky="w", pady=(14, 4)
            )
            ttk.Entry(panel, textvariable=self.setting_vars[key]).grid(
                row=12, column=column, sticky="ew", padx=(0, 12 if column < 2 else 0)
            )
        ttk.Label(panel, text="データ保存先", style="Body.TLabel").grid(
            row=13, column=0, sticky="w", pady=(18, 4)
        )
        self.runtime_path_var = tk.StringVar(
            value=str(self.service.runtime_data_directory())
        )
        ttk.Label(
            panel,
            textvariable=self.runtime_path_var,
            style="Muted.TLabel",
        ).grid(row=14, column=0, columnspan=3, sticky="w")
        footer = ttk.Frame(panel, style="Surface.TFrame")
        footer.grid(row=15, column=0, columnspan=3, sticky="ew", pady=(18, 0))
        self.settings_status_var = tk.StringVar(value="")
        ttk.Label(
            footer, textvariable=self.settings_status_var, style="Muted.TLabel"
        ).pack(side="left")
        self._icon_button(footer, "refresh", "設定を再読込", self.load_settings).pack(
            side="right"
        )
        self._icon_button(
            footer, "save", "設定を保存", self.save_settings, style="Primary.TButton"
        ).pack(side="right", padx=(0, 8))
        data_panel = self._surface(notebook, padding=(20, 18))
        notebook.add(data_panel, text="管理データ")
        data_header = ttk.Frame(data_panel, style="Surface.TFrame")
        data_header.pack(fill="x", pady=(0, 18))
        ttk.Label(data_header, text="履歴・デッキ・タグ・シーズン", style="Heading.TLabel").pack(side="left")
        self._icon_button(
            data_header, "import", "履歴・デッキ・タグ・シーズンを読み込む", self.import_managed_data
        ).pack(side="right")
        self._icon_button(
            data_header, "export", "履歴・デッキ・タグ・シーズンを書き出す", self.export_managed_data
        ).pack(side="right", padx=(0, 8))
        ttk.Separator(data_panel, orient="horizontal").pack(fill="x", pady=(0, 18))
        ttk.Label(data_panel, text="初期化", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 10)
        )
        reset_grid = ttk.Frame(data_panel, style="Surface.TFrame")
        reset_grid.pack(fill="x")
        for column, (scope, label) in enumerate((
            ("history", "履歴情報"),
            ("decks", "デッキ"),
            ("tags", "タグ"),
            ("seasons", "シーズン"),
        )):
            ttk.Button(
                reset_grid,
                text=f"{label}を初期化",
                command=lambda selected=scope, name=label: self.reset_managed_data(
                    selected, name
                ),
            ).grid(row=0, column=column, sticky="ew", padx=(0, 8 if column < 3 else 0))
            reset_grid.columnconfigure(column, weight=1, uniform="reset-control")
        ttk.Separator(data_panel, orient="horizontal").pack(fill="x", pady=(22, 18))
        protection_header = ttk.Frame(data_panel, style="Surface.TFrame")
        protection_header.pack(fill="x")
        ttk.Label(
            protection_header, text="データ保全", style="Heading.TLabel"
        ).pack(side="left")
        self._icon_button(
            protection_header,
            "import",
            "検証済みバックアップから復元",
            self.restore_data_backup,
        ).pack(side="right")
        self._icon_button(
            protection_header,
            "diagnostic",
            "データ整合性を診断",
            self.run_data_integrity_diagnosis,
        ).pack(side="right", padx=(0, 8))
        self._icon_button(
            protection_header,
            "save",
            "データバックアップを作成",
            self.create_data_backup,
            style="Primary.TButton",
        ).pack(side="right", padx=(0, 8))
        self.data_protection_status_var = tk.StringVar(value="保全状態を確認中")
        ttk.Label(
            data_panel,
            textvariable=self.data_protection_status_var,
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(10, 0))
        self.data_backup_tree = ttk.Treeview(
            data_panel,
            columns=("created", "reason", "schema", "size", "protected"),
            show="headings",
            height=5,
        )
        for key, label, width in (
            ("created", "作成日時", 155),
            ("reason", "作成契機", 190),
            ("schema", "DB版", 70),
            ("size", "サイズ", 90),
            ("protected", "保護", 70),
        ):
            self.data_backup_tree.heading(key, text=label)
            self.data_backup_tree.column(
                key, width=width, stretch=key == "reason", anchor="center" if key != "reason" else "w"
            )
        self.data_backup_tree.pack(fill="x", pady=(10, 0))
        self.widgets["data_protection_status"] = self.data_protection_status_var
        self.widgets["data_backup_table"] = self.data_backup_tree
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(2, weight=1)
        self.widgets["settings_form"] = notebook
        self.widgets["ffmpeg_setup"] = self.ffmpeg_setup_button

    def show_page(self, key: str) -> None:
        titles = {
            "record": "録画",
            "history": "戦績管理",
            "statistics": "統計",
            "decks": "デッキ名",
            "tags": "タグ",
            "seasons": "シーズン",
            "prepare": "MP4準備",
            "settings": "設定",
        }
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.page_title.configure(text=titles[key])
        for name, button in self.nav_buttons.items():
            selected = name == key
            button.configure(
                background=self.COLORS["sidebar_active"]
                if selected
                else self.COLORS["sidebar"],
                foreground=self.COLORS["primary"] if selected else self.COLORS["text"],
                font=("Segoe UI Semibold", 10) if selected else ("Segoe UI", 10),
            )
        self.current_page = key
        if key == "history":
            self.refresh_history()
        elif key == "statistics":
            self.refresh_statistics()
        elif key == "decks":
            self.refresh_catalog("deck")
        elif key == "tags":
            self.refresh_catalog("tag")
        elif key == "seasons":
            self.refresh_seasons()
        elif key == "prepare":
            self.refresh_preparations()
        elif key == "settings":
            self.load_settings()
            self.refresh_data_protection()

    def refresh_all(self) -> None:
        self.refresh_targets()
        self.run_diagnosis()
        self.refresh_history()
        self.refresh_active_seasons()

    def refresh_statistics(self) -> None:
        if self.smoke_mode:
            return
        try:
            filters = self._statistics_filters()
        except ValueError as exc:
            self._show_error(exc)
            return
        granularity = {"日": "day", "週": "week", "月": "month"}[
            self.statistics_granularity_var.get()
        ]
        self._run(
            lambda: (
                self.service.get_statistics_dashboard(filters, granularity=granularity),
                self.service.list_decks(),
                self.service.list_tags(),
                self.service.list_seasons(include_archived=True),
            ),
            self._statistics_loaded,
        )

    def clear_statistics_filters(self) -> None:
        self.statistics_date_from_var.set("")
        self.statistics_date_to_var.set("")
        self.statistics_deck_var.set("すべて")
        self.statistics_tag_var.set("すべて")
        self.statistics_order_var.set("すべて")
        self.statistics_coin_face_var.set("すべて")
        self.statistics_coin_outcome_var.set("すべて")
        self.statistics_season_var.set("すべて")
        self.statistics_granularity_var.set("月")
        self.refresh_statistics()

    def _statistics_filters(self) -> StatisticsFilter:
        date_from = _parse_filter_date(self.statistics_date_from_var.get(), "開始日")
        date_to = _parse_filter_date(self.statistics_date_to_var.get(), "終了日")
        order = {"すべて": None, "先攻": "first", "後攻": "second"}.get(
            self.statistics_order_var.get()
        )
        coin_face = {"すべて": None, "表": "heads", "裏": "tails", "未設定": "unknown"}[
            self.statistics_coin_face_var.get()
        ]
        coin_outcome = {"すべて": None, "勝ち": "win", "負け": "loss", "未設定": "unknown"}[
            self.statistics_coin_outcome_var.get()
        ]
        return StatisticsFilter(
            date_from=date_from,
            date_to=date_to,
            own_deck=self.statistics_decks_by_label.get(self.statistics_deck_var.get()),
            tag_entry_id=self.statistics_tags_by_label.get(
                self.statistics_tag_var.get()
            ),
            play_order=order,
            coin_face=coin_face,
            coin_toss_outcome=coin_outcome,
            season_id=self.statistics_seasons_by_label.get(
                self.statistics_season_var.get()
            ),
            season_unassigned=self.statistics_season_var.get() == "未設定",
        )

    def _statistics_loaded(
        self,
        payload: tuple[
            StatisticsDashboard,
            tuple[DuelCatalogEntry, ...],
            tuple[object, ...],
            tuple[DuelCatalogEntry, ...],
        ],
    ) -> None:
        dashboard, decks, tags, seasons = payload
        selected_deck = self.statistics_deck_var.get()
        selected_tag = self.statistics_tag_var.get()
        self.statistics_decks_by_label = {
            "すべて": None,
            **{
                entry.name: entry.name
                for entry in decks
                if not entry.hidden_from_history_statistics
            },
        }
        self.statistics_tags_by_label = {
            "すべて": None,
            **{entry.name: entry.entry_id for entry in tags},
        }
        self.statistics_deck_combo.configure(
            values=tuple(self.statistics_decks_by_label)
        )
        self.statistics_tag_combo.configure(values=tuple(self.statistics_tags_by_label))
        selected_season = self.statistics_season_var.get()
        self.statistics_seasons_by_label = {
            "すべて": None,
            "未設定": None,
            **{item.name: item.season_id for item in seasons},
        }
        self.statistics_season_combo.configure(
            values=tuple(self.statistics_seasons_by_label)
        )
        self.statistics_season_var.set(
            selected_season
            if selected_season in self.statistics_seasons_by_label
            else "すべて"
        )
        self.statistics_deck_var.set(
            selected_deck
            if selected_deck in self.statistics_decks_by_label
            else "すべて"
        )
        self.statistics_tag_var.set(
            selected_tag if selected_tag in self.statistics_tags_by_label else "すべて"
        )
        self.statistics_overall_rate_var.set(_format_win_rate(dashboard.overall))
        self.statistics_overall_detail_var.set(
            _format_statistics_detail(dashboard.overall)
        )
        self.statistics_filtered_rate_var.set(_format_win_rate(dashboard.filtered))
        self.statistics_filtered_detail_var.set(
            _format_statistics_detail(dashboard.filtered)
        )
        order_map = {item.key: item.metric for item in dashboard.by_play_order}
        self.statistics_order_summary_var.set(
            f"先攻時 {_format_win_rate(order_map.get('first', StatisticsMetric(0, 0, 0, 0)))}\n"
            f"後攻時 {_format_win_rate(order_map.get('second', StatisticsMetric(0, 0, 0, 0)))}"
        )
        active_conditions = []
        if dashboard.filters.date_from or dashboard.filters.date_to:
            active_conditions.append(
                f"期間 {dashboard.filters.date_from or '指定なし'} 〜 {dashboard.filters.date_to or '指定なし'}"
            )
        if dashboard.filters.own_deck:
            active_conditions.append(f"デッキ {dashboard.filters.own_deck}")
        if dashboard.filters.tag_entry_id:
            active_conditions.append(f"タグ {selected_tag}")
        if dashboard.filters.play_order:
            active_conditions.append(
                "先攻" if dashboard.filters.play_order == "first" else "後攻"
            )
        if dashboard.filters.coin_face is not None:
            active_conditions.append(
                f"コイン {'表' if dashboard.filters.coin_face == 'heads' else '裏' if dashboard.filters.coin_face == 'tails' else '未設定'}"
            )
        if dashboard.filters.coin_toss_outcome is not None:
            active_conditions.append(
                f"コイントス {'勝ち' if dashboard.filters.coin_toss_outcome == 'win' else '負け' if dashboard.filters.coin_toss_outcome == 'loss' else '未設定'}"
            )
        if dashboard.filters.season_id:
            active_conditions.append(f"シーズン {selected_season}")
        elif dashboard.filters.season_unassigned:
            active_conditions.append("シーズン 未設定")
        self.statistics_filter_status_var.set(
            " / ".join(active_conditions) or "すべての確定済み対戦"
        )
        self.statistics_chart.set_points(dashboard.trend)
        self._clear_tree(self.statistics_deck_tree)
        self._clear_tree(self.statistics_order_tree)
        self._clear_tree(self.statistics_coin_tree)
        for item in dashboard.by_deck:
            self.statistics_deck_tree.insert(
                "", "end", values=_statistics_breakdown_values(item.label, item.metric)
            )
        for item in dashboard.by_deck_play_order:
            self.statistics_order_tree.insert(
                "", "end", values=_statistics_breakdown_values(item.label, item.metric)
            )
        for item in (*dashboard.by_coin_face, *dashboard.by_coin_toss_outcome):
            self.statistics_coin_tree.insert(
                "", "end", values=_statistics_breakdown_values(item.label, item.metric)
            )

    def refresh_catalog(self, kind: str) -> None:
        if self.smoke_mode:
            return
        operation = self.service.list_tags if kind == "tag" else self.service.list_decks
        self._run(operation, lambda entries: self._catalog_loaded(kind, entries))

    def open_calendar_picker(self, variable: tk.StringVar) -> None:
        try:
            selected = (
                date.fromisoformat(variable.get()) if variable.get() else date.today()
            )
        except ValueError:
            selected = date.today()
        dialog = tk.Toplevel(self.root)
        dialog.title("日付を選択")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        current = [selected.year, selected.month]
        title = ttk.Label(frame, style="Heading.TLabel")
        title.grid(row=0, column=1, columnspan=5)

        def render() -> None:
            for child in frame.grid_slaves():
                if int(child.grid_info().get("row", 0)) >= 2:
                    child.destroy()
            title.configure(text=f"{current[0]}年 {current[1]}月")
            for column, label in enumerate(("月", "火", "水", "木", "金", "土", "日")):
                ttk.Label(frame, text=label).grid(row=2, column=column, padx=4, pady=4)
            for week_index, week in enumerate(calendar.monthcalendar(*current)):
                for column, day in enumerate(week):
                    if day:
                        ttk.Button(
                            frame,
                            text=str(day),
                            width=3,
                            command=lambda value=day: (
                                variable.set(
                                    date(current[0], current[1], value).isoformat()
                                ),
                                dialog.destroy(),
                            ),
                        ).grid(row=3 + week_index, column=column, padx=2, pady=2)

        def move(delta: int) -> None:
            current[1] += delta
            if current[1] < 1:
                current[:] = [current[0] - 1, 12]
            elif current[1] > 12:
                current[:] = [current[0] + 1, 1]
            render()

        ttk.Button(frame, text="‹", command=lambda: move(-1)).grid(row=0, column=0)
        ttk.Button(frame, text="›", command=lambda: move(1)).grid(row=0, column=6)
        render()
        dialog.grab_set()

    def _catalog_loaded(self, kind: str, entries: tuple[DuelCatalogEntry, ...]) -> None:
        for entry in entries:
            self.catalog_entries_by_id[str(entry.entry_id)] = entry
        tree = self.catalog_trees[kind]
        self._clear_tree(tree)
        self.catalog_color_images.clear()
        for entry in entries:
            color = entry.color or "#4F6F8F"
            image = self._tag_color_swatch(color)
            if image is not None:
                self.catalog_color_images[str(entry.entry_id)] = image
            tree.insert(
                "",
                "end",
                iid=str(entry.entry_id),
                text=color,
                image=image or "",
                values=(
                    entry.name,
                    entry.description,
                    " / ".join(
                        filter(
                            None,
                            (
                                "相手のみ" if entry.opponent_only else "",
                                "非表示"
                                if entry.hidden_from_history_statistics
                                else "",
                            ),
                        )
                    )
                    or "通常",
                ),
            )
        self._catalog_selection_changed(kind)

    def _catalog_selection_changed(self, kind: str) -> None:
        tree = self.catalog_trees[kind]
        selection = tree.selection()
        state = "normal" if selection else "disabled"
        self.catalog_update_buttons[kind].configure(state=state)
        self.catalog_delete_buttons[kind].configure(state=state)
        if not selection:
            return
        entry = self.catalog_entries_by_id.get(str(selection[0]))
        if entry is None:
            return
        self.catalog_name_vars[kind].set(entry.name)
        self.catalog_description_vars[kind].set(entry.description)
        self._set_catalog_color(
            kind, entry.color or ("#4F6F8F" if kind == "tag" else "#2F6B5F")
        )
        if kind == "deck":
            self.catalog_opponent_only_var.set(entry.opponent_only)
            self.catalog_hidden_var.set(entry.hidden_from_history_statistics)

    def choose_catalog_color(self, kind: str) -> None:
        current = self.catalog_color_vars[kind].get() or "#4F6F8F"
        _rgb, selected = colorchooser.askcolor(
            current, title="カラー", parent=self.root
        )
        if selected:
            self._set_catalog_color(kind, selected.upper())

    def _set_catalog_color(self, kind: str, color: str) -> None:
        self.catalog_color_vars[kind].set(color)
        button = self.catalog_color_buttons.get(kind)
        if button is not None:
            button.configure(
                background=color,
                activebackground=color,
                foreground=_contrast_text_color(color),
                activeforeground=_contrast_text_color(color),
            )

    def _vertical_color_line(self, color: str) -> tk.PhotoImage:
        image = tk.PhotoImage(master=self.root, width=10, height=22)
        image.put(self.COLORS["surface"], to=(0, 0, 10, 22))
        image.put(color, to=(3, 2, 6, 20))
        return image

    def _tag_color_swatch(self, color: str) -> tk.PhotoImage:
        image = tk.PhotoImage(master=self.root, width=22, height=18)
        image.put("#5f6368", to=(0, 0, 22, 18))
        image.put(color, to=(2, 2, 20, 16))
        return image

    def add_catalog_entry(self, kind: str) -> None:
        name = self.catalog_name_vars[kind].get()
        description = self.catalog_description_vars[kind].get()
        color = self.catalog_color_vars[kind].get()
        self._run(
            lambda: self.service.add_duel_catalog_entry(
                kind,
                name,
                description=description,
                color=color,
            ),
            lambda entry: (
                self._activity(
                    f"{'タグ' if kind == 'tag' else 'デッキ名'}を追加しました: {entry.name}"
                ),
                self.catalog_name_vars[kind].set(""),
                self.catalog_description_vars[kind].set(""),
                self.refresh_catalog(kind),
            ),
        )

    def update_catalog_entry(self, kind: str) -> None:
        tree = self.catalog_trees[kind]
        selection = tree.selection()
        if not selection:
            return
        entry = self.catalog_entries_by_id.get(str(selection[0]))
        if entry is None:
            return
        name = self.catalog_name_vars[kind].get()
        description = self.catalog_description_vars[kind].get()

        def operation() -> DuelCatalogEntry:
            if kind == "tag":
                return self.service.update_tag(
                    entry.entry_id,
                    name=name,
                    description=description,
                    color=self.catalog_color_vars[kind].get(),
                )
            return self.service.update_deck(
                entry.entry_id,
                name=name,
                description=description,
                color=self.catalog_color_vars[kind].get(),
                opponent_only=self.catalog_opponent_only_var.get(),
                hidden_from_history_statistics=self.catalog_hidden_var.get(),
            )

        self._run(
            operation,
            lambda updated: (
                self._activity(f"保存しました: {updated.name}"),
                self.refresh_catalog(kind),
            ),
        )

    def delete_catalog_entry(self, kind: str) -> None:
        tree = self.catalog_trees[kind]
        selection = tree.selection()
        if not selection:
            return
        entry = self.catalog_entries_by_id.get(str(selection[0]))
        if entry is None:
            return
        if not messagebox.askyesno(
            "辞書から削除",
            f"「{entry.name}」を選択肢から削除しますか？\n過去の対戦記録は変更されません。",
            parent=self.root,
        ):
            return
        self._run(
            lambda: self.service.delete_duel_catalog_entry(entry.entry_id),
            lambda deleted: (
                self._activity(
                    f"{'アーカイブ' if deleted.is_archived else '削除'}しました: {deleted.name}"
                ),
                self.catalog_name_vars[kind].set(""),
                self.catalog_description_vars[kind].set(""),
                self.refresh_catalog(kind),
            ),
        )

    def refresh_targets(self) -> None:
        self._run(self.service.list_capture_targets, self._targets_loaded)

    def _targets_loaded(self, targets: tuple[CaptureTarget, ...]) -> None:
        self.targets_by_label = {target.label: target for target in targets}
        labels = tuple(self.targets_by_label)
        self.target_combo.configure(values=labels)
        config = self.service.load_config().config
        selected = next(
            (
                target.label
                for target in targets
                if target.mode.value == config.capture_mode
                and (
                    not config.capture_target_id
                    or target.identifier == config.capture_target_id
                )
            ),
            labels[0] if labels else "",
        )
        self.target_var.set(selected)
        self._activity(f"録画対象を{len(targets)}件検出しました")

    def save_selected_target(self) -> None:
        target = self.targets_by_label.get(self.target_var.get())
        if target is None:
            self._show_error(ValueError("録画対象を選択してください"))
            return
        self._run(
            lambda: self.service.select_capture_target(target),
            lambda _config: self._activity(f"録画対象を保存しました: {target.label}"),
        )

    def run_diagnosis(self) -> None:
        self._run(self.service.diagnose, self._diagnosis_loaded)

    def open_visual_diagnostics(self) -> None:
        directory = self.service.paths.logs / "visual-monitor"
        directory.mkdir(parents=True, exist_ok=True)
        if not hasattr(os, "startfile"):
            raise OSError("数値診断フォルダはWindows Explorerでのみ開けます")
        os.startfile(str(directory.resolve()))  # type: ignore[attr-defined]

    def export_visual_diagnostics(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="自動監視診断を保存",
            defaultextension=".zip",
            filetypes=(("ZIP", "*.zip"),),
        )
        if destination:
            self._run(
                lambda: self.service.export_visual_diagnostics(Path(destination)),
                lambda path: self._activity(f"自動監視診断を保存しました: {path}"),
            )

    def toggle_visual_details(self) -> None:
        visible = not self.visual_details_visible.get()
        self.visual_details_visible.set(visible)
        if visible:
            self.visual_details_label.grid(row=4, column=0, sticky="w", pady=(3, 0))
            self.visual_details_button.configure(text=ICON_GLYPHS["collapse"])
            self.visual_details_button.accessible_name = "自動判定の詳細を閉じる"  # type: ignore[attr-defined]
        else:
            self.visual_details_label.grid_remove()
            self.visual_details_button.configure(text=ICON_GLYPHS["expand"])
            self.visual_details_button.accessible_name = "自動判定の詳細を表示"  # type: ignore[attr-defined]

    def _diagnosis_loaded(self, report: PreflightReport) -> None:
        for item in self.diagnosis_tree.get_children():
            self.diagnosis_tree.delete(item)
        labels = {
            CheckStatus.OK: "OK",
            CheckStatus.WARNING: "注意",
            CheckStatus.ERROR: "エラー",
        }
        for check in report.checks:
            self.diagnosis_tree.insert(
                "",
                "end",
                values=(labels[check.status], f"{check.label}: {check.message}"),
            )
        has_errors = any(check.status is CheckStatus.ERROR for check in report.checks)
        status_text = "利用可能" if report.succeeded else ("利用不可" if has_errors else "要確認")
        status_color = "#47D18C" if report.succeeded else ("#FF6B6B" if has_errors else "#FFD166")
        status_icon = (
            "available" if report.succeeded else ("unavailable" if has_errors else "warning")
        )
        self.connection_label.configure(
            text=status_text,
            foreground=status_color,
        )
        self.connection_icon_label.configure(
            text=ICON_GLYPHS[status_icon],
            foreground=status_color,
        )
        self._activity("環境診断が完了しました")
        ffmpeg_missing = any(
            check.code == "ffmpeg" and check.status is CheckStatus.ERROR
            for check in report.checks
        )
        if ffmpeg_missing and not self.ffmpeg_setup_prompted:
            self.ffmpeg_setup_prompted = True
            self.root.after_idle(self.show_ffmpeg_setup)

    def show_ffmpeg_setup(self) -> None:
        if self.smoke_mode:
            return
        if self.ffmpeg_setup_dialog is not None:
            self.ffmpeg_setup_dialog.lift()
            self.ffmpeg_setup_dialog.focus_force()
            return
        self.ffmpeg_setup_prompted = True
        dialog = tk.Toplevel(self.root)
        self.ffmpeg_setup_dialog = dialog
        dialog.title("FFmpegのセットアップ")
        dialog.geometry("680x410")
        dialog.minsize(620, 390)
        dialog.transient(self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="録画機能にFFmpeg 6.0以上が必要です",
            style="Heading.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "FFmpegがまだ導入されていない初回起動では、この画面から導入できます。"
                "インストールを選ぶまでダウンロードやファイル作成は行いません。"
            ),
            style="Body.TLabel",
            wraplength=620,
            justify="left",
        ).pack(anchor="w", pady=(8, 14))

        details = ttk.Frame(frame)
        details.pack(fill="x")
        for row, (label, value) in enumerate(
            (
                ("配布元", "Gyan FFmpeg Builds（FFmpeg公式サイト掲載）"),
                ("パッケージ", "release essentials ZIP（約110MB、64-bit）"),
                ("ライセンス", FFMPEG_LICENSE),
            )
        ):
            ttk.Label(details, text=label).grid(row=row, column=0, sticky="nw", pady=3)
            ttk.Label(details, text=value).grid(
                row=row, column=1, sticky="w", padx=(16, 0), pady=3
            )
        ttk.Button(
            details,
            text="配布元を開く",
            command=lambda: webbrowser.open(FFMPEG_PROVIDER_PAGE),
        ).grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 0))
        details.columnconfigure(1, weight=1)

        ttk.Label(frame, text="インストール先", style="Body.TLabel").pack(
            anchor="w", pady=(18, 5)
        )
        destination_row = ttk.Frame(frame)
        destination_row.pack(fill="x")
        destination_var = tk.StringVar(
            value=str(self.service.default_ffmpeg_install_directory())
        )
        destination_entry = ttk.Entry(destination_row, textvariable=destination_var)
        destination_entry.pack(side="left", fill="x", expand=True)

        def browse() -> None:
            current = Path(destination_var.get()).expanduser()
            selected = filedialog.askdirectory(
                parent=dialog,
                title="FFmpegのインストール先を選択",
                initialdir=current.parent if current.parent.exists() else Path.home(),
                mustexist=False,
            )
            if selected:
                destination_var.set(selected)

        browse_button = ttk.Button(destination_row, text="参照", command=browse)
        browse_button.pack(side="left", padx=(8, 0))

        progress_var = tk.StringVar(value="待機中")
        ttk.Label(frame, textvariable=progress_var, style="Muted.TLabel").pack(
            anchor="w", pady=(12, 0)
        )
        progress_queue: queue.Queue[FfmpegInstallProgress] = queue.Queue()

        def close_dialog() -> None:
            self.ffmpeg_setup_dialog = None
            dialog.destroy()

        def drain_progress() -> None:
            latest: FfmpegInstallProgress | None = None
            while True:
                try:
                    latest = progress_queue.get_nowait()
                except queue.Empty:
                    break
            if latest is not None:
                message = latest.stage
                if latest.downloaded_bytes:
                    downloaded = latest.downloaded_bytes / (1024 * 1024)
                    if latest.total_bytes:
                        total = latest.total_bytes / (1024 * 1024)
                        message = f"{message}: {downloaded:.1f} / {total:.1f} MB"
                    else:
                        message = f"{message}: {downloaded:.1f} MB"
                progress_var.set(message)
            if dialog.winfo_exists():
                dialog.after(100, drain_progress)

        buttons = ttk.Frame(frame)
        buttons.pack(side="bottom", fill="x", pady=(18, 0))
        cancel_button = ttk.Button(buttons, text="キャンセル", command=close_dialog)
        cancel_button.pack(side="right")

        def failed(error: BaseException) -> None:
            destination_entry.configure(state="normal")
            browse_button.configure(state="normal")
            install_button.configure(state="normal")
            cancel_button.configure(state="normal")
            progress_var.set("導入に失敗しました")
            self._activity(f"FFmpeg導入エラー: {error}")
            messagebox.showerror("FFmpegを導入できません", str(error), parent=dialog)

        def installed(result: FfmpegInstallResult) -> None:
            executable = result.executable
            self.setting_vars["recorder.ffmpeg_path"].set(str(executable))
            self._activity(f"FFmpegを導入しました: {executable}")
            close_dialog()
            self.run_diagnosis()

        def install() -> None:
            raw_destination = destination_var.get().strip()
            if not raw_destination:
                messagebox.showerror(
                    "インストール先を確認してください",
                    "インストール先を指定してください。",
                    parent=dialog,
                )
                return
            destination = Path(raw_destination).expanduser().resolve()
            confirmed = messagebox.askyesno(
                "FFmpeg導入の確認",
                (
                    f"次の配布物をダウンロードして導入します。\n\n"
                    f"配布元: {FFMPEG_DOWNLOAD_URL}\n"
                    f"ライセンス: {FFMPEG_LICENSE}\n"
                    f"インストール先: {destination}\n\n続行しますか？"
                ),
                parent=dialog,
            )
            if not confirmed:
                return
            destination_entry.configure(state="disabled")
            browse_button.configure(state="disabled")
            install_button.configure(state="disabled")
            cancel_button.configure(state="disabled")
            progress_var.set("導入を開始しています")
            self._run(
                lambda: self.service.install_ffmpeg(
                    destination,
                    progress=progress_queue.put,
                ),
                installed,
                failed,
            )

        install_button = ttk.Button(
            buttons,
            text="インストール",
            style="Primary.TButton",
            command=install,
        )
        install_button.pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", cancel_button.invoke)
        drain_progress()

    def start_recording(self) -> None:
        target = self.targets_by_label.get(self.target_var.get())
        self._set_record_controls(starting=True)
        self._run(
            lambda: self.service.start_recording(target),
            self._recording_started,
            self._recording_failed,
        )

    def _recording_started(self, snapshot: RecordingSnapshot) -> None:
        self._render_recording(snapshot)
        self._update_duel_write_controls()
        self._update_record_audio_status(active=True)
        self._activity(f"録画を開始しました: {snapshot.recording_id}")

    def _recording_failed(self, error: BaseException) -> None:
        self._set_record_controls(starting=False)
        self._set_record_status("failed")
        self._show_error(error)

    def stop_recording(self) -> None:
        self.stop_button.configure(state="disabled")
        self._set_record_status("stopping")
        self._run(self.service.stop_recording, self._recording_stopped)

    def _recording_stopped(self, snapshot: RecordingSnapshot) -> None:
        self._render_recording(snapshot)
        self._update_duel_write_controls()
        self._update_record_audio_status(active=False)
        self._activity(f"録画を停止しました: {snapshot.output_path}")
        if snapshot.state is RecordingState.COMPLETED and snapshot.recording_id:
            self.refresh_history()
            self._open_quick_duel_editor(snapshot.recording_id)

    def toggle_watch(self) -> None:
        if self.service.watch_active:
            self.watch_button.configure(state="disabled")
            self._run(
                self.service.stop_watch,
                lambda _value: self._watch_stopped(),
                self._watch_failed,
            )
        else:
            self.start_button.configure(state="disabled")
            self.watch_button.configure(state="disabled")
            self._run(
                lambda: self.service.start_watch(self.watch_events.put),
                lambda _value: self._watch_started(),
                self._watch_failed,
            )

    def _watch_started(self) -> None:
        self.watch_button.configure(text="自動監視停止", state="normal")
        self.automatic_recording_confirmed = False
        self._set_record_status("watch_waiting")
        self._update_record_audio_status(active=False)
        self._update_duel_write_controls()

    def _update_record_audio_status(self, *, active: bool) -> None:
        try:
            audio_input = self.service.load_config().config.audio_input
        except Exception:
            self.record_audio_status_var.set("音声: 設定を確認できません")
            return
        if not audio_input:
            self.record_audio_status_var.set("音声: 無効（映像のみ）")
        elif active:
            self.record_audio_status_var.set(f"音声: 入力中 - {audio_input}")
        else:
            self.record_audio_status_var.set(f"音声: 設定済み - {audio_input}")

    def _watch_stopped(self) -> None:
        self.watch_button.configure(text="自動監視開始", state="normal")
        self.start_button.configure(state="normal")
        self.automatic_recording_confirmed = False
        self._set_record_status("idle")
        self._update_duel_write_controls()

    def _watch_failed(self, error: BaseException) -> None:
        self._watch_stopped()
        self._show_error(error)

    def refresh_history(self) -> None:
        if self.smoke_mode:
            return
        self._run(
            lambda: self.service.get_history_dashboard(query=self.history_query),
            self._history_loaded,
        )

    def refresh_active_seasons(self) -> None:
        if self.smoke_mode:
            return
        self._run(self.service.active_season_summaries, self._active_seasons_loaded)

    def _active_seasons_loaded(
        self, summaries: tuple[ActiveSeasonSummary, ...]
    ) -> None:
        for child in self.active_season_host.winfo_children():
            child.destroy()
        self.active_season_buttons.clear()
        if not summaries:
            ttk.Label(
                self.active_season_host,
                text="現在開催中のシーズンはありません",
                style="Muted.TLabel",
            ).pack(side="left")
            return
        for summary in summaries:
            metric = summary.statistics.filtered
            button = ttk.Button(
                self.active_season_host,
                text=(
                    f"{summary.season.name}  {_format_win_rate(metric)}  "
                    f"{_format_statistics_detail(metric)}"
                ),
                command=lambda season=summary.season: self._open_season_report(season),
            )
            button.pack(side="left", padx=(0, 8))
            self.active_season_buttons.append(button)

    def _open_season_report(self, season: object) -> None:
        season_id = season.season_id
        self._run(
            lambda: (
                season,
                (
                    self.service.get_statistics_dashboard(
                        StatisticsFilter(season_id=season_id), granularity="day"
                    ),
                    self.service.get_statistics_dashboard(
                        StatisticsFilter(season_id=season_id), granularity="week"
                    ),
                    self.service.get_statistics_dashboard(
                        StatisticsFilter(season_id=season_id), granularity="month"
                    ),
                ),
            ),
            self._show_season_report_dialog,
        )

    def _update_duel_write_controls(self) -> None:
        snapshot = self.service.operation_snapshot()
        state = "normal" if snapshot.allows(OperationAction.WRITE_DUEL) else "disabled"
        self.manual_duel_button.configure(state=state)
        self.history_add_button.configure(state=state)
        self.history_incomplete_button.configure(state=state)
        self._history_selection_changed()

    def clear_history_filter(self) -> None:
        self.history_query = DuelManagementQuery(limit=200)
        self.active_saved_filter_id = None
        self.history_filter_count_var.set("")
        self.refresh_history()

    @staticmethod
    def _criteria_from_history_query(query: DuelManagementQuery) -> DuelFilterCriteria:
        return DuelFilterCriteria(
            season_id=query.season_id,
            own_deck_id=query.own_deck_id,
            opponent_deck_id=query.opponent_deck_id,
            tag_entry_ids=query.tag_entry_ids,
            coin_face=query.coin_face,
            coin_toss_outcome=query.coin_toss_outcome,
            entry_origin=query.entry_origin,
        )

    @staticmethod
    def _history_query_from_criteria(criteria: DuelFilterCriteria) -> DuelManagementQuery:
        return DuelManagementQuery(
            limit=200,
            season_id=criteria.season_id,
            own_deck_id=criteria.own_deck_id,
            opponent_deck_id=criteria.opponent_deck_id,
            tag_entry_ids=criteria.tag_entry_ids,
            coin_face=criteria.coin_face,
            coin_toss_outcome=criteria.coin_toss_outcome,
            entry_origin=criteria.entry_origin,
        )

    def open_history_filter(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("戦績管理フィルター")
        dialog.geometry("600x700")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        seasons = self.service.list_seasons(include_archived=True)
        decks = self.service.list_decks()
        tags = self.service.list_tags()
        saved_filters = self.service.list_saved_duel_filters()
        saved_by_label = {item.name: item for item in saved_filters}
        season_map = {"すべて": None, **{item.name: item.season_id for item in seasons}}
        visible_decks = tuple(
            item for item in decks if not item.hidden_from_history_statistics
        )
        deck_map = {
            "すべて": None,
            **{item.name: item.entry_id for item in visible_decks},
        }
        season_var = tk.StringVar(value="すべて")
        own_var = tk.StringVar(value="すべて")
        opponent_var = tk.StringVar(value="すべて")
        coin_face_var = tk.StringVar(value="すべて")
        coin_outcome_var = tk.StringVar(value="すべて")
        origin_var = tk.StringVar(value="すべて")
        saved_var = tk.StringVar(value="")
        ttk.Label(frame, text="保存済み").grid(row=0, column=0, sticky="w", pady=6)
        saved_combo = ttk.Combobox(
            frame,
            textvariable=saved_var,
            values=tuple(saved_by_label),
            state="readonly",
        )
        saved_combo.grid(row=0, column=1, sticky="ew", pady=6)
        for row, (label, variable, values) in enumerate(
            (
                ("シーズン", season_var, tuple(season_map)),
                ("自分デッキ", own_var, tuple(deck_map)),
                ("相手デッキ", opponent_var, tuple(deck_map)),
                ("コインの面", coin_face_var, ("すべて", "表", "裏", "未設定")),
                ("コイントス勝敗", coin_outcome_var, ("すべて", "勝ち", "負け", "未設定")),
                ("登録元", origin_var, ("すべて", "録画", "手動")),
            ),
            start=1,
        ):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
            ttk.Combobox(
                frame, textvariable=variable, values=values, state="readonly"
            ).grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Label(frame, text="タグ（複数可）").grid(
            row=7, column=0, sticky="nw", pady=6
        )
        tag_list = tk.Listbox(frame, selectmode="multiple", exportselection=False)
        tag_list.grid(row=7, column=1, sticky="nsew", pady=6)
        for item in tags:
            tag_list.insert("end", item.name)

        def current_query() -> DuelManagementQuery:
            selected_tag_ids = tuple(
                tags[index].entry_id for index in tag_list.curselection()
            )
            return DuelManagementQuery(
                limit=200,
                season_id=season_map[season_var.get()],
                own_deck_id=deck_map[own_var.get()],
                opponent_deck_id=deck_map[opponent_var.get()],
                tag_entry_ids=selected_tag_ids,
                coin_face={"すべて": None, "表": "heads", "裏": "tails", "未設定": "unknown"}[
                    coin_face_var.get()
                ],
                coin_toss_outcome={"すべて": None, "勝ち": "win", "負け": "loss", "未設定": "unknown"}[
                    coin_outcome_var.get()
                ],
                entry_origin={"すべて": None, "録画": "recording", "手動": "manual"}[
                    origin_var.get()
                ],
            )
        def apply_query(query: DuelManagementQuery, *, saved_id: str | None = None) -> None:
            self.history_query = query
            self.active_saved_filter_id = saved_id
            count = sum(
                value is not None
                for value in (
                    self.history_query.season_id,
                    self.history_query.own_deck_id,
                    self.history_query.opponent_deck_id,
                    self.history_query.coin_face,
                    self.history_query.coin_toss_outcome,
                    self.history_query.entry_origin,
                )
            ) + len(query.tag_entry_ids)
            label = str(count) if count else ""
            if saved_id is not None:
                saved = next(item for item in saved_filters if item.filter_id == saved_id)
                label = f"{saved.name} ({count})"
            self.history_filter_count_var.set(label)
            dialog.destroy()
            self.refresh_history()

        def apply() -> None:
            apply_query(current_query())

        def load_saved(_event: object | None = None) -> None:
            selected = saved_by_label.get(saved_var.get())
            if selected is not None:
                apply_query(
                    self._history_query_from_criteria(selected.criteria),
                    saved_id=selected.filter_id,
                )

        def save_current() -> None:
            name = simpledialog.askstring(
                "フィルターを保存", "フィルター名", parent=dialog
            )
            if not name:
                return
            existing = saved_by_label.get(name)
            if existing is not None and not messagebox.askyesno(
                "フィルターを上書き",
                f"「{name}」を上書きしますか？",
                parent=dialog,
            ):
                return
            saved = self.service.save_duel_filter(
                name,
                self._criteria_from_history_query(current_query()),
                filter_id=existing.filter_id if existing is not None else None,
            )
            self.active_saved_filter_id = saved.filter_id
            dialog.destroy()
            self.history_filter_count_var.set(saved.name)
            self.refresh_history()

        def delete_saved() -> None:
            selected = saved_by_label.get(saved_var.get())
            if selected is None:
                return
            if messagebox.askyesno(
                "保存済みフィルターを削除",
                f"「{selected.name}」を削除しますか？",
                parent=dialog,
            ):
                self.service.delete_duel_filter(selected.filter_id)
                if self.active_saved_filter_id == selected.filter_id:
                    self.active_saved_filter_id = None
                dialog.destroy()

        ttk.Button(frame, text="適用", style="Primary.TButton", command=apply).grid(
            row=8, column=1, sticky="e", pady=(12, 0)
        )
        saved_actions = ttk.Frame(frame)
        saved_actions.grid(row=9, column=1, sticky="e", pady=(8, 0))
        ttk.Button(saved_actions, text="現在条件を保存", command=save_current).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(saved_actions, text="保存済みを削除", command=delete_saved).pack(
            side="left"
        )
        saved_combo.bind("<<ComboboxSelected>>", load_saved)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(7, weight=1)
        dialog.grab_set()

    def _history_loaded(self, dashboard: RecordingHistoryDashboard) -> None:
        views = dashboard.views
        self._set_incomplete_duel_count(dashboard.incomplete_duel_record_count)
        previous = self.history_tree.selection()
        previous_id = str(previous[0]) if previous else None
        self._clear_tree(self.history_tree)
        self.history_views_by_id = {view.row_id: view for view in views}
        for view in views:
            entry = view.entry
            started = view.occurred_at
            duration = (
                f"{entry.duration_seconds:.1f}秒"
                if entry is not None and entry.duration_seconds is not None
                else "-"
            )
            size = _format_bytes(entry.size_bytes) if entry is not None else "-"
            if entry is not None and entry.state == "failed":
                result, play_order, coin_face, coin_outcome, duel_type = "録画失敗", "-", "-", "-", "-"
            elif entry is not None and entry.state in {"starting", "recording"}:
                result = "開始中" if entry.state == "starting" else "録画中"
                play_order, coin_face, coin_outcome, duel_type = "-", "-", "-", "-"
            elif view.duel_record is None:
                result = play_order = coin_face = coin_outcome = duel_type = "未入力"
            else:
                result = duel_choice_label("result", view.result)
                play_order = duel_choice_label("play_order", view.play_order)
                coin_face = duel_choice_label("coin_face", view.coin_face)
                coin_outcome = duel_choice_label("coin_toss_outcome", view.coin_toss_outcome)
                duel_type = duel_choice_label("duel_type", view.duel_type)
            own_deck = view.own_deck or "未設定"
            self.history_tree.insert(
                "",
                "end",
                iid=view.row_id,
                values=(
                    started.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    own_deck,
                    result,
                    play_order,
                    coin_face,
                    coin_outcome,
                    duel_type,
                    duration,
                    size,
                    "手動" if view.entry_origin == "manual" else "録画",
                ),
            )
        if previous_id is not None and self.history_tree.exists(previous_id):
            self.history_tree.selection_set(previous_id)
            self.history_tree.focus(previous_id)
            self.history_tree.see(previous_id)
        self._history_selection_changed()
        self.root.after_idle(self._draw_history_color_lines)

    def _draw_history_color_lines(self) -> None:
        for widget in self.history_color_lines:
            widget.destroy()
        self.history_color_lines.clear()
        if not self.history_tree.winfo_exists():
            return
        for row_id, view in self.history_views_by_id.items():
            color = getattr(view, "own_deck_color", None)
            if not color:
                continue
            bounds = self.history_tree.bbox(row_id, "started")
            if not bounds:
                continue
            x, y, width, height = bounds
            line = tk.Frame(self.history_tree, background=color, borderwidth=0)
            line.place(x=x + width - 3, y=y + 5, width=3, height=max(1, height - 10))
            self.history_color_lines.append(line)

    def _history_selection_changed(self, _event: object | None = None) -> None:
        selection = self.history_tree.selection()
        view = self.history_views_by_id.get(str(selection[0])) if selection else None
        has_recording = view is not None and view.recording_id is not None
        editable = view is not None and (
            view.duel_record is not None
            or (view.entry is not None and view.entry.state == "completed")
        )
        media_state = "normal" if has_recording else "disabled"
        self.history_diagnostic_button.configure(state=media_state)
        self.history_timeline_button.configure(state=media_state)
        self.history_action_buttons["play"].configure(state=media_state)
        self.history_action_buttons["folder"].configure(state=media_state)
        self.history_action_buttons["edit"].configure(
            state="normal" if editable else "disabled"
        )
        self.history_action_buttons["delete"].configure(
            state="normal" if len(selection) == 1 and view is not None else "disabled"
        )
        missing_recording = False
        if has_recording and view is not None and view.entry is not None:
            missing_recording = not (
                self.service.paths.recordings / view.entry.output_path
            ).is_file()
        self.history_relink_button.configure(
            state="normal" if missing_recording else "disabled"
        )
        self.history_bulk_button.configure(
            state="normal"
            if selection
            and all(
                self.history_views_by_id[str(item)].duel_record is not None
                for item in selection
            )
            and self.service.duel_write_block_reason() is None
            else "disabled"
        )

    def play_selected_history(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        view = self.history_views_by_id[str(selection[0])]
        if view.recording_id is None:
            return
        recording_id = view.recording_id
        self._run(
            lambda: self.service.play_recording(recording_id),
            lambda reference: self._recording_opened("再生を開始しました", reference),
        )

    def reveal_selected_history(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        view = self.history_views_by_id[str(selection[0])]
        if view.recording_id is None:
            return
        recording_id = view.recording_id
        self._run(
            lambda: self.service.reveal_recording(recording_id),
            lambda reference: self._recording_opened("保存場所を開きました", reference),
        )

    def relink_selected_history(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        view = self.history_views_by_id[str(selection[0])]
        if view.recording_id is None:
            return
        candidate = filedialog.askopenfilename(
            title="再関連付けする録画ファイルを選択",
            initialdir=self.service.paths.recordings,
            filetypes=(("録画ファイル", "*.mkv *.mp4"), ("すべてのファイル", "*.*")),
            parent=self.root,
        )
        if not candidate:
            return

        def confirm(preview: object) -> None:
            if not messagebox.askyesno(
                "録画ファイルを再関連付け",
                "録画ファイルの参照先だけを更新します。動画は移動・変更しません。\n\n"
                f"録画ID: {preview.recording_id}\n"
                f"現在: {preview.previous_path}\n"
                f"候補: {preview.candidate_path}\n"
                f"サイズ: {_format_bytes(preview.size_bytes)}\n"
                f"SHA-256: {preview.sha256}\n\n"
                "事前バックアップを作成して続行しますか？",
                parent=self.root,
            ):
                return
            self._run(
                lambda: self.service.relink_recording(preview),
                lambda _result: (
                    self._activity(f"録画ファイルを再関連付けしました: {preview.recording_id}"),
                    self.refresh_history(),
                    self.refresh_data_protection(),
                ),
            )

        self._run(
            lambda: self.service.preview_recording_relink(
                view.recording_id, Path(candidate)
            ),
            confirm,
        )

    def open_duplicate_candidates(self) -> None:
        self._run(
            self.service.duplicate_duel_candidates,
            self._show_duplicate_candidates,
        )

    def _show_duplicate_candidates(self, candidates: tuple[object, ...]) -> None:
        if not candidates:
            messagebox.showinfo(
                "重複戦績候補",
                "比較対象となる重複候補はありません。データは変更していません。",
                parent=self.root,
            )
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("重複戦績候補を比較")
        dialog.geometry("940x560")
        dialog.minsize(780, 460)
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="候補を比較し、両方保持・個別編集・片方削除を選択できます",
            style="Heading.TLabel",
        ).pack(anchor="w", pady=(0, 10))
        ttk.Label(
            frame,
            text="候補提示だけではデータを変更しません。自動統合は行いません。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 10))
        tree = ttk.Treeview(
            frame,
            columns=("left", "right", "score", "reason"),
            show="headings",
            height=9,
        )
        for key, label, width in (
            ("left", "候補A", 250),
            ("right", "候補B", 250),
            ("score", "一致度", 80),
            ("reason", "根拠", 300),
        ):
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=key == "reason")
        tree.pack(fill="both", expand=True)
        candidate_by_row: dict[str, object] = {}

        def record_label(identifier: str) -> str:
            record = self.service.get_duel_record(identifier)
            if record is None:
                return identifier
            values = record.values
            deck = values.own_deck or "デッキ未設定"
            result = duel_choice_label("result", values.result)
            return f"{record.occurred_at.astimezone():%Y-%m-%d %H:%M:%S} / {deck} / {result}"

        for index, candidate in enumerate(candidates):
            row_id = f"candidate:{index}"
            candidate_by_row[row_id] = candidate
            tree.insert(
                "",
                "end",
                iid=row_id,
                values=(
                    record_label(candidate.left_duel_id),
                    record_label(candidate.right_duel_id),
                    candidate.score,
                    "、".join(candidate.reasons),
                ),
            )
        detail_var = tk.StringVar(value="候補を選択してください")
        ttk.Label(
            frame,
            textvariable=detail_var,
            style="Muted.TLabel",
            justify="left",
            wraplength=880,
        ).pack(
            anchor="w", fill="x", pady=(10, 8)
        )
        actions = ttk.Frame(frame)
        actions.pack(anchor="e")
        action_buttons: list[ttk.Button] = []

        def selected_candidate() -> object | None:
            selection = tree.selection()
            return candidate_by_row.get(str(selection[0])) if selection else None

        def select_side(side: str) -> str | None:
            candidate = selected_candidate()
            return getattr(candidate, f"{side}_duel_id", None) if candidate else None

        def edit_side(side: str) -> None:
            identifier = select_side(side)
            if identifier is not None:
                self._open_duel_editor(identifier)

        def delete_side(side: str) -> None:
            identifier = select_side(side)
            if identifier is None:
                return
            record = self.service.get_duel_record(identifier)
            if record is None:
                return
            deletes_recording = record.recording_id is not None
            warning = (
                "対戦記録に加えて録画ファイルも完全に削除します。"
                if deletes_recording
                else "録画を伴わない対戦記録を完全に削除します。"
            )
            if not messagebox.askyesno(
                "重複候補を削除",
                f"{warning}\n\n対象: {record_label(identifier)}\n\n"
                "管理データの事前バックアップを作成して続行しますか？",
                parent=dialog,
            ):
                return

            def operation() -> object:
                if record.recording_id is not None:
                    return self.service.delete_history(record.recording_id)
                return self.service.delete_duel_record(record.duel_id)

            def deleted(_result: object) -> None:
                dialog.destroy()
                self._activity("重複候補から選択した戦績を削除しました")
                self.refresh_history()
                self.refresh_data_protection()

            self._run(operation, deleted)

        def selection_changed(_event: object | None = None) -> None:
            candidate = selected_candidate()
            enabled = "normal" if candidate is not None else "disabled"
            for button in action_buttons:
                button.configure(state=enabled)
            if candidate is None:
                detail_var.set("候補を選択してください")
                return

            def detail(side: str, identifier: str) -> str:
                record = self.service.get_duel_record(identifier)
                if record is None:
                    return f"{side}: 取得できません ({identifier})"
                values = record.values
                recording = record.recording_id or "なし（手動戦績）"
                tags = "、".join(values.tags) or "なし"
                notes = values.notes.strip() or "なし"
                return (
                    f"{side}: {record.occurred_at.astimezone():%Y-%m-%d %H:%M:%S} / "
                    f"録画 {recording} / デッキ {values.own_deck or '未設定'} / "
                    f"勝敗 {duel_choice_label('result', values.result)} / "
                    f"先後 {duel_choice_label('play_order', values.play_order)}\n"
                    f"タグ: {tags} / メモ: {notes}"
                )

            detail_var.set(
                f"開始時刻差 {candidate.time_delta_seconds:.1f}秒 / "
                f"一致度 {candidate.score} / 根拠: {'、'.join(candidate.reasons)}\n"
                f"{detail('A', candidate.left_duel_id)}\n"
                f"{detail('B', candidate.right_duel_id)}"
            )

        for label, command in (
            ("Aを編集", lambda: edit_side("left")),
            ("Bを編集", lambda: edit_side("right")),
            ("Aを削除", lambda: delete_side("left")),
            ("Bを削除", lambda: delete_side("right")),
        ):
            button = ttk.Button(actions, text=label, command=command, state="disabled")
            button.pack(side="left", padx=(0, 8))
            action_buttons.append(button)
        ttk.Button(actions, text="両方保持して閉じる", command=dialog.destroy).pack(
            side="left"
        )
        tree.bind("<<TreeviewSelect>>", selection_changed)
        dialog.grab_set()

    def delete_selected_history(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        row_id = str(selection[0])
        view = self.history_views_by_id[row_id]
        row = self.history_tree.item(row_id, "values")
        display_name = row[0] if row else row_id
        manual = view.recording_id is None and view.duel_record is not None
        confirmation = (
            "次の戦績を完全に削除します。\n\n"
            f"開始日時: {display_name}\n\n"
            "対戦記録とタグ関連が削除されます。この操作は元に戻せません。"
            if manual
            else "次の録画と戦績を完全に削除します。\n\n"
            f"開始日時: {display_name}\n\n"
            "録画ファイル、対戦記録、タグ関連、タイムラインも削除されます。"
            "この操作は元に戻せません。"
        )
        if not messagebox.askyesno(
            "戦績を削除",
            confirmation,
            parent=self.root,
        ):
            return
        def operation() -> object:
            if manual:
                return self.service.delete_duel_record(view.duel_record.duel_id)
            return self.service.delete_history(view.recording_id)
        self._run(
            operation,
            lambda result: (
                self._activity(
                    "戦績を削除しました"
                    if manual
                    else f"録画と戦績を削除しました: {result.recording_id} / ファイル {len(result.deleted_files)}件"
                ),
                self.refresh_history(),
                self.refresh_active_seasons(),
            ),
        )

    def show_selected_history_diagnostic(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        view = self.history_views_by_id[str(selection[0])]
        if view.recording_id is None:
            return
        recording_id = view.recording_id
        self._run(
            lambda: self.service.get_history(recording_id),
            self._show_history_diagnostic,
        )

    def edit_selected_duel_record(self) -> None:
        selection = self.history_tree.selection()
        if selection:
            view = self.history_views_by_id[str(selection[0])]
            identifier = (
                view.duel_record.duel_id
                if view.duel_record is not None
                else view.recording_id
            )
            if identifier is not None:
                self._open_duel_editor(identifier)

    def show_selected_timeline(self) -> None:
        selection = self.history_tree.selection()
        if selection:
            view = self.history_views_by_id[str(selection[0])]
            if view.recording_id is not None:
                self._show_timeline(view.recording_id)

    def _show_timeline(self, recording_id: str) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("対戦タイムライン")
        dialog.geometry("920x620")
        dialog.minsize(760, 500)
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"録画ID: {recording_id}", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w", pady=(0, 12)
        )

        status_var = tk.StringVar(value="すべて")
        type_var = tk.StringVar(value="すべて")
        ttk.Label(frame, text="状態").grid(row=1, column=0, sticky="w")
        status_combo = ttk.Combobox(
            frame,
            textvariable=status_var,
            values=("すべて", "candidate", "confirmed", "rejected"),
            state="readonly",
            width=14,
        )
        status_combo.grid(row=1, column=1, sticky="w", padx=(6, 18))
        ttk.Label(frame, text="種別").grid(row=1, column=2, sticky="w")
        type_combo = ttk.Combobox(
            frame,
            textvariable=type_var,
            values=("すべて", "duel_start", "turn_change", "duel_result", "marker"),
            state="readonly",
            width=16,
        )
        type_combo.grid(row=1, column=3, sticky="w", padx=(6, 18))
        refresh_button = self._icon_button(
            frame, "refresh", "タイムラインを更新", lambda: None
        )
        refresh_button.grid(row=1, column=5, sticky="e")

        columns = ("time", "type", "status", "confidence", "detail", "source", "id")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=15)
        for key, label, width in (
            ("time", "時刻", 80),
            ("type", "種別", 125),
            ("status", "状態", 95),
            ("confidence", "信頼度", 75),
            ("detail", "内容", 180),
            ("source", "入力元", 90),
            ("id", "イベントID", 230),
        ):
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=key in {"detail", "id"})
        tree.grid(row=2, column=0, columnspan=6, sticky="nsew", pady=(10, 12))

        elapsed_var = tk.StringVar(value="0.0")
        label_var = tk.StringVar()
        ttk.Label(frame, text="時刻（秒）").grid(row=3, column=0, sticky="w")
        ttk.Entry(frame, textvariable=elapsed_var, width=12).grid(
            row=3, column=1, sticky="w", padx=(6, 18)
        )
        ttk.Label(frame, text="マーカー名").grid(row=3, column=2, sticky="w")
        ttk.Entry(frame, textvariable=label_var).grid(
            row=3, column=3, columnspan=2, sticky="ew", padx=(6, 18)
        )
        add_button = ttk.Button(frame, text="マーカー追加", style="Primary.TButton")
        add_button.grid(row=3, column=5, sticky="e")

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=6, sticky="e", pady=(12, 0))
        confirm_button = ttk.Button(actions, text="候補を確認", state="disabled")
        confirm_button.pack(side="left", padx=(0, 8))
        reject_button = ttk.Button(actions, text="候補を却下", state="disabled")
        reject_button.pack(side="left")

        events_by_id: dict[str, DuelEvent] = {}

        def loaded(events: tuple[DuelEvent, ...]) -> None:
            events_by_id.clear()
            for item in tree.get_children():
                tree.delete(item)
            for event in events:
                events_by_id[event.event_id] = event
                detail = event.label or event.outcome or event.actor or "-"
                tree.insert(
                    "",
                    "end",
                    iid=event.event_id,
                    values=(
                        _format_elapsed_ms(event.elapsed_ms),
                        event.event_type,
                        event.status,
                        f"{event.confidence:.2f}"
                        if event.confidence is not None
                        else "-",
                        detail,
                        event.source,
                        event.event_id,
                    ),
                )
            candidate_selected()

        def refresh() -> None:
            status = None if status_var.get() == "すべて" else status_var.get()
            event_type = None if type_var.get() == "すべて" else type_var.get()
            self._run(
                lambda: self.service.list_timeline(
                    recording_id, status=status, event_type=event_type
                ),
                loaded,
            )

        def add_marker() -> None:
            try:
                elapsed_ms = round(float(elapsed_var.get()) * 1000)
            except ValueError:
                self._show_error(ValueError("時刻は0以上の秒数で入力してください"))
                return
            if elapsed_ms < 0:
                self._show_error(ValueError("時刻は0以上の秒数で入力してください"))
                return
            self._run(
                lambda: self.service.add_timeline_event(
                    recording_id,
                    elapsed_ms=elapsed_ms,
                    event_type="marker",
                    label=label_var.get(),
                ),
                lambda _event: (label_var.set(""), refresh()),
            )

        def candidate_selected(_event: object | None = None) -> None:
            selection = tree.selection()
            event = events_by_id.get(str(selection[0])) if selection else None
            state = (
                "normal"
                if event is not None and event.status == "candidate"
                else "disabled"
            )
            confirm_button.configure(state=state)
            reject_button.configure(state=state)

        def transition(confirm: bool) -> None:
            selection = tree.selection()
            if not selection:
                return
            event_id = str(selection[0])

            def operation() -> DuelEvent:
                if confirm:
                    return self.service.confirm_timeline_event(event_id)
                return self.service.reject_timeline_event(event_id)

            self._run(operation, lambda _event: refresh())

        refresh_button.configure(command=refresh)
        add_button.configure(command=add_marker)
        confirm_button.configure(command=lambda: transition(True))
        reject_button.configure(command=lambda: transition(False))
        status_combo.bind("<<ComboboxSelected>>", lambda _event: refresh())
        type_combo.bind("<<ComboboxSelected>>", lambda _event: refresh())
        tree.bind("<<TreeviewSelect>>", candidate_selected)
        frame.columnconfigure(3, weight=1)
        frame.columnconfigure(4, weight=1)
        frame.rowconfigure(2, weight=1)
        refresh()

    def _open_manual_quick_duel_editor(self) -> None:
        reason = self.service.duel_write_block_reason()
        if reason is not None:
            messagebox.showinfo("戦績を追加できません", reason, parent=self.root)
            return
        self._run(
            lambda: self.service.get_duel_editor_data(),
            lambda data: self._show_quick_duel_editor(None, data),
        )

    def _open_manual_duel_editor(self) -> None:
        reason = self.service.duel_write_block_reason()
        if reason is not None:
            messagebox.showinfo("戦績を追加できません", reason, parent=self.root)
            return
        self._run(
            lambda: self.service.get_duel_editor_data(),
            lambda data: self._show_duel_editor(None, data, read_only_reason=None),
        )

    def _open_quick_duel_editor(self, identifier: str) -> None:
        self._run(
            lambda: self.service.get_duel_editor_data(identifier),
            lambda data: self._show_quick_duel_editor(identifier, data),
        )

    def _show_quick_duel_editor(
        self,
        identifier: str | None,
        data: DuelEditorData,
        *,
        on_saved: Callable[[], None] | None = None,
    ) -> None:
        if self.service.duel_write_block_reason() is not None:
            return
        record = data.record
        is_manual = identifier is None or (
            record is not None and record.entry_origin == "manual"
        )
        dialog = tk.Toplevel(self.root)
        dialog.title("戦績を簡易入力")
        dialog.geometry("560x500")
        dialog.minsize(520, 460)
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="戦績を簡易入力", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        reason_text = " / ".join(data.suggestion_reasons)
        ttk.Label(
            frame,
            text=reason_text or "候補値を確認して保存してください",
            style="Muted.TLabel",
            wraplength=480,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        values = data.values
        result_var = tk.StringVar(value=duel_choice_label("result", values.result))
        order_var = tk.StringVar(value=duel_choice_label("play_order", values.play_order))
        visible_decks = tuple(
            item
            for item in data.decks
            if not item.hidden_from_history_statistics and not item.opponent_only
        )
        deck_var = tk.StringVar(value=values.own_deck)
        seasons = {"未設定": None, **{item.name: item.season_id for item in data.seasons}}
        current_season = next(
            (name for name, season_id in seasons.items() if season_id == values.season_id),
            "未設定",
        )
        season_var = tk.StringVar(value=current_season)
        occurred_var = tk.StringVar(
            value=(
                record.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                if record is not None
                else datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            )
        )
        fields = [
            ("勝敗", result_var, duel_choice_labels("result")),
            ("先後", order_var, duel_choice_labels("play_order")),
            ("自分デッキ", deck_var, tuple(item.name for item in visible_decks)),
            ("シーズン", season_var, tuple(seasons)),
        ]
        row = 2
        if is_manual:
            ttk.Label(frame, text="対戦日時").grid(row=row, column=0, sticky="w", pady=7)
            ttk.Entry(frame, textvariable=occurred_var).grid(
                row=row, column=1, sticky="ew", pady=7
            )
            row += 1
        for label, variable, choices in fields:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=7)
            ttk.Combobox(
                frame,
                textvariable=variable,
                values=choices,
                state="normal" if label == "自分デッキ" else "readonly",
            ).grid(row=row, column=1, sticky="ew", pady=7)
            row += 1
        frame.columnconfigure(1, weight=1)

        def save(status: str) -> None:
            try:
                occurred = datetime.strptime(
                    occurred_var.get().strip(), "%Y-%m-%d %H:%M:%S"
                ).astimezone()
            except ValueError:
                self._show_error(ValueError("対戦日時はYYYY-MM-DD HH:MM:SSで入力してください"))
                return
            updated = DuelRecordValues(
                **{
                    **values.__dict__,
                    "status": status,
                    "result": duel_choice_value("result", result_var.get()),
                    "play_order": duel_choice_value("play_order", order_var.get()),
                    "own_deck": deck_var.get(),
                    "season_id": seasons[season_var.get()],
                }
            )
            selected_season = next(
                (item for item in data.seasons if item.season_id == updated.season_id),
                None,
            )
            if selected_season is not None and not selected_season.contains(
                occurred.date()
            ):
                if not messagebox.askyesno(
                    "シーズン期間外",
                    f"対戦日は{occurred.date()}で、選択したシーズン期間外です。保存しますか？",
                    parent=dialog,
                ):
                    return
            def operation() -> object:
                if is_manual:
                    return self.service.create_manual_duel_record(updated, occurred_at=occurred)
                if record is not None and record.entry_origin == "manual":
                    return self.service.update_duel_record(
                        record.duel_id,
                        updated,
                        expected_revision=record.revision,
                        occurred_at=occurred,
                    )
                recording_identifier = (
                    record.recording_id if record is not None else identifier
                )
                assert recording_identifier is not None
                return self.service.save_duel_record(
                    recording_identifier,
                    updated,
                    expected_revision=record.revision if record is not None else 0,
                )
            self._run(
                operation,
                lambda _saved: self._quick_duel_saved(dialog, on_saved),
            )

        actions = ttk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=2, sticky="e", pady=(18, 0))
        ttk.Button(actions, text="後回し", command=lambda: save("draft")).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(
            actions,
            text="詳細入力",
            command=lambda: (
                dialog.destroy(),
                self._show_duel_editor(identifier, data, read_only_reason=None),
            ),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            actions,
            text="保存",
            style="Primary.TButton",
            command=lambda: save("confirmed"),
        ).pack(side="left")
        dialog.bind("<Control-s>", lambda _event: save("confirmed"))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _quick_duel_saved(
        self, dialog: tk.Toplevel, on_saved: Callable[[], None] | None
    ) -> None:
        dialog.destroy()
        self.refresh_history()
        self.refresh_active_seasons()
        if on_saved is not None:
            on_saved()

    def _open_incomplete_duel_queue(self) -> None:
        reason = self.service.duel_write_block_reason()
        if reason is not None:
            messagebox.showinfo("未完了戦績を処理できません", reason, parent=self.root)
            return
        self._run(self.service.list_incomplete_duels, self._show_incomplete_duel_queue)

    def _show_incomplete_duel_queue(self, items: tuple[object, ...]) -> None:
        if not items:
            messagebox.showinfo("未完了戦績", "未完了の戦績はありません", parent=self.root)
            return
        state = {"index": 0}
        dialog = tk.Toplevel(self.root)
        dialog.title("未完了戦績を連続処理")
        dialog.geometry("520x240")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        progress = tk.StringVar()
        detail = tk.StringVar()
        ttk.Label(frame, textvariable=progress, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(frame, textvariable=detail, style="Muted.TLabel").pack(
            anchor="w", pady=(8, 20)
        )
        actions = ttk.Frame(frame)
        actions.pack(anchor="e")

        def render() -> None:
            index = state["index"]
            item = items[index]
            progress.set(f"未完了 {index + 1} / {len(items)}")
            detail.set(
                f"{item.occurred_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')} / "
                f"{'未入力' if item.kind == 'missing' else '編集中'}"
            )

        def move(delta: int) -> None:
            state["index"] = (state["index"] + delta) % len(items)
            render()

        def edit() -> None:
            item = items[state["index"]]
            dialog.destroy()
            self._run(
                lambda: self.service.get_duel_editor_data(item.identifier),
                lambda data: self._show_quick_duel_editor(
                    item.identifier,
                    data,
                    on_saved=self._open_incomplete_duel_queue,
                ),
            )

        ttk.Button(actions, text="前へ", command=lambda: move(-1)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="後回し", command=lambda: move(1)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="入力する", style="Primary.TButton", command=edit).pack(side="left")
        render()
        dialog.grab_set()

    def _open_bulk_duel_editor(self) -> None:
        selection = tuple(str(item) for item in self.history_tree.selection())
        records = tuple(
            self.history_views_by_id[item].duel_record
            for item in selection
            if self.history_views_by_id[item].duel_record is not None
        )
        if not records:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("戦績を一括編集")
        dialog.geometry("560x460")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"選択した {len(records)} 件を一括編集", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        seasons = self.service.list_seasons(include_archived=True)
        decks = tuple(
            item
            for item in self.service.list_decks()
            if not item.hidden_from_history_statistics and not item.opponent_only
        )
        season_values = {"変更しない": "", "未設定": "none", **{item.name: str(item.season_id) for item in seasons}}
        deck_values = ("変更しない", *(item.name for item in decks))
        type_values = ("変更しない", *duel_choice_labels("duel_type"))
        season_var = tk.StringVar(value="変更しない")
        deck_var = tk.StringVar(value="変更しない")
        type_var = tk.StringVar(value="変更しない")
        add_tags_var = tk.StringVar()
        remove_tags_var = tk.StringVar()
        rows = (
            ("シーズン", season_var, tuple(season_values)),
            ("自分デッキ", deck_var, deck_values),
            ("対戦種別", type_var, type_values),
        )
        for row, (label, variable, choices) in enumerate(rows, start=1):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=7)
            ttk.Combobox(frame, textvariable=variable, values=choices, state="readonly").grid(
                row=row, column=1, sticky="ew", pady=7
            )
        for row, (label, variable) in enumerate(
            (("追加タグ（カンマ区切り）", add_tags_var), ("削除タグ（カンマ区切り）", remove_tags_var)),
            start=4,
        ):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=7)
            ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=7)
        frame.columnconfigure(1, weight=1)

        def apply() -> None:
            if not messagebox.askyesno(
                "一括更新を確認",
                f"{len(records)}件へ変更を適用しますか？",
                parent=dialog,
            ):
                return
            season_value = season_values[season_var.get()]
            update = BulkDuelUpdate(
                season_id=None if season_value in {"", "none"} else int(season_value),
                change_season=season_value != "",
                own_deck=None if deck_var.get() == "変更しない" else deck_var.get(),
                duel_type=None
                if type_var.get() == "変更しない"
                else duel_choice_value("duel_type", type_var.get()),
                add_tags=tuple(item.strip() for item in add_tags_var.get().split(",") if item.strip()),
                remove_tags=tuple(item.strip() for item in remove_tags_var.get().split(",") if item.strip()),
            )
            self._run(
                lambda: self.service.bulk_update_duel_records(
                    tuple(item.duel_id for item in records), update
                ),
                lambda saved: (
                    self._activity(f"戦績を一括更新しました: {len(saved)}件"),
                    dialog.destroy(),
                    self.refresh_history(),
                    self.refresh_active_seasons(),
                ),
            )

        ttk.Button(frame, text="適用", style="Primary.TButton", command=apply).grid(
            row=6, column=1, sticky="e", pady=(18, 0)
        )
        dialog.grab_set()

    def _open_duel_editor(self, identifier: str) -> None:
        read_only_reason = self.service.duel_write_block_reason()
        self._run(
            lambda: self.service.get_duel_editor_data(identifier),
            lambda data: self._show_duel_editor(
                identifier, data, read_only_reason=read_only_reason
            ),
        )

    def _show_duel_editor(
        self,
        identifier: str | None,
        data: DuelEditorData,
        *,
        read_only_reason: str | None,
    ) -> None:
        values = data.values
        revision = data.record.revision if data.record is not None else 0
        is_new_manual = identifier is None
        is_manual = is_new_manual or (
            data.record is not None and data.record.entry_origin == "manual"
        )
        recording_id = (
            data.record.recording_id
            if data.record is not None
            else identifier
        )
        dialog = tk.Toplevel(self.root)
        dialog.title("戦績を追加" if is_new_manual else "対戦記録")
        dialog.geometry("720x740" if is_manual else "720x700")
        dialog.minsize(620, 620)
        dialog.transient(self.root)
        form = ttk.Frame(dialog, padding=18)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="対戦内容", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        fields = (
            ("状態", "status", "confirmed" if is_new_manual else values.status),
            ("勝敗", "result", values.result),
            ("先後", "play_order", values.play_order),
            ("コインの面", "coin_face", values.coin_face),
            ("コイントス勝敗", "coin_toss_outcome", values.coin_toss_outcome),
            ("対戦種別", "duel_type", values.duel_type),
        )
        variables: dict[str, tk.StringVar] = {}
        editable_widgets: list[tk.Widget] = []
        row = 1
        occurred_at_var = tk.StringVar(
            value=(
                data.record.occurred_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                if data.record is not None
                else datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            )
        )
        if is_manual:
            ttk.Label(form, text="対戦日時").grid(row=row, column=0, sticky="w", pady=5)
            occurred_entry = ttk.Entry(form, textvariable=occurred_at_var)
            occurred_entry.grid(row=row, column=1, sticky="ew", pady=5)
            editable_widgets.append(occurred_entry)
            row += 1
        for label, key, current in fields:
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=5)
            variable = tk.StringVar(value=duel_choice_label(key, current))
            variables[key] = variable
            combo = ttk.Combobox(
                form,
                textvariable=variable,
                values=duel_choice_labels(key),
                state="readonly",
            )
            combo.grid(row=row, column=1, sticky="ew", pady=5)
            editable_widgets.append(combo)
            row += 1
        visible_decks = tuple(
            entry for entry in data.decks if not entry.hidden_from_history_statistics
        )
        own_deck_names = tuple(
            entry.name for entry in visible_decks if not entry.opponent_only
        )
        opponent_deck_names = tuple(entry.name for entry in visible_decks)
        for label, key, current, choices in (
            ("自分デッキ", "own_deck", values.own_deck, own_deck_names),
            ("相手デッキ", "opponent_deck", values.opponent_deck, opponent_deck_names),
        ):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=5)
            variable = tk.StringVar(value=current)
            variables[key] = variable
            combo = ttk.Combobox(
                form,
                textvariable=variable,
                values=tuple(dict.fromkeys((*choices, current)))
                if current
                else choices,
                state="normal",
            )
            combo.grid(row=row, column=1, sticky="ew", pady=5)
            editable_widgets.append(combo)
            row += 1
        season_by_label = {
            "未設定": None,
            **{item.name: item.season_id for item in data.seasons},
        }
        current_season = next(
            (item.name for item in data.seasons if item.season_id == values.season_id),
            "未設定",
        )
        ttk.Label(form, text="シーズン").grid(row=row, column=0, sticky="w", pady=5)
        season_var = tk.StringVar(value=current_season)
        season_combo = ttk.Combobox(
            form,
            textvariable=season_var,
            values=tuple(season_by_label),
            state="readonly",
        )
        season_combo.grid(row=row, column=1, sticky="ew", pady=5)
        editable_widgets.append(season_combo)
        row += 1
        ttk.Label(form, text="タグ").grid(row=row, column=0, sticky="nw", pady=5)
        tag_panel = ttk.Frame(form)
        tag_panel.grid(row=row, column=1, sticky="nsew", pady=5)
        tag_var = tk.StringVar()
        tag_combo = ttk.Combobox(
            tag_panel,
            textvariable=tag_var,
            values=tuple(entry.name for entry in data.tags),
            state="normal",
        )
        tag_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        tag_list = tk.Listbox(tag_panel, height=5, exportselection=False)
        tag_list.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        tag_info_var = tk.StringVar(value="")
        ttk.Label(tag_panel, textvariable=tag_info_var, style="Muted.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )
        tag_entries = {entry.name.casefold(): entry for entry in data.tags}

        def style_tag(index: int, tag: str) -> None:
            entry = tag_entries.get(tag.casefold())
            if entry is None or not entry.color:
                return
            tag_list.itemconfig(
                index,
                background=entry.color,
                foreground=_contrast_text_color(entry.color),
            )

        for tag in values.tags:
            tag_list.insert("end", tag)
            style_tag(tag_list.size() - 1, tag)

        def add_tag() -> None:
            tag = tag_var.get().strip()
            if not tag:
                return
            current = tuple(
                str(tag_list.get(index)) for index in range(tag_list.size())
            )
            if tag.casefold() not in {item.casefold() for item in current}:
                tag_list.insert("end", tag)
                style_tag(tag_list.size() - 1, tag)
            tag_var.set("")

        def remove_tag() -> None:
            selection = tag_list.curselection()
            if selection:
                tag_list.delete(selection[0])

        add_tag_button = ttk.Button(tag_panel, text="追加", command=add_tag)
        add_tag_button.grid(
            row=0, column=1, padx=(0, 8)
        )
        remove_tag_button = ttk.Button(tag_panel, text="選択を外す", command=remove_tag)
        remove_tag_button.grid(
            row=0, column=2
        )
        editable_widgets.extend((tag_combo, tag_list, add_tag_button, remove_tag_button))

        def show_tag_description(_event: object | None = None) -> None:
            entry = tag_entries.get(tag_var.get().strip().casefold())
            tag_info_var.set(entry.description if entry is not None else "")

        tag_combo.bind("<<ComboboxSelected>>", show_tag_description)
        tag_panel.columnconfigure(0, weight=1)
        tag_panel.rowconfigure(1, weight=1)
        row += 1
        ttk.Label(form, text="メモ").grid(row=row, column=0, sticky="nw", pady=5)
        notes = tk.Text(form, height=10, wrap="word")
        notes.insert("1.0", values.notes)
        notes.grid(row=row, column=1, sticky="nsew", pady=5)
        editable_widgets.append(notes)
        form.columnconfigure(1, weight=1)
        form.rowconfigure(row, weight=1)

        def save() -> None:
            add_tag()
            tags = tuple(str(tag_list.get(index)) for index in range(tag_list.size()))
            updated = DuelRecordValues(
                status=duel_choice_value("status", variables["status"].get()),
                result=duel_choice_value("result", variables["result"].get()),
                play_order=duel_choice_value(
                    "play_order", variables["play_order"].get()
                ),
                coin_face=duel_choice_value("coin_face", variables["coin_face"].get()),
                coin_toss_outcome=duel_choice_value(
                    "coin_toss_outcome", variables["coin_toss_outcome"].get()
                ),
                own_deck=variables["own_deck"].get(),
                opponent_deck=variables["opponent_deck"].get(),
                duel_type=duel_choice_value("duel_type", variables["duel_type"].get()),
                tags=tags,
                notes=notes.get("1.0", "end-1c"),
                season_id=season_by_label[season_var.get()],
            )
            selected_season = next(
                (item for item in data.seasons if item.season_id == updated.season_id),
                None,
            )
            try:
                occurred_at = datetime.strptime(
                    occurred_at_var.get().strip(), "%Y-%m-%d %H:%M:%S"
                ).astimezone()
            except ValueError:
                self._show_error(ValueError("対戦日時はYYYY-MM-DD HH:MM:SSで入力してください"))
                return
            matching_view = next(
                (
                    view
                    for view in self.history_views_by_id.values()
                    if view.recording_id == recording_id
                ),
                None,
            )
            occurred = (
                occurred_at.date()
                if is_manual
                else data.record.occurred_at.astimezone().date()
                if data.record is not None
                else matching_view.occurred_at.astimezone().date()
                if matching_view is not None
                else date.today()
            )
            if selected_season is not None and not selected_season.contains(occurred):
                if not messagebox.askyesno(
                    "シーズン期間外",
                    f"録画日は{occurred}で、選択したシーズン期間外です。保存しますか？",
                    parent=dialog,
                ):
                    return
            def operation() -> object:
                if is_new_manual:
                    return self.service.create_manual_duel_record(
                        updated, occurred_at=occurred_at
                    )
                if is_manual:
                    assert data.record is not None
                    return self.service.update_duel_record(
                        data.record.duel_id,
                        updated,
                        expected_revision=revision,
                        occurred_at=occurred_at,
                    )
                assert recording_id is not None
                return self.service.save_duel_record(
                    recording_id, updated, expected_revision=revision
                )
            self._run(
                operation,
                lambda saved: (
                    self._activity(
                        f"対戦記録を保存しました: revision {saved.revision}"
                    ),
                    dialog.destroy(),
                    self.refresh_history(),
                    self.refresh_active_seasons(),
                ),
            )

        buttons = ttk.Frame(form)
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(14, 0))
        if recording_id is not None:
            ttk.Button(
                buttons,
                text="タイムライン",
                command=lambda: self._show_timeline(recording_id),
            ).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="キャンセル", command=dialog.destroy).pack(
            side="left", padx=(0, 8)
        )
        save_button = self._icon_button(
            buttons, "save", "対戦記録を保存", save, style="Primary.TButton"
        )
        save_button.pack(side="left")
        if read_only_reason is not None:
            for widget in editable_widgets:
                try:
                    widget.configure(state="disabled")
                except tk.TclError:
                    pass
            save_button.configure(state="disabled")
            ttk.Label(
                form,
                text=read_only_reason,
                style="Muted.TLabel",
            ).grid(row=row + 2, column=0, columnspan=2, sticky="e", pady=(8, 0))
        dialog.grab_set()

    def _show_history_diagnostic(self, entry: object) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("録画診断")
        dialog.geometry("760x480")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text=f"録画ID: {entry.recording_id}", style="Heading.TLabel"
        ).pack(anchor="w", pady=(0, 10))
        details = (
            f"状態: {entry.state}\n"
            f"終了コード: {entry.returncode if entry.returncode is not None else '-'}\n"
            f"失敗分類: {entry.failure_code or '-'}\n"
            f"検出理由: {entry.detection_reason or '-'}\n"
            f"音声入力: {entry.audio_input or 'なし'}\n"
            f"音声状態: {entry.audio_state}\n"
            f"音声警告: {entry.audio_warning or '-'}\n"
            f"エラー: {entry.error or '-'}\n\n"
            "FFmpeg診断出力:\n"
            + ("\n".join(entry.diagnostics) if entry.diagnostics else "-")
        )
        text = tk.Text(frame, wrap="word", font=("Consolas", 10), padx=10, pady=10)
        text.insert("1.0", details)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        ttk.Button(frame, text="閉じる", command=dialog.destroy).pack(
            anchor="e", pady=(10, 0)
        )
        dialog.grab_set()

    def _recording_opened(self, action: str, reference: RecordingReference) -> None:
        self._activity(f"{action}: {reference.recording_id}")
        for warning in reference.warnings:
            self._activity(f"注意: {warning}")

    def check_history(self) -> None:
        self._run(
            self.service.check_history,
            lambda issues: self._activity(f"履歴の不整合: {len(issues)}件"),
        )

    def refresh_seasons(self) -> None:
        if not self.smoke_mode:
            self._run(
                lambda: self.service.list_seasons(include_archived=True),
                self._seasons_loaded,
            )

    def _seasons_loaded(self, seasons: tuple[object, ...]) -> None:
        self._clear_tree(self.season_tree)
        self.seasons_by_id = {str(item.season_id): item for item in seasons}
        self.season_color_images.clear()
        for season in seasons:
            color = {"ranked": "#006A6A", "event": "#9A6700", "custom": "#6750A4"}[
                season.season_type
            ]
            image = self._vertical_color_line(color)
            self.season_color_images[str(season.season_id)] = image
            self.season_tree.insert(
                "",
                "end",
                iid=str(season.season_id),
                image=image,
                values=(
                    season.name,
                    {"ranked": "ランク", "event": "イベント", "custom": "カスタム"}[
                        season.season_type
                    ],
                    f"{season.start_date} - {season.end_date}",
                    "アーカイブ" if season.is_archived else "利用中",
                ),
            )

    def _season_selection_changed(self) -> None:
        selected = self.season_tree.selection()
        state = "normal" if selected else "disabled"
        self.season_update_button.configure(state=state)
        self.season_delete_button.configure(state=state)
        self.season_report_button.configure(state=state)
        if not selected:
            return
        season = self.seasons_by_id.get(str(selected[0]))
        if season is None:
            return
        self.season_name_var.set(season.name)
        self.season_type_var.set(
            {"ranked": "ランク", "event": "イベント", "custom": "カスタム"}[
                season.season_type
            ]
        )
        self.season_start_var.set(str(season.start_date))
        self.season_end_var.set(str(season.end_date))
        self.season_description_var.set(season.description)

    def _season_report_text(
        self,
        dashboards: tuple[
            StatisticsDashboard,
            StatisticsDashboard,
            StatisticsDashboard,
        ],
    ) -> str:
        day, week, month = dashboards
        metric = day.filtered
        orders = {item.key: item.metric for item in day.by_play_order}
        first = orders.get("first", StatisticsMetric(0, 0, 0, 0))
        second = orders.get("second", StatisticsMetric(0, 0, 0, 0))
        decks = " / ".join(
            f"{item.label} {item.metric.matches}戦 {_format_win_rate(item.metric)}"
            for item in day.by_deck[:5]
        ) or "対戦なし"
        return (
            f"{_format_win_rate(metric)}  {_format_statistics_detail(metric)}\n"
            f"先攻時 {_format_win_rate(first)} / 後攻時 {_format_win_rate(second)}\n"
            f"デッキ別: {decks}\n"
            f"推移: 日別{len(day.trend)}区間 / 週別{len(week.trend)}区間 / "
            f"月別{len(month.trend)}区間"
        )

    def add_season(self) -> None:
        self._save_season(None)

    def update_selected_season(self) -> None:
        selected = self.season_tree.selection()
        if selected:
            self._save_season(int(selected[0]))

    def _save_season(self, season_id: int | None) -> None:
        try:
            type_value = {
                "ランク": "ranked",
                "イベント": "event",
                "カスタム": "custom",
            }[self.season_type_var.get()]
            values = dict(
                name=self.season_name_var.get(),
                season_type=type_value,
                duel_type={"ranked": "ranked", "event": "event", "custom": "other"}[
                    type_value
                ],
                start_date=date.fromisoformat(self.season_start_var.get()),
                end_date=date.fromisoformat(self.season_end_var.get()),
                description=self.season_description_var.get(),
                report_notes=(
                    self.seasons_by_id[str(season_id)].report_notes
                    if season_id is not None
                    else ""
                ),
            )
        except (KeyError, ValueError) as exc:
            self._show_error(ValueError(f"シーズンの種別または日付を確認してください: {exc}"))
            return
        operation = (
            (lambda: self.service.add_season(**values))
            if season_id is None
            else (lambda: self.service.update_season(season_id, **values))
        )
        self._run(
            operation,
            lambda _saved: (
                self._activity("シーズンを保存しました"),
                self.refresh_seasons(),
            ),
        )

    def open_selected_season_report(self) -> None:
        selected = self.season_tree.selection()
        if not selected or self.smoke_mode:
            return
        season = self.seasons_by_id.get(str(selected[0]))
        if season is None:
            return
        season_id = season.season_id
        self._run(
            lambda: (
                season,
                (
                    self.service.get_statistics_dashboard(
                        StatisticsFilter(season_id=season_id), granularity="day"
                    ),
                    self.service.get_statistics_dashboard(
                        StatisticsFilter(season_id=season_id), granularity="week"
                    ),
                    self.service.get_statistics_dashboard(
                        StatisticsFilter(season_id=season_id), granularity="month"
                    ),
                ),
            ),
            self._show_season_report_dialog,
        )

    def _show_season_report_dialog(self, payload: tuple[object, object]) -> None:
        season, dashboards = payload
        dialog = tk.Toplevel(self.root)
        dialog.title(f"シーズンレポート - {season.name}")
        dialog.geometry("720x610")
        dialog.transient(self.root)
        dialog.configure(background=self.COLORS["canvas"])
        frame = ttk.Frame(dialog, style="App.TFrame", padding=22)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=season.name, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text=f"{season.start_date} - {season.end_date} / "
            f"{ {'ranked': 'ランク', 'event': 'イベント', 'custom': 'カスタム'}[season.season_type] }",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 12))
        metrics = self._surface(frame, padding=(16, 14))
        metrics.pack(fill="x", pady=(0, 12))
        ttk.Label(metrics, text="ライブ集計レポート", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            metrics,
            text=self._season_report_text(dashboards),
            style="Body.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(8, 0))
        ttk.Label(frame, text="レポートメモ", style="Heading.TLabel").pack(anchor="w")
        notes = tk.Text(
            frame,
            height=12,
            wrap="word",
            borderwidth=1,
            relief="solid",
            background=self.COLORS["surface"],
            foreground=self.COLORS["text"],
            font=("Segoe UI", 10),
            padx=10,
            pady=10,
        )
        notes.insert("1.0", season.report_notes)
        notes.pack(fill="both", expand=True, pady=(6, 12))

        def save_notes() -> None:
            self._run(
                lambda: self.service.update_season(
                    season.season_id,
                    name=season.name,
                    season_type=season.season_type,
                    duel_type=season.duel_type,
                    start_date=season.start_date,
                    end_date=season.end_date,
                    description=season.description,
                    report_notes=notes.get("1.0", "end-1c"),
                ),
                lambda _saved: (dialog.destroy(), self.refresh_seasons()),
            )

        self._icon_button(
            frame, "save", "レポートメモを保存", save_notes, style="Primary.TButton"
        ).pack(anchor="e")
        dialog.grab_set()

    def delete_selected_season(self) -> None:
        selected = self.season_tree.selection()
        if selected and messagebox.askyesno(
            "シーズン", "未参照なら削除、参照中ならアーカイブします。", parent=self.root
        ):
            self._run(
                lambda: self.service.delete_season(int(selected[0])),
                lambda _item: self.refresh_seasons(),
            )

    def export_managed_data(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root,
            title="管理データを書き出す",
            defaultextension=".json",
            filetypes=(("JSON", "*.json"),),
            initialdir=str(self.service.paths.exports),
            initialfile=f"mdrl-managed-data-{date.today()}.json",
        )
        if destination:
            self._run(
                lambda: self.service.export_managed_data(Path(destination)),
                lambda result: self._activity(
                    f"管理データを書き出しました: {result.path} ({result.row_count}行)"
                ),
            )

    def import_managed_data(self) -> None:
        source = filedialog.askopenfilename(
            parent=self.root,
            title="管理データを読み込む",
            filetypes=(("JSON", "*.json"),),
            initialdir=str(self.service.paths.exports),
        )
        if not source:
            return
        if not messagebox.askyesno(
            "管理データの読み込み",
            "現在の履歴・デッキ・タグ・シーズンを置き換えます。\n"
            "操作前にSQLiteバックアップを自動作成します。続行しますか？",
            parent=self.root,
        ):
            return
        self._run(
            lambda: self.service.import_managed_data(Path(source)),
            lambda result: (
                self._activity(
                    f"管理データを読み込みました: {result.row_count}行 / "
                    f"バックアップ: {result.backup_path}"
                ),
                self.refresh_all(),
            ),
        )

    def reset_managed_data(self, scope: str, label: str) -> None:
        detail = (
            "録画ファイル自体は削除しません。履歴DBの情報だけを初期化します。"
            if scope == "history"
            else "参照中の対戦記録から該当する関連付けも解除します。"
        )
        if not messagebox.askyesno(
            f"{label}の初期化",
            f"{label}を初期化します。{detail}\n操作前にバックアップを作成します。",
            parent=self.root,
        ):
            return
        phrase = f"{label}を初期化"
        entered = simpledialog.askstring(
            "最終確認",
            f"取り消せない操作です。続行するには「{phrase}」と入力してください。",
            parent=self.root,
        )
        if entered != phrase:
            if entered is not None:
                messagebox.showinfo("初期化を中止", "確認文字列が一致しません。", parent=self.root)
            return
        self._run(
            lambda: self.service.reset_managed_data(scope),
            lambda result: (
                self._activity(f"{label}を初期化しました / バックアップ: {result.backup_path}"),
                self.refresh_all(),
            ),
        )

    def refresh_data_protection(self) -> None:
        if self.smoke_mode:
            return
        self._run(
            lambda: (
                self.service.diagnose_data_integrity(),
                self.service.list_data_backups(),
            ),
            self._data_protection_loaded,
        )

    def _data_protection_loaded(self, result: object) -> None:
        report, backups = result
        latest = backups[0] if backups else None
        finding_severities = {item.severity for item in report.findings}
        severity = (
            "要確認"
            if "error" in finding_severities
            else "注意"
            if "warning" in finding_severities
            else "正常"
        )
        latest_text = (
            f"{latest.created_at.astimezone():%Y-%m-%d %H:%M} / {_format_bytes(latest.size_bytes)}"
            if latest is not None
            else "未作成"
        )
        total = sum(item.size_bytes for item in backups)
        self.data_protection_status_var.set(
            f"DB: {severity} / 最終バックアップ: {latest_text} / "
            f"{len(backups)}世代 {_format_bytes(total)}"
        )
        self._clear_tree(self.data_backup_tree)
        for backup in backups:
            self.data_backup_tree.insert(
                "",
                "end",
                values=(
                    backup.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    backup.reason,
                    backup.schema_version,
                    _format_bytes(backup.size_bytes),
                    "保護" if backup.protected else "通常",
                ),
            )

    def create_data_backup(self) -> None:
        self._run(
            self.service.create_data_backup,
            lambda backup: (
                self._activity(f"データバックアップを作成しました: {backup.path}"),
                self.refresh_data_protection(),
            ),
        )

    def run_data_integrity_diagnosis(self) -> None:
        self._run(
            self.service.diagnose_data_integrity,
            lambda report: messagebox.showinfo(
                "データ整合性診断",
                "\n".join(
                    f"[{item.severity}] {item.message}\n{item.recommendation}"
                    for item in report.findings
                ),
                parent=self.root,
            ),
        )

    def restore_data_backup(self) -> None:
        source = filedialog.askopenfilename(
            parent=self.root,
            title="検証済みバックアップを選択",
            filetypes=(("MDRL backup", "*.mdrl-backup"),),
            initialdir=str(self.service.paths.data / "backups"),
        )
        if not source:
            return

        def preview_loaded(preview: object) -> None:
            before = preview.current_counts
            after = preview.backup_counts
            lines = [
                f"{name}: {before.get(name, 0)} -> {after.get(name, 0)}"
                for name in ("recordings", "duels", "decks", "tags", "seasons", "filters")
            ]
            if not messagebox.askyesno(
                "バックアップ復元の最終確認",
                "現在のDBを安全退避して、次の内容へ置き換えます。\n\n"
                + "\n".join(lines)
                + "\n\n録画ファイルは変更しません。続行しますか？",
                parent=self.root,
            ):
                return
            self._run(
                lambda: self.service.restore_data_backup(Path(source)),
                lambda _result: (
                    self._activity("検証済みバックアップから復元しました"),
                    self.refresh_all(),
                    self.refresh_data_protection(),
                ),
            )

        self._run(
            lambda: self.service.preview_data_restore(Path(source)), preview_loaded
        )

    def refresh_preparations(self) -> None:
        if self.smoke_mode:
            return
        self._run(self.service.list_preparations, self._preparations_loaded)

    def _preparations_loaded(self, items: tuple[object, ...]) -> None:
        self._clear_tree(self.prepare_tree)
        for item in items:
            self.prepare_tree.insert(
                "",
                "end",
                iid=item.queue_id,
                values=(
                    item.state.value,
                    item.metadata.title,
                    item.recording_id,
                    item.queue_id,
                ),
            )

    def enqueue_preparation(self) -> None:
        recording_id = self.prepare_recording_var.get().strip()
        title = self.prepare_title_var.get().strip()
        if not recording_id or not title:
            self._show_error(ValueError("録画IDとタイトルを入力してください"))
            return
        self._run(
            lambda: self.service.enqueue_preparation(recording_id, title=title),
            lambda _item: (
                self._activity("準備キューへ追加しました"),
                self.refresh_preparations(),
            ),
        )

    def process_preparations(self) -> None:
        self._run(
            self.service.process_preparations,
            lambda results: (
                self._activity(f"MP4準備を{len(results)}件処理しました"),
                self.refresh_preparations(),
            ),
        )

    def load_settings(self) -> None:
        if self.smoke_mode:
            return
        try:
            config = self.service.load_config().config
        except Exception as exc:
            self._show_error(exc)
            return
        values = {
            "recorder.ffmpeg_path": config.ffmpeg_path,
            "recorder.audio_input": config.audio_input,
            "recorder.audio_gain_db": str(config.audio_gain_db),
            "recorder.audio_sample_rate": str(config.audio_sample_rate),
            "recorder.audio_channels": str(config.audio_channels),
            "recorder.frame_rate": str(config.frame_rate),
            "recorder.video_bitrate_kbps": str(config.video_bitrate_kbps),
            "recorder.capture_width": str(config.capture_width),
            "recorder.capture_height": str(config.capture_height),
            "detection.visual_maximum_fps": str(config.visual_detection_maximum_fps),
            "detection.visual_language": config.visual_detection_language,
            "detection.visual_minimum_confidence": str(
                config.visual_detection_minimum_confidence
            ),
        }
        for key, value in values.items():
            self.setting_vars[key].set(value)
        self.auto_start_var.set(config.auto_start_recording)
        self.auto_stop_var.set(config.auto_stop_recording)
        self.visual_detection_var.set(config.visual_detection_enabled)
        self.windows_notifications_var.set(config.windows_notifications_enabled)
        self.settings_status_var.set("設定を読み込みました")
        self.audio_choice_var.set(config.audio_input or "音声なし")
        self.refresh_audio_inputs()

    def refresh_audio_inputs(self) -> None:
        if self.smoke_mode:
            return
        self.audio_status_var.set("音声入力を検索中です")
        self._run(self.service.list_audio_inputs, self._audio_inputs_loaded)

    def _audio_inputs_loaded(self, result: object) -> None:
        current = self.setting_vars["recorder.audio_input"].get().strip()
        mapping: dict[str, object] = {"音声なし": None}
        type_labels = {
            "system": "ゲーム・システム",
            "microphone": "マイク",
            "unknown": "音声",
        }
        for index, item in enumerate(result.inputs, start=1):
            base = f"{type_labels.get(item.source_type, '音声')}: {item.display_name}"
            label = base if base not in mapping else f"{base} ({index})"
            mapping[label] = item
        self.audio_inputs_by_label = mapping
        self.audio_input_combo.configure(values=tuple(mapping))
        selected_label = next(
            (
                label
                for label, item in mapping.items()
                if item is not None and current in {item.identifier, item.display_name}
            ),
            "音声なし" if not current else f"利用不可: {current}",
        )
        if selected_label not in mapping:
            mapping[selected_label] = current
            self.audio_input_combo.configure(values=tuple(mapping))
        self.audio_choice_var.set(selected_label)
        if result.errors:
            self.audio_status_var.set(result.errors[0])
        elif result.warnings:
            self.audio_status_var.set(result.warnings[0])
        else:
            self.audio_status_var.set(f"音声入力を{len(result.inputs)}件検出しました")

    def _audio_input_selected(self, _event: object | None = None) -> None:
        selected = self.audio_inputs_by_label.get(self.audio_choice_var.get())
        self.setting_vars["recorder.audio_input"].set(
            selected
            if isinstance(selected, str)
            else selected.identifier
            if selected is not None
            else ""
        )

    def test_selected_audio_input(self) -> None:
        self._audio_input_selected()
        identifier = self.setting_vars["recorder.audio_input"].get()
        self.audio_status_var.set("音声入力をテスト中です")
        self._run(
            lambda: self.service.test_audio_input(identifier),
            lambda result: self.audio_status_var.set(result.message),
        )

    def save_settings(self) -> None:
        self._audio_input_selected()
        values = {key: value.get() for key, value in self.setting_vars.items()}
        values["detection.auto_start_recording"] = str(
            self.auto_start_var.get()
        ).lower()
        values["detection.auto_stop_recording"] = str(self.auto_stop_var.get()).lower()
        values["detection.visual_events_enabled"] = str(
            self.visual_detection_var.get()
        ).lower()
        values["detection.windows_notifications_enabled"] = str(
            self.windows_notifications_var.get()
        ).lower()
        self._run(
            lambda: self.service.save_settings(values),
            lambda _config: (
                self.settings_status_var.set("設定を保存しました"),
                self.run_diagnosis(),
            ),
        )

    def request_close(self) -> None:
        if self.closing:
            return
        if self.busy_operations > 0:
            if not self.smoke_mode:
                messagebox.showinfo(
                    "処理中",
                    "実行中の処理が完了してから終了してください。",
                    parent=self.root,
                )
            return
        active = self.service.watch_active
        try:
            active = active or self.service.recording_snapshot().active
        except Exception:
            pass
        if active and not self.smoke_mode:
            if not messagebox.askyesno(
                "終了の確認",
                "実行中の録画または監視を正常停止して終了しますか？",
                parent=self.root,
            ):
                return
        self.closing = True
        self.busy_label.configure(text="終了処理中")
        self.tasks.submit(
            self.service.close,
            callback=lambda _value: self._destroy(),
            error_callback=lambda _error: self._destroy(),
        )

    def _destroy(self) -> None:
        self.tasks.close()
        self.root.destroy()

    def _poll_runtime(self) -> None:
        while True:
            try:
                event = self.watch_events.get_nowait()
            except queue.Empty:
                break
            if event.kind != "visual":
                if event.kind == "watch" and event.state == "stopped":
                    self._remove_activity(WAITING_ACTIVITY_PREFIX)
                self._activity(event.message)
            if event.kind == "started":
                if not self.automatic_recording_confirmed:
                    self._set_record_status("candidate_recording")
                self.record_detail_var.set(
                    f"録画ID: {event.recording_id or '-'}\n保存先: 履歴で確認"
                )
            elif event.kind in {"stopped", "error"}:
                self.automatic_recording_confirmed = False
                self._set_record_status(
                    "watch_waiting" if self.service.watch_active else "idle"
                )
                if event.kind == "stopped" and event.recording_id:
                    self._activity(
                        f"対戦記録は未入力です。録画履歴から編集できます: {event.recording_id}"
                    )
                    self.refresh_history()
            elif event.kind == "visual_transition" and event.state == "confirmed":
                self.automatic_recording_confirmed = True
                self._set_record_status("automatic_recording")
            elif (
                event.kind == "watch" and event.state == "stopped" and not self.closing
            ):
                self._watch_stopped()
            elif event.kind == "visual":
                self.visual_details_var.set(f"判定詳細: {event.message}")
        can_poll_service = self.busy_operations == 0 and not self.closing
        if can_poll_service and not self.service.watch_active:
            try:
                snapshot = self.service.recording_snapshot()
            except Exception as exc:
                self._activity(f"状態確認エラー: {exc}")
            else:
                self._render_recording(snapshot)
        if not self.closing:
            if can_poll_service:
                status = self.service.visual_detection_status()
                operation = self.service.operation_snapshot()
                self.visual_status_var.set(f"自動監視: {operation.message}")
                self.visual_details_var.set(
                    f"判定詳細: {status.message}\n"
                    f"取得元 {status.source or '-'} / {status.resolution or '-'} / "
                    f"{status.profile} / {status.effective_fps:.1f}fps / 状態 {status.visual_state}\n"
                    f"coin {status.coin_score:.2f} / board {status.board_score:.2f} / "
                    f"turn {status.turn_score:.2f} / order {status.turn_order_score:.2f} / "
                    f"result {status.result_score:.2f} / "
                    f"error {status.error_score:.2f} / replay {status.replay_score:.2f} / "
                    f"overlay {status.overlay_score:.2f} / loading {status.loading_score:.2f} / "
                    f"合意 {status.agreement or '-'} / "
                    f"再起動 {status.restart_count}"
                )
            self.root.after(500, self._poll_runtime)

    def _render_recording(self, snapshot: RecordingSnapshot) -> None:
        statuses = {
            RecordingState.RECORDING: "manual_recording",
            RecordingState.STARTING: "starting",
            RecordingState.STOPPING: "stopping",
            RecordingState.COMPLETED: "idle",
            RecordingState.FAILED: "failed",
            RecordingState.CREATED: "idle",
        }
        self._set_record_status(statuses[snapshot.state])
        self.elapsed_var.set(_format_duration(snapshot.elapsed_seconds))
        self.record_detail_var.set(
            f"録画ID: {snapshot.recording_id or '-'}\n保存先: {snapshot.output_path or '-'}"
        )
        self.start_button.configure(
            state="normal"
            if self.service.operation_snapshot().allows(OperationAction.START_MANUAL)
            else "disabled"
        )
        operation = self.service.operation_snapshot()
        self.stop_button.configure(
            state="normal" if operation.allows(OperationAction.STOP_RECORDING) else "disabled"
        )
        self.watch_button.configure(
            state="normal"
            if operation.allows(OperationAction.START_WATCH)
            or operation.allows(OperationAction.STOP_WATCH)
            else "disabled"
        )
        self._update_duel_write_controls()

    def _set_record_status(self, status: str) -> None:
        presentation = record_status_presentation(status)
        self.record_state_var.set(presentation.text)
        self.record_status_label.configure(
            background=presentation.background,
            foreground=presentation.foreground,
        )

    def _set_incomplete_duel_count(self, count: int) -> None:
        presentation = incomplete_duel_count_presentation(count)
        self.incomplete_duel_count_var.set(presentation.text)
        self.incomplete_duel_count_button.configure(
            background=presentation.background,
            foreground=presentation.foreground,
            activebackground=presentation.background,
            activeforeground=presentation.foreground,
        )

    def _set_record_controls(self, *, starting: bool) -> None:
        snapshot = self.service.operation_snapshot()
        self.start_button.configure(
            state="normal" if snapshot.allows(OperationAction.START_MANUAL) else "disabled"
        )
        self.stop_button.configure(
            state="normal" if snapshot.allows(OperationAction.STOP_RECORDING) else "disabled"
        )
        self.watch_button.configure(
            state="normal"
            if snapshot.allows(OperationAction.START_WATCH)
            or snapshot.allows(OperationAction.STOP_WATCH)
            else "disabled"
        )
        if starting:
            self._set_record_status("starting")

    def _run(
        self,
        operation: Callable[[], T],
        callback: Callable[[T], None] | None = None,
        error_callback: Callable[[BaseException], None] | None = None,
    ) -> None:
        self.busy_operations += 1
        self.busy_label.configure(text="処理中")

        def success(value: T) -> None:
            self._operation_finished()
            if callback is not None:
                callback(value)

        def failure(error: BaseException) -> None:
            self._operation_finished()
            (error_callback or self._show_error)(error)

        self.tasks.submit(operation, callback=success, error_callback=failure)

    def _operation_finished(self) -> None:
        self.busy_operations = max(0, self.busy_operations - 1)
        if self.busy_operations == 0:
            self.busy_label.configure(text="")

    def _show_error(self, error: BaseException) -> None:
        self._activity(f"エラー: {error}")
        if not self.smoke_mode:
            messagebox.showerror("操作を完了できません", str(error), parent=self.root)

    def _activity(self, message: str, *, replace_prefix: str | None = None) -> None:
        if replace_prefix is not None:
            self._remove_activity(replace_prefix)
        self.activity_list.insert(0, message)
        if self.activity_list.size() > 100:
            self.activity_list.delete(100, "end")

    def _remove_activity(self, prefix: str) -> None:
        for index in range(self.activity_list.size() - 1, -1, -1):
            if str(self.activity_list.get(index)).startswith(prefix):
                self.activity_list.delete(index)

    def _selected_id(self, tree: ttk.Treeview) -> str | None:
        selection = tree.selection()
        if not selection:
            self._show_error(ValueError("対象を選択してください"))
            return None
        return str(selection[0])

    @staticmethod
    def _clear_tree(tree: ttk.Treeview) -> None:
        for item in tree.get_children():
            tree.delete(item)

    def _populate_smoke_data(self) -> None:
        self.target_combo.configure(
            values=("Master Duelウィンドウ", "デスクトップ全体")
        )
        self.target_var.set("Master Duelウィンドウ")
        for state, message in (
            ("OK", "設定: 既定値を利用可能"),
            ("OK", "保存先: 書き込み可能"),
            ("注意", "FFmpeg: 実環境で診断してください"),
        ):
            self.diagnosis_tree.insert("", "end", values=(state, message))
        self._activity("GUI起動スモーク")
        self._set_incomplete_duel_count(3)
        self.connection_label.configure(
            text="要確認", foreground=self.COLORS["amber"]
        )
        self.connection_icon_label.configure(
            text=ICON_GLYPHS["warning"], foreground=self.COLORS["amber"]
        )
        smoke_points = (
            StatisticsTrendPoint(
                date(2026, 5, 1), "2026/05", StatisticsMetric(8, 4, 4, 0)
            ),
            StatisticsTrendPoint(
                date(2026, 6, 1), "2026/06", StatisticsMetric(12, 7, 5, 0)
            ),
            StatisticsTrendPoint(
                date(2026, 7, 1), "2026/07", StatisticsMetric(10, 6, 3, 1)
            ),
            StatisticsTrendPoint(
                date(2026, 8, 1), "2026/08", StatisticsMetric(14, 9, 5, 0)
            ),
        )
        smoke_dashboard = StatisticsDashboard(
            overall=StatisticsMetric(44, 26, 17, 1),
            filtered=StatisticsMetric(44, 26, 17, 1),
            by_deck=(),
            by_play_order=(),
            by_deck_play_order=(),
            by_coin_face=(),
            by_coin_toss_outcome=(),
            trend=smoke_points,
            filters=StatisticsFilter(),
            granularity="month",
        )
        self._statistics_loaded((smoke_dashboard, (), (), ()))


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _parse_filter_date(value: str, label: str) -> date | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label}はYYYY-MM-DD形式で入力してください") from exc


def _format_win_rate(metric: StatisticsMetric) -> str:
    if metric.win_rate is None:
        return "-"
    return f"{metric.win_rate * 100:.1f}%"


def _format_statistics_detail(metric: StatisticsMetric) -> str:
    return f"{metric.matches}戦  {metric.wins}勝  {metric.losses}敗  {metric.draws}引分"


def _statistics_breakdown_values(
    label: str, metric: StatisticsMetric
) -> tuple[object, ...]:
    return (
        label,
        metric.matches,
        metric.wins,
        metric.losses,
        metric.draws,
        _format_win_rate(metric),
    )


def _format_elapsed_ms(elapsed_ms: int) -> str:
    minutes, seconds = divmod(elapsed_ms / 1000, 60)
    return f"{int(minutes):02d}:{seconds:05.2f}"


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _contrast_text_color(color: str) -> str:
    try:
        red, green, blue = (int(color[index : index + 2], 16) for index in (1, 3, 5))
    except (TypeError, ValueError):
        return "#202124"
    luminance = (0.299 * red) + (0.587 * green) + (0.114 * blue)
    return "#202124" if luminance >= 150 else "#ffffff"


def build_gui_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Master Duel Recorder Lite GUI")
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--user-data-dir", type=Path, default=None)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--smoke-output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_gui_parser().parse_args(argv)
    root = tk.Tk()
    service = RecorderApplicationService(
        project_root=args.project_root,
        user_data_dir=args.user_data_dir,
    )
    app = RecorderGui(root, service, smoke_mode=args.smoke_test)
    if args.smoke_test:
        root.update_idletasks()
        geometry = {
            "width": root.winfo_width(),
            "height": root.winfo_height(),
            "widgets": sorted(app.widgets),
            "title": root.title(),
            "version": __version__,
            "runtime_data": str(service.paths.root),
        }
        if geometry["width"] < 900 or geometry["height"] < 600 or len(app.widgets) < 8:
            app._destroy()
            return 1
        if args.smoke_output is not None:
            args.smoke_output.parent.mkdir(parents=True, exist_ok=True)
            args.smoke_output.write_text(
                json.dumps(geometry, ensure_ascii=False), encoding="utf-8"
            )
        root.after(700, app.request_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
