from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import queue
import sys
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Callable, TypeVar
import webbrowser

from . import __version__
from .application import (
    ApplicationEvent,
    DuelEditorData,
    RecorderApplicationService,
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
from .duel_timeline import DuelEvent
from .ffmpeg_setup import (
    FFMPEG_DOWNLOAD_URL,
    FFMPEG_LICENSE,
    FFMPEG_PROVIDER_PAGE,
    FfmpegInstallResult,
    FfmpegInstallProgress,
)
from .preflight import CheckStatus, PreflightReport
from .recording_browsing import RecordingReference
from .recording_session import RecordingState


T = TypeVar("T")
WAITING_ACTIVITY_PREFIX = "対戦開始を判定中です"

ICON_GLYPHS = {
    "add": "+",
    "delete": "🗑",
    "edit": "✎",
    "folder": "📂",
    "play": "▶",
    "refresh": "↻",
    "save": "▣",
    "test": "▷",
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
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mdrl-gui")
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


class RecorderGui:
    COLORS = {
        "canvas": "#f4f5f7",
        "surface": "#ffffff",
        "sidebar": "#20242a",
        "sidebar_active": "#343a43",
        "text": "#202124",
        "muted": "#687078",
        "border": "#d9dde3",
        "green": "#147d64",
        "red": "#b3261e",
        "amber": "#9a6700",
        "blue": "#245ea8",
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
        self.catalog_update_buttons: dict[str, ttk.Button] = {}
        self.catalog_delete_buttons: dict[str, ttk.Button] = {}
        self.history_views_by_id: dict[str, object] = {}
        self.current_page = "record"
        self.watch_events: queue.Queue[ApplicationEvent] = queue.Queue()
        self.busy_operations = 0
        self.closing = False
        self.ffmpeg_setup_prompted = False
        self.ffmpeg_setup_dialog: tk.Toplevel | None = None
        self.tooltips: list[WidgetTooltip] = []

        self._configure_window()
        self._configure_styles()
        self._build_shell()
        self._build_record_page()
        self._build_history_page()
        self._build_catalog_pages()
        self._build_recovery_page()
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
        style.configure(
            "Title.TLabel",
            background=self.COLORS["canvas"],
            foreground=self.COLORS["text"],
            font=("Segoe UI Semibold", 18),
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
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("Icon.TButton", font=("Segoe UI Symbol", 12), padding=(8, 7))
        style.configure("Primary.TButton", foreground="#ffffff", background=self.COLORS["blue"])
        style.map("Primary.TButton", background=[("active", "#1d4f91"), ("disabled", "#9ea9b7")])
        style.configure("Record.TButton", foreground="#ffffff", background=self.COLORS["red"])
        style.map("Record.TButton", background=[("active", "#8f1f19"), ("disabled", "#c6a09d")])
        style.configure("Stop.TButton", foreground="#ffffff", background=self.COLORS["green"])
        style.map("Stop.TButton", background=[("active", "#0f6651"), ("disabled", "#9ebdb5")])
        style.configure("Treeview", rowheight=29, font=("Segoe UI", 9), borderwidth=0)
        style.configure("Treeview.Heading", font=("Segoe UI Semibold", 9), padding=(6, 7))
        style.map("Treeview", background=[("selected", "#d9e7f7")], foreground=[("selected", "#202124")])

    def _build_shell(self) -> None:
        shell = tk.Frame(self.root, background=self.COLORS["canvas"])
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, width=196, background=self.COLORS["sidebar"])
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        brand = tk.Label(
            sidebar,
            text="MDRL",
            anchor="w",
            padx=20,
            pady=22,
            background=self.COLORS["sidebar"],
            foreground="#ffffff",
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
            foreground="#aeb5bf",
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(0, 18))
        for key, label in (
            ("record", "録画"),
            ("history", "録画履歴"),
            ("decks", "デッキ名"),
            ("tags", "タグ"),
            ("recovery", "復旧"),
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
                foreground="#e5e8ec",
                activebackground=self.COLORS["sidebar_active"],
                activeforeground="#ffffff",
                font=("Segoe UI Semibold", 10),
                command=lambda page=key: self.show_page(page),
            )
            button.pack(fill="x")
            self.nav_buttons[key] = button
        self.connection_label = tk.Label(
            sidebar,
            text="準備中",
            anchor="w",
            padx=20,
            pady=16,
            background=self.COLORS["sidebar"],
            foreground="#aeb5bf",
            font=("Segoe UI", 9),
        )
        self.connection_label.pack(side="bottom", fill="x")

        content = ttk.Frame(shell, style="App.TFrame", padding=(24, 18, 24, 20))
        content.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(content, style="App.TFrame")
        header.pack(fill="x", pady=(0, 14))
        self.page_title = ttk.Label(header, text="録画", style="Title.TLabel")
        self.page_title.pack(side="left")
        self.busy_label = ttk.Label(header, text="", style="Title.TLabel", font=("Segoe UI", 9))
        self.busy_label.pack(side="right")
        self.page_host = ttk.Frame(content, style="App.TFrame")
        self.page_host.pack(fill="both", expand=True)

    def _new_page(self, key: str) -> ttk.Frame:
        page = ttk.Frame(self.page_host, style="App.TFrame")
        self.pages[key] = page
        return page

    def _surface(self, parent: tk.Misc, *, padding: tuple[int, int] = (16, 14)) -> ttk.Frame:
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

    def _build_record_page(self) -> None:
        page = self._new_page("record")
        target_panel = self._surface(page)
        target_panel.pack(fill="x", pady=(0, 12))
        ttk.Label(target_panel, text="録画対象", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            target_panel,
            text="選択したウィンドウ、モニター、またはデスクトップを実際のFFmpeg入力に使用します。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(3, 10))
        self.target_var = tk.StringVar()
        self.target_combo = ttk.Combobox(target_panel, textvariable=self.target_var, state="readonly", width=74)
        self.target_combo.grid(row=2, column=0, sticky="ew", padx=(0, 8))
        refresh = self._icon_button(
            target_panel, "refresh", "録画対象を更新", self.refresh_targets
        )
        refresh.grid(row=2, column=1, padx=(0, 8))
        save = ttk.Button(target_panel, text="選択を保存", style="Primary.TButton", command=self.save_selected_target)
        save.grid(row=2, column=2)
        target_panel.columnconfigure(0, weight=1)
        self.widgets["target_selector"] = self.target_combo

        controls = self._surface(page, padding=(18, 18))
        controls.pack(fill="x", pady=(0, 12))
        self.record_state_var = tk.StringVar(value="待機中")
        self.elapsed_var = tk.StringVar(value="00:00:00")
        ttk.Label(controls, textvariable=self.record_state_var, style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(controls, textvariable=self.elapsed_var, style="Heading.TLabel", font=("Consolas", 20)).grid(
            row=1, column=0, sticky="w", pady=(5, 2)
        )
        self.record_detail_var = tk.StringVar(value="録画ID: -\n保存先: -")
        ttk.Label(controls, textvariable=self.record_detail_var, style="Muted.TLabel", justify="left").grid(
            row=2, column=0, sticky="w"
        )
        self.visual_status_var = tk.StringVar(value="自動判定: 録画開始後に状態を表示")
        ttk.Label(
            controls, textvariable=self.visual_status_var, style="Muted.TLabel"
        ).grid(row=3, column=0, sticky="w", pady=(5, 0))
        self.record_audio_status_var = tk.StringVar(value="音声: 設定で入力を選択できます")
        ttk.Label(
            controls,
            textvariable=self.record_audio_status_var,
            style="Muted.TLabel",
        ).grid(row=4, column=0, sticky="w", pady=(3, 0))
        button_row = ttk.Frame(controls, style="Surface.TFrame")
        button_row.grid(row=0, column=1, rowspan=5, sticky="e")
        self.start_button = ttk.Button(button_row, text="録画開始", style="Record.TButton", command=self.start_recording)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(button_row, text="停止", style="Stop.TButton", command=self.stop_recording, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 18))
        self.watch_button = ttk.Button(button_row, text="自動監視開始", command=self.toggle_watch)
        self.watch_button.pack(side="left")
        controls.columnconfigure(0, weight=1)
        self.widgets["record_start"] = self.start_button
        self.widgets["record_stop"] = self.stop_button
        self.widgets["watch_toggle"] = self.watch_button
        self.widgets["visual_status"] = self.visual_status_var

        lower = ttk.Frame(page, style="App.TFrame")
        lower.pack(fill="both", expand=True)
        diagnosis = self._surface(lower)
        diagnosis.pack(side="left", fill="both", expand=True, padx=(0, 6))
        header = ttk.Frame(diagnosis, style="Surface.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="環境診断", style="Heading.TLabel").pack(side="left")
        ttk.Button(header, text="診断実行", command=self.run_diagnosis).pack(side="right")
        self.diagnosis_tree = ttk.Treeview(diagnosis, columns=("state", "message"), show="headings", height=8)
        self.diagnosis_tree.heading("state", text="状態")
        self.diagnosis_tree.heading("message", text="項目と結果")
        self.diagnosis_tree.column("state", width=76, anchor="center", stretch=False)
        self.diagnosis_tree.column("message", width=400)
        self.diagnosis_tree.pack(fill="both", expand=True, pady=(10, 0))

        activity = self._surface(lower)
        activity.pack(side="left", fill="both", expand=True, padx=(6, 0))
        ttk.Label(activity, text="アクティビティ", style="Heading.TLabel").pack(anchor="w")
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
        ttk.Label(toolbar, text="録画履歴", style="Heading.TLabel").pack(side="left")
        ttk.Button(toolbar, text="整合性確認", command=self.check_history).pack(side="right")
        self._icon_button(toolbar, "refresh", "録画履歴を更新", self.refresh_history).pack(
            side="right", padx=(0, 8)
        )
        self.history_timeline_button = ttk.Button(
            toolbar,
            text="◷ タイムライン",
            command=self.show_selected_timeline,
            state="disabled",
        )
        self.history_timeline_button.pack(side="right", padx=(0, 8))
        self.history_diagnostic_button = ttk.Button(
            toolbar,
            text="ⓘ 診断",
            command=self.show_selected_history_diagnostic,
            state="disabled",
        )
        self.history_diagnostic_button.pack(side="right", padx=(0, 8))
        panel = self._surface(page, padding=(0, 0))
        panel.pack(fill="both", expand=True)
        columns = (
            "started",
            "result",
            "order",
            "duel_type",
            "duration",
            "size",
            "audio",
            "actions",
        )
        self.history_tree = ttk.Treeview(panel, columns=columns, show="headings")
        for key, label, width in (
            ("started", "開始日時", 155),
            ("result", "勝敗", 90),
            ("order", "先後", 75),
            ("duel_type", "対戦種別", 105),
            ("duration", "時間", 85),
            ("size", "サイズ", 100),
            ("audio", "音声", 85),
            ("actions", "操作", 150),
        ):
            self.history_tree.heading(key, text=label)
            self.history_tree.column(key, width=width, stretch=key == "started")
        self.history_tree.pack(fill="both", expand=True)
        self.history_tree.bind("<<TreeviewSelect>>", self._history_selection_changed)
        self.history_tree.bind("<ButtonRelease-1>", self._history_action_clicked, add="+")
        self.history_tree.bind("<Motion>", self._history_action_motion)
        self.history_tree.bind("<Leave>", lambda _event: self._hide_history_action_tip())
        self.history_tree.bind("<Return>", lambda _event: self.play_selected_history())
        self.history_tree.bind("<Control-e>", lambda _event: self.edit_selected_duel_record())
        self.history_tree.bind("<Control-o>", lambda _event: self.reveal_selected_history())
        self.history_tree.bind("<Delete>", lambda _event: self.delete_selected_history())
        self.history_action_tip: tk.Toplevel | None = None
        self.history_action_tip_text = ""
        self.widgets["history_table"] = self.history_tree
        self.widgets["history_play"] = self.history_tree
        self.widgets["history_reveal"] = self.history_tree
        self.widgets["history_diagnostic"] = self.history_diagnostic_button
        self.widgets["history_duel"] = self.history_tree
        self.widgets["history_timeline"] = self.history_timeline_button
        self.widgets["history_delete"] = self.history_tree

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
        ttk.Label(toolbar, text=f"{title}管理", style="Heading.TLabel").pack(side="left")
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
        color_var = tk.StringVar(value="#4F6F8F")
        self.catalog_name_vars[kind] = name_var
        self.catalog_description_vars[kind] = description_var
        self.catalog_color_vars[kind] = color_var
        ttk.Label(editor, text="名前").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(editor, textvariable=name_var).grid(
            row=0, column=1, sticky="ew", padx=(0, 12)
        )
        ttk.Label(editor, text="説明").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(editor, textvariable=description_var).grid(
            row=1, column=1, sticky="ew", padx=(0, 12), pady=(8, 0)
        )
        if is_tag:
            ttk.Label(editor, text="カラー").grid(row=0, column=2, sticky="w", padx=(0, 8))
            color_button = tk.Button(
                editor,
                textvariable=color_var,
                width=10,
                command=lambda: self.choose_catalog_color("tag"),
            )
            color_button.grid(row=0, column=3, padx=(0, 12))
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
        columns = ("name", "description", "color") if is_tag else ("name", "description")
        tree = ttk.Treeview(
            panel,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        tree.heading("name", text="名前")
        tree.heading("description", text="説明")
        tree.column("name", width=220, stretch=False)
        tree.column("description", width=520, stretch=True)
        if is_tag:
            tree.heading("color", text="カラー")
            tree.column("color", width=110, stretch=False)
        tree.pack(fill="both", expand=True)
        tree.bind(
            "<<TreeviewSelect>>",
            lambda _event, selected=kind: self._catalog_selection_changed(selected),
        )
        self.catalog_trees[kind] = tree
        self.widgets[f"{kind}_catalog_table"] = tree
        if kind == "deck":
            self.widgets["catalog_table"] = tree

    def _build_recovery_page(self) -> None:
        page = self._new_page("recovery")
        toolbar = self._surface(page, padding=(14, 10))
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="中断録画の復旧", style="Heading.TLabel").pack(side="left")
        ttk.Button(toolbar, text="中断検出", command=self.detect_recovery).pack(side="right")
        self._icon_button(
            toolbar, "refresh", "復旧対象を更新", self.refresh_recovery
        ).pack(side="right", padx=(0, 8))
        panel = self._surface(page, padding=(0, 0))
        panel.pack(fill="both", expand=True)
        self.recovery_tree = ttk.Treeview(panel, columns=("state", "code", "file", "id"), show="headings")
        for key, label, width in (
            ("state", "復旧状態", 110),
            ("code", "分類", 130),
            ("file", "ファイル", 300),
            ("id", "録画ID", 260),
        ):
            self.recovery_tree.heading(key, text=label)
            self.recovery_tree.column(key, width=width, stretch=key in {"file", "id"})
        self.recovery_tree.pack(fill="both", expand=True)
        actions = self._surface(page, padding=(14, 10))
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="検査", command=self.inspect_selected_recovery).pack(side="left")
        ttk.Button(actions, text="修復予定", command=lambda: self.repair_selected_recovery(True)).pack(side="left", padx=8)
        ttk.Button(actions, text="別ファイルへ修復", style="Primary.TButton", command=lambda: self.repair_selected_recovery(False)).pack(side="left")
        self.recovery_result_var = tk.StringVar(value="対象を選択してください")
        ttk.Label(actions, textvariable=self.recovery_result_var, style="Muted.TLabel").pack(side="right")
        self.widgets["recovery_table"] = self.recovery_tree

    def _build_prepare_page(self) -> None:
        page = self._new_page("prepare")
        form = self._surface(page)
        form.pack(fill="x", pady=(0, 10))
        ttk.Label(form, text="アップロード用MP4準備", style="Heading.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(form, text="録画ID", style="Body.TLabel").grid(row=1, column=0, sticky="w", pady=(12, 4))
        ttk.Label(form, text="タイトル", style="Body.TLabel").grid(row=1, column=1, sticky="w", pady=(12, 4))
        self.prepare_recording_var = tk.StringVar()
        self.prepare_title_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.prepare_recording_var, width=44).grid(row=2, column=0, sticky="ew", padx=(0, 8))
        ttk.Entry(form, textvariable=self.prepare_title_var, width=42).grid(row=2, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(form, text="キューへ追加", style="Primary.TButton", command=self.enqueue_preparation).grid(row=2, column=2, padx=(0, 8))
        ttk.Button(form, text="待機中を実行", command=self.process_preparations).grid(row=2, column=3)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        panel = self._surface(page, padding=(0, 0))
        panel.pack(fill="both", expand=True)
        self.prepare_tree = ttk.Treeview(panel, columns=("state", "title", "recording", "queue"), show="headings")
        for key, label, width in (
            ("state", "状態", 100),
            ("title", "タイトル", 240),
            ("recording", "録画ID", 260),
            ("queue", "キューID", 260),
        ):
            self.prepare_tree.heading(key, text=label)
            self.prepare_tree.column(key, width=width, stretch=key in {"title", "recording", "queue"})
        self.prepare_tree.pack(fill="both", expand=True)
        self.widgets["prepare_table"] = self.prepare_tree

    def _build_settings_page(self) -> None:
        page = self._new_page("settings")
        panel = self._surface(page, padding=(20, 18))
        panel.pack(fill="both", expand=True)
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
            ttk.Label(panel, text=label, style="Body.TLabel").grid(row=row, column=column, columnspan=span, sticky="w", pady=(14, 4))
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
        ttk.Button(audio_row, text="▷ テスト", command=self.test_selected_audio_input).pack(
            side="left", padx=(8, 0)
        )
        self.audio_status_var = tk.StringVar(value="")
        ttk.Label(audio_row, textvariable=self.audio_status_var, style="Muted.TLabel").pack(
            side="left", padx=(10, 0)
        )
        self.auto_start_var = tk.BooleanVar(value=True)
        self.auto_stop_var = tk.BooleanVar(value=True)
        self.visual_detection_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(panel, text="ウィンドウ検出時に自動開始", variable=self.auto_start_var).grid(row=9, column=0, sticky="w", pady=(18, 0))
        ttk.Checkbutton(panel, text="ウィンドウ消失時に自動停止", variable=self.auto_stop_var).grid(row=9, column=1, sticky="w", pady=(18, 0))
        ttk.Checkbutton(panel, text="対戦イベントを自動判定", variable=self.visual_detection_var).grid(row=9, column=2, sticky="w", pady=(18, 0))
        for column, (key, label) in enumerate(
            (
                ("detection.visual_maximum_fps", "自動判定fps（最大2）"),
                ("detection.visual_language", "UI言語（auto / ja / en）"),
                ("detection.visual_minimum_confidence", "候補閾値（0.70以上）"),
            )
        ):
            ttk.Label(panel, text=label, style="Body.TLabel").grid(
                row=10, column=column, sticky="w", pady=(14, 4)
            )
            ttk.Entry(panel, textvariable=self.setting_vars[key]).grid(
                row=11, column=column, sticky="ew", padx=(0, 12 if column < 2 else 0)
            )
        ttk.Label(panel, text="データ保存先", style="Body.TLabel").grid(
            row=12, column=0, sticky="w", pady=(18, 4)
        )
        self.runtime_path_var = tk.StringVar(
            value=str(self.service.runtime_data_directory())
        )
        ttk.Label(
            panel,
            textvariable=self.runtime_path_var,
            style="Muted.TLabel",
        ).grid(row=13, column=0, columnspan=3, sticky="w")
        footer = ttk.Frame(panel, style="Surface.TFrame")
        footer.grid(row=14, column=0, columnspan=3, sticky="ew", pady=(18, 0))
        self.settings_status_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.settings_status_var, style="Muted.TLabel").pack(side="left")
        self._icon_button(
            footer, "refresh", "設定を再読込", self.load_settings
        ).pack(side="right")
        self._icon_button(
            footer, "save", "設定を保存", self.save_settings, style="Primary.TButton"
        ).pack(side="right", padx=(0, 8))
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(2, weight=1)
        self.widgets["settings_form"] = panel
        self.widgets["ffmpeg_setup"] = self.ffmpeg_setup_button

    def show_page(self, key: str) -> None:
        titles = {
            "record": "録画",
            "history": "録画履歴",
            "decks": "デッキ名",
            "tags": "タグ",
            "recovery": "復旧",
            "prepare": "MP4準備",
            "settings": "設定",
        }
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        self.page_title.configure(text=titles[key])
        for name, button in self.nav_buttons.items():
            button.configure(background=self.COLORS["sidebar_active"] if name == key else self.COLORS["sidebar"])
        self.current_page = key
        if key == "history":
            self.refresh_history()
        elif key == "decks":
            self.refresh_catalog("deck")
        elif key == "tags":
            self.refresh_catalog("tag")
        elif key == "recovery":
            self.refresh_recovery()
        elif key == "prepare":
            self.refresh_preparations()
        elif key == "settings":
            self.load_settings()

    def refresh_all(self) -> None:
        self.refresh_targets()
        self.run_diagnosis()
        self.refresh_history()

    def refresh_catalog(self, kind: str) -> None:
        if self.smoke_mode:
            return
        operation = self.service.list_tags if kind == "tag" else self.service.list_decks
        self._run(operation, lambda entries: self._catalog_loaded(kind, entries))

    def _catalog_loaded(self, kind: str, entries: tuple[DuelCatalogEntry, ...]) -> None:
        for entry in entries:
            self.catalog_entries_by_id[str(entry.entry_id)] = entry
        tree = self.catalog_trees[kind]
        self._clear_tree(tree)
        for entry in entries:
            values = (
                (entry.name, entry.description, entry.color or "")
                if kind == "tag"
                else (entry.name, entry.description)
            )
            tree.insert(
                "",
                "end",
                iid=str(entry.entry_id),
                values=values,
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
        if kind == "tag":
            self.catalog_color_vars[kind].set(entry.color or "#4F6F8F")

    def choose_catalog_color(self, kind: str) -> None:
        current = self.catalog_color_vars[kind].get() or "#4F6F8F"
        _rgb, selected = colorchooser.askcolor(current, title="タグカラー", parent=self.root)
        if selected:
            self.catalog_color_vars[kind].set(selected.upper())

    def add_catalog_entry(self, kind: str) -> None:
        name = self.catalog_name_vars[kind].get()
        description = self.catalog_description_vars[kind].get()
        color = self.catalog_color_vars[kind].get() if kind == "tag" else None
        self._run(
            lambda: self.service.add_duel_catalog_entry(
                kind,
                name,
                description=description,
                color=color,
            ),
            lambda entry: (
                self._activity(f"{'タグ' if kind == 'tag' else 'デッキ名'}を追加しました: {entry.name}"),
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
                and (not config.capture_target_id or target.identifier == config.capture_target_id)
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
        self._run(lambda: self.service.select_capture_target(target), lambda _config: self._activity(f"録画対象を保存しました: {target.label}"))

    def run_diagnosis(self) -> None:
        self._run(self.service.diagnose, self._diagnosis_loaded)

    def _diagnosis_loaded(self, report: PreflightReport) -> None:
        for item in self.diagnosis_tree.get_children():
            self.diagnosis_tree.delete(item)
        labels = {CheckStatus.OK: "OK", CheckStatus.WARNING: "注意", CheckStatus.ERROR: "エラー"}
        for check in report.checks:
            self.diagnosis_tree.insert("", "end", values=(labels[check.status], f"{check.label}: {check.message}"))
        self.connection_label.configure(text="利用可能" if report.succeeded else "要確認", foreground="#7fe1bd" if report.succeeded else "#ffcc80")
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
        self._run(lambda: self.service.start_recording(target), self._recording_started, self._recording_failed)

    def _recording_started(self, snapshot: RecordingSnapshot) -> None:
        self._render_recording(snapshot)
        self._update_record_audio_status(active=True)
        self._activity(f"録画を開始しました: {snapshot.recording_id}")

    def _recording_failed(self, error: BaseException) -> None:
        self._set_record_controls(starting=False)
        self._show_error(error)

    def stop_recording(self) -> None:
        self.stop_button.configure(state="disabled")
        self._run(self.service.stop_recording, self._recording_stopped)

    def _recording_stopped(self, snapshot: RecordingSnapshot) -> None:
        self._render_recording(snapshot)
        self._update_record_audio_status(active=False)
        self._activity(f"録画を停止しました: {snapshot.output_path}")
        if snapshot.state is RecordingState.COMPLETED and snapshot.recording_id:
            self._open_duel_editor(snapshot.recording_id)

    def toggle_watch(self) -> None:
        if self.service.watch_active:
            self.watch_button.configure(state="disabled")
            self._run(self.service.stop_watch, lambda _value: self._watch_stopped(), self._watch_failed)
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
        self.record_state_var.set("自動監視中")
        self._update_record_audio_status(active=False)

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
        self.record_state_var.set("待機中")

    def _watch_failed(self, error: BaseException) -> None:
        self._watch_stopped()
        self._show_error(error)

    def refresh_history(self) -> None:
        if self.smoke_mode:
            return
        self._run(self.service.list_history_views, self._history_loaded)

    def _history_loaded(self, views: tuple[object, ...]) -> None:
        previous = self.history_tree.selection()
        previous_id = str(previous[0]) if previous else None
        self._clear_tree(self.history_tree)
        self.history_views_by_id = {view.recording_id: view for view in views}
        for view in views:
            entry = view.entry
            started = entry.started_at or entry.created_at
            duration = f"{entry.duration_seconds:.1f}秒" if entry.duration_seconds is not None else "-"
            size = _format_bytes(entry.size_bytes)
            if entry.state == "failed":
                result, play_order, duel_type = "録画失敗", "-", "-"
            elif entry.state in {"starting", "recording"}:
                result = "開始中" if entry.state == "starting" else "録画中"
                play_order, duel_type = "-", "-"
            elif view.duel_record is None:
                result = play_order = duel_type = "未入力"
            else:
                result = duel_choice_label("result", view.result)
                play_order = duel_choice_label("play_order", view.play_order)
                duel_type = duel_choice_label("duel_type", view.duel_type)
            self.history_tree.insert(
                "",
                "end",
                iid=entry.recording_id,
                values=(
                    started.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                    result,
                    play_order,
                    duel_type,
                    duration,
                    size,
                    {
                        "disabled": "なし",
                        "configured": "設定済み",
                        "recorded": "あり",
                        "warning": "警告",
                        "failed": "失敗",
                    }.get(entry.audio_state, entry.audio_state),
                    "    ".join(ICON_GLYPHS[icon] for icon, _label, _key in HISTORY_ROW_ACTIONS),
                ),
            )
        if previous_id is not None and self.history_tree.exists(previous_id):
            self.history_tree.selection_set(previous_id)
            self.history_tree.focus(previous_id)
            self.history_tree.see(previous_id)
        self._history_selection_changed()

    def _history_selection_changed(self, _event: object | None = None) -> None:
        state = "normal" if self.history_tree.selection() else "disabled"
        self.history_diagnostic_button.configure(state=state)
        self.history_timeline_button.configure(state=state)

    def _history_action_clicked(self, event: tk.Event[tk.Misc]) -> None:
        recording_id = self.history_tree.identify_row(event.y)
        if not recording_id or self.history_tree.identify_column(event.x) != "#8":
            return
        bbox = self.history_tree.bbox(recording_id, "actions")
        if not bbox:
            return
        self.history_tree.selection_set(recording_id)
        self.history_tree.focus(recording_id)
        action_index = min(3, max(0, int((event.x - bbox[0]) * 4 / max(1, bbox[2]))))
        actions = (
            self.play_selected_history,
            self.edit_selected_duel_record,
            self.reveal_selected_history,
            self.delete_selected_history,
        )
        actions[action_index]()

    def _history_action_motion(self, event: tk.Event[tk.Misc]) -> None:
        recording_id = self.history_tree.identify_row(event.y)
        if not recording_id or self.history_tree.identify_column(event.x) != "#8":
            self._hide_history_action_tip()
            return
        bbox = self.history_tree.bbox(recording_id, "actions")
        if not bbox:
            self._hide_history_action_tip()
            return
        action_index = min(3, max(0, int((event.x - bbox[0]) * 4 / max(1, bbox[2]))))
        _icon, label, shortcut = HISTORY_ROW_ACTIONS[action_index]
        self._show_history_action_tip(f"{label} ({shortcut})")

    def _show_history_action_tip(self, text: str) -> None:
        if self.history_action_tip is not None and self.history_action_tip_text == text:
            return
        self._hide_history_action_tip()
        tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(
            tip,
            text=text,
            background="#20242a",
            foreground="#ffffff",
            padx=7,
            pady=4,
            font=("Segoe UI", 9),
        ).pack()
        tip.geometry(f"+{self.root.winfo_pointerx() + 12}+{self.root.winfo_pointery() + 12}")
        self.history_action_tip = tip
        self.history_action_tip_text = text

    def _hide_history_action_tip(self) -> None:
        if getattr(self, "history_action_tip", None) is not None:
            self.history_action_tip.destroy()
            self.history_action_tip = None
            self.history_action_tip_text = ""

    def play_selected_history(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        recording_id = str(selection[0])
        self._run(
            lambda: self.service.play_recording(recording_id),
            lambda reference: self._recording_opened("再生を開始しました", reference),
        )

    def reveal_selected_history(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        recording_id = str(selection[0])
        self._run(
            lambda: self.service.reveal_recording(recording_id),
            lambda reference: self._recording_opened("保存場所を開きました", reference),
        )

    def delete_selected_history(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        recording_id = str(selection[0])
        row = self.history_tree.item(recording_id, "values")
        display_name = row[0] if row else recording_id
        if not messagebox.askyesno(
            "録画履歴を削除",
            "次の録画を完全に削除します。\n\n"
            f"開始日時: {display_name}\n\n"
            "録画ファイル、復旧成果物、対戦記録、タイムラインも削除されます。"
            "この操作は元に戻せません。",
            parent=self.root,
        ):
            return
        self._run(
            lambda: self.service.delete_history(recording_id),
            lambda result: (
                self._activity(
                    f"録画履歴を削除しました: {result.recording_id} "
                    f"/ ファイル {len(result.deleted_files)}件"
                ),
                self.refresh_history(),
                self.refresh_recovery(),
            ),
        )

    def show_selected_history_diagnostic(self) -> None:
        selection = self.history_tree.selection()
        if not selection:
            return
        recording_id = str(selection[0])
        self._run(lambda: self.service.get_history(recording_id), self._show_history_diagnostic)

    def edit_selected_duel_record(self) -> None:
        selection = self.history_tree.selection()
        if selection:
            self._open_duel_editor(str(selection[0]))

    def show_selected_timeline(self) -> None:
        selection = self.history_tree.selection()
        if selection:
            self._show_timeline(str(selection[0]))

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
        refresh_button = self._icon_button(frame, "refresh", "タイムラインを更新", lambda: None)
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
                        f"{event.confidence:.2f}" if event.confidence is not None else "-",
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
            state = "normal" if event is not None and event.status == "candidate" else "disabled"
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

    def _open_duel_editor(self, recording_id: str) -> None:
        self._run(
            lambda: self.service.get_duel_editor_data(recording_id),
            lambda data: self._show_duel_editor(recording_id, data),
        )

    def _show_duel_editor(self, recording_id: str, data: DuelEditorData) -> None:
        values = data.values
        revision = data.record.revision if data.record is not None else 0
        dialog = tk.Toplevel(self.root)
        dialog.title("対戦記録")
        dialog.geometry("720x700")
        dialog.minsize(620, 620)
        dialog.transient(self.root)
        form = ttk.Frame(dialog, padding=18)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="対戦内容", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        fields = (
            ("状態", "status", values.status),
            ("勝敗", "result", values.result),
            ("先後", "play_order", values.play_order),
            ("対戦種別", "duel_type", values.duel_type),
        )
        variables: dict[str, tk.StringVar] = {}
        row = 1
        for label, key, current in fields:
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=5)
            variable = tk.StringVar(value=duel_choice_label(key, current))
            variables[key] = variable
            ttk.Combobox(
                form,
                textvariable=variable,
                values=duel_choice_labels(key),
                state="readonly",
            ).grid(row=row, column=1, sticky="ew", pady=5)
            row += 1
        deck_names = tuple(entry.name for entry in data.decks)
        for label, key, current in (
            ("自分デッキ", "own_deck", values.own_deck),
            ("相手デッキ", "opponent_deck", values.opponent_deck),
        ):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=5)
            variable = tk.StringVar(value=current)
            variables[key] = variable
            ttk.Combobox(
                form,
                textvariable=variable,
                values=deck_names,
                state="normal",
            ).grid(row=row, column=1, sticky="ew", pady=5)
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
            current = tuple(str(tag_list.get(index)) for index in range(tag_list.size()))
            if tag.casefold() not in {item.casefold() for item in current}:
                tag_list.insert("end", tag)
                style_tag(tag_list.size() - 1, tag)
            tag_var.set("")

        def remove_tag() -> None:
            selection = tag_list.curselection()
            if selection:
                tag_list.delete(selection[0])

        ttk.Button(tag_panel, text="追加", command=add_tag).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(tag_panel, text="選択を外す", command=remove_tag).grid(row=0, column=2)

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
        form.columnconfigure(1, weight=1)
        form.rowconfigure(row, weight=1)

        def save() -> None:
            add_tag()
            tags = tuple(str(tag_list.get(index)) for index in range(tag_list.size()))
            updated = DuelRecordValues(
                status=duel_choice_value("status", variables["status"].get()),
                result=duel_choice_value("result", variables["result"].get()),
                play_order=duel_choice_value("play_order", variables["play_order"].get()),
                own_deck=variables["own_deck"].get(),
                opponent_deck=variables["opponent_deck"].get(),
                duel_type=duel_choice_value("duel_type", variables["duel_type"].get()),
                tags=tags,
                notes=notes.get("1.0", "end-1c"),
            )
            self._run(
                lambda: self.service.save_duel_record(
                    recording_id,
                    updated,
                    expected_revision=revision,
                ),
                lambda saved: (
                    self._activity(f"対戦記録を保存しました: revision {saved.revision}"),
                    dialog.destroy(),
                    self.refresh_history(),
                ),
            )

        buttons = ttk.Frame(form)
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(
            buttons,
            text="タイムライン",
            command=lambda: self._show_timeline(recording_id),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="キャンセル", command=dialog.destroy).pack(side="left", padx=(0, 8))
        self._icon_button(
            buttons, "save", "対戦記録を保存", save, style="Primary.TButton"
        ).pack(side="left")
        dialog.grab_set()

    def _show_history_diagnostic(self, entry: object) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("録画診断")
        dialog.geometry("760x480")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"録画ID: {entry.recording_id}", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 10)
        )
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
        ttk.Button(frame, text="閉じる", command=dialog.destroy).pack(anchor="e", pady=(10, 0))
        dialog.grab_set()

    def _recording_opened(self, action: str, reference: RecordingReference) -> None:
        self._activity(f"{action}: {reference.recording_id}")
        for warning in reference.warnings:
            self._activity(f"注意: {warning}")

    def check_history(self) -> None:
        self._run(self.service.check_history, lambda issues: self._activity(f"履歴の不整合: {len(issues)}件"))

    def refresh_recovery(self) -> None:
        if self.smoke_mode:
            return
        self._run(self.service.list_recovery, self._recovery_loaded)

    def _recovery_loaded(self, entries: tuple[object, ...]) -> None:
        self._clear_tree(self.recovery_tree)
        for entry in entries:
            self.recovery_tree.insert("", "end", iid=entry.recording_id, values=(entry.recovery_state, entry.failure_code or "-", entry.output_path, entry.recording_id))

    def detect_recovery(self) -> None:
        self._run(self.service.detect_recovery, lambda items: (self._activity(f"中断候補を{len(items)}件確認しました"), self.refresh_recovery()))

    def inspect_selected_recovery(self) -> None:
        recording_id = self._selected_id(self.recovery_tree)
        if recording_id:
            self._run(lambda: self.service.inspect_recovery(recording_id), lambda result: self.recovery_result_var.set(f"{result.status.value}: {result.message}"))

    def repair_selected_recovery(self, dry_run: bool) -> None:
        recording_id = self._selected_id(self.recovery_tree)
        if not recording_id:
            return
        if not dry_run and not messagebox.askyesno("修復の確認", "元録画を保持し、別ファイルへ修復します。続行しますか？", parent=self.root):
            return
        self._run(lambda: self.service.repair_recovery(recording_id, dry_run=dry_run), lambda result: (self.recovery_result_var.set(result.message), self.refresh_recovery()))

    def refresh_preparations(self) -> None:
        if self.smoke_mode:
            return
        self._run(self.service.list_preparations, self._preparations_loaded)

    def _preparations_loaded(self, items: tuple[object, ...]) -> None:
        self._clear_tree(self.prepare_tree)
        for item in items:
            self.prepare_tree.insert("", "end", iid=item.queue_id, values=(item.state.value, item.metadata.title, item.recording_id, item.queue_id))

    def enqueue_preparation(self) -> None:
        recording_id = self.prepare_recording_var.get().strip()
        title = self.prepare_title_var.get().strip()
        if not recording_id or not title:
            self._show_error(ValueError("録画IDとタイトルを入力してください"))
            return
        self._run(lambda: self.service.enqueue_preparation(recording_id, title=title), lambda _item: (self._activity("準備キューへ追加しました"), self.refresh_preparations()))

    def process_preparations(self) -> None:
        self._run(self.service.process_preparations, lambda results: (self._activity(f"MP4準備を{len(results)}件処理しました"), self.refresh_preparations()))

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
        type_labels = {"system": "ゲーム・システム", "microphone": "マイク", "unknown": "音声"}
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
            selected if isinstance(selected, str) else selected.identifier if selected is not None else ""
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
        values["detection.auto_start_recording"] = str(self.auto_start_var.get()).lower()
        values["detection.auto_stop_recording"] = str(self.auto_stop_var.get()).lower()
        values["detection.visual_events_enabled"] = str(
            self.visual_detection_var.get()
        ).lower()
        self._run(lambda: self.service.save_settings(values), lambda _config: (self.settings_status_var.set("設定を保存しました"), self.run_diagnosis()))

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
            if not messagebox.askyesno("終了の確認", "実行中の録画または監視を正常停止して終了しますか？", parent=self.root):
                return
        self.closing = True
        self.busy_label.configure(text="終了処理中")
        self.tasks.submit(self.service.close, callback=lambda _value: self._destroy(), error_callback=lambda _error: self._destroy())

    def _destroy(self) -> None:
        self.tasks.close()
        self.root.destroy()

    def _poll_runtime(self) -> None:
        while True:
            try:
                event = self.watch_events.get_nowait()
            except queue.Empty:
                break
            if (
                event.kind == "visual"
                and event.state == "waiting"
                and event.message.startswith(WAITING_ACTIVITY_PREFIX)
            ):
                self._activity(event.message, replace_prefix=WAITING_ACTIVITY_PREFIX)
            else:
                if event.kind == "visual" or (
                    event.kind == "watch" and event.state == "stopped"
                ):
                    self._remove_activity(WAITING_ACTIVITY_PREFIX)
                self._activity(event.message)
            if event.kind == "started":
                self.record_state_var.set("自動録画中")
                self.record_detail_var.set(f"録画ID: {event.recording_id or '-'}\n保存先: 履歴で確認")
            elif event.kind in {"stopped", "error"}:
                self.record_state_var.set("自動監視中" if self.service.watch_active else "待機中")
                if event.kind == "stopped" and event.recording_id:
                    self._activity(
                        f"対戦記録は未入力です。録画履歴から編集できます: {event.recording_id}"
                    )
            elif event.kind == "watch" and event.state == "stopped" and not self.closing:
                self._watch_stopped()
            elif event.kind == "visual":
                self.visual_status_var.set(f"自動判定: {event.message}")
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
                self.visual_status_var.set(
                    "自動判定: "
                    f"{status.message} / 候補 {status.candidate_count} / "
                    f"処理 {status.processed_frames} / 破棄 {status.dropped_frames}"
                )
            self.root.after(500, self._poll_runtime)

    def _render_recording(self, snapshot: RecordingSnapshot) -> None:
        active = snapshot.active
        labels = {
            RecordingState.RECORDING: "録画中",
            RecordingState.STARTING: "開始処理中",
            RecordingState.STOPPING: "停止処理中",
            RecordingState.COMPLETED: "待機中",
            RecordingState.FAILED: "録画失敗",
            RecordingState.CREATED: "待機中",
        }
        self.record_state_var.set(labels[snapshot.state])
        self.elapsed_var.set(_format_duration(snapshot.elapsed_seconds))
        self.record_detail_var.set(f"録画ID: {snapshot.recording_id or '-'}\n保存先: {snapshot.output_path or '-'}")
        self.start_button.configure(state="disabled" if active or self.service.watch_active else "normal")
        self.stop_button.configure(state="normal" if active else "disabled")
        self.watch_button.configure(state="disabled" if active else "normal")

    def _set_record_controls(self, *, starting: bool) -> None:
        self.start_button.configure(state="disabled" if starting else "normal")
        self.stop_button.configure(state="disabled")
        self.watch_button.configure(state="disabled" if starting else "normal")
        if starting:
            self.record_state_var.set("開始処理中")

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
        self.target_combo.configure(values=("Master Duelウィンドウ", "デスクトップ全体"))
        self.target_var.set("Master Duelウィンドウ")
        for state, message in (("OK", "設定: 既定値を利用可能"), ("OK", "保存先: 書き込み可能"), ("注意", "FFmpeg: 実環境で診断してください")):
            self.diagnosis_tree.insert("", "end", values=(state, message))
        self._activity("GUI起動スモーク")
        self.connection_label.configure(text="GUI確認中", foreground="#ffcc80")


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


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
            args.smoke_output.write_text(json.dumps(geometry, ensure_ascii=False), encoding="utf-8")
        root.after(700, app.request_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
