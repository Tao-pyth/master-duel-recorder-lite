from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
import queue
import sys
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, TypeVar

from . import __version__
from .application import ApplicationEvent, RecorderApplicationService, RecordingSnapshot
from .capture_targets import CaptureTarget
from .preflight import CheckStatus, PreflightReport
from .recording_session import RecordingState
from .runtime_paths import application_project_root


T = TypeVar("T")


@dataclass(frozen=True)
class UiResult:
    callback: Callable[[object], None] | None
    error_callback: Callable[[BaseException], None] | None
    value: object | None = None
    error: BaseException | None = None


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
        self.current_page = "record"
        self.watch_events: queue.Queue[ApplicationEvent] = queue.Queue()
        self.busy_operations = 0
        self.closing = False

        self._configure_window()
        self._configure_styles()
        self._build_shell()
        self._build_record_page()
        self._build_history_page()
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
        refresh = ttk.Button(target_panel, text="更新", command=self.refresh_targets)
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
        button_row = ttk.Frame(controls, style="Surface.TFrame")
        button_row.grid(row=0, column=1, rowspan=3, sticky="e")
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
        ttk.Button(toolbar, text="更新", command=self.refresh_history).pack(side="right", padx=(0, 8))
        panel = self._surface(page, padding=(0, 0))
        panel.pack(fill="both", expand=True)
        columns = ("started", "state", "duration", "size", "id")
        self.history_tree = ttk.Treeview(panel, columns=columns, show="headings")
        for key, label, width in (
            ("started", "開始日時", 170),
            ("state", "状態", 90),
            ("duration", "時間", 85),
            ("size", "サイズ", 100),
            ("id", "録画ID", 300),
        ):
            self.history_tree.heading(key, text=label)
            self.history_tree.column(key, width=width, stretch=key == "id")
        self.history_tree.pack(fill="both", expand=True)
        self.widgets["history_table"] = self.history_tree

    def _build_recovery_page(self) -> None:
        page = self._new_page("recovery")
        toolbar = self._surface(page, padding=(14, 10))
        toolbar.pack(fill="x", pady=(0, 10))
        ttk.Label(toolbar, text="中断録画の復旧", style="Heading.TLabel").pack(side="left")
        ttk.Button(toolbar, text="中断検出", command=self.detect_recovery).pack(side="right")
        ttk.Button(toolbar, text="更新", command=self.refresh_recovery).pack(side="right", padx=(0, 8))
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
        ttk.Label(panel, text="録画設定", style="Heading.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        self.setting_vars = {
            "recorder.ffmpeg_path": tk.StringVar(),
            "recorder.audio_input": tk.StringVar(),
            "recorder.frame_rate": tk.StringVar(),
            "recorder.video_bitrate_kbps": tk.StringVar(),
            "recorder.capture_width": tk.StringVar(),
            "recorder.capture_height": tk.StringVar(),
        }
        fields = (
            ("recorder.ffmpeg_path", "FFmpeg", 1, 0, 3),
            ("recorder.audio_input", "音声入力（空欄で無効）", 3, 0, 3),
            ("recorder.frame_rate", "フレームレート", 5, 0, 1),
            ("recorder.video_bitrate_kbps", "映像ビットレート（kbps）", 5, 1, 1),
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
        self.auto_start_var = tk.BooleanVar(value=True)
        self.auto_stop_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(panel, text="ウィンドウ検出時に自動開始", variable=self.auto_start_var).grid(row=9, column=0, sticky="w", pady=(18, 0))
        ttk.Checkbutton(panel, text="ウィンドウ消失時に自動停止", variable=self.auto_stop_var).grid(row=9, column=1, sticky="w", pady=(18, 0))
        footer = ttk.Frame(panel, style="Surface.TFrame")
        footer.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(24, 0))
        self.settings_status_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.settings_status_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(footer, text="設定を再読込", command=self.load_settings).pack(side="right")
        ttk.Button(footer, text="保存", style="Primary.TButton", command=self.save_settings).pack(side="right", padx=(0, 8))
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(2, weight=1)
        self.widgets["settings_form"] = panel

    def show_page(self, key: str) -> None:
        titles = {
            "record": "録画",
            "history": "録画履歴",
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

    def start_recording(self) -> None:
        target = self.targets_by_label.get(self.target_var.get())
        self._set_record_controls(starting=True)
        self._run(lambda: self.service.start_recording(target), self._recording_started, self._recording_failed)

    def _recording_started(self, snapshot: RecordingSnapshot) -> None:
        self._render_recording(snapshot)
        self._activity(f"録画を開始しました: {snapshot.recording_id}")

    def _recording_failed(self, error: BaseException) -> None:
        self._set_record_controls(starting=False)
        self._show_error(error)

    def stop_recording(self) -> None:
        self.stop_button.configure(state="disabled")
        self._run(self.service.stop_recording, self._recording_stopped)

    def _recording_stopped(self, snapshot: RecordingSnapshot) -> None:
        self._render_recording(snapshot)
        self._activity(f"録画を停止しました: {snapshot.output_path}")

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
        self._run(self.service.list_history, self._history_loaded)

    def _history_loaded(self, entries: tuple[object, ...]) -> None:
        self._clear_tree(self.history_tree)
        for entry in entries:
            started = entry.started_at or entry.created_at
            duration = f"{entry.duration_seconds:.1f}秒" if entry.duration_seconds is not None else "-"
            size = _format_bytes(entry.size_bytes)
            self.history_tree.insert("", "end", iid=entry.recording_id, values=(started.astimezone().strftime("%Y-%m-%d %H:%M:%S"), entry.state, duration, size, entry.recording_id))

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
            "recorder.frame_rate": str(config.frame_rate),
            "recorder.video_bitrate_kbps": str(config.video_bitrate_kbps),
            "recorder.capture_width": str(config.capture_width),
            "recorder.capture_height": str(config.capture_height),
        }
        for key, value in values.items():
            self.setting_vars[key].set(value)
        self.auto_start_var.set(config.auto_start_recording)
        self.auto_stop_var.set(config.auto_stop_recording)
        self.settings_status_var.set("設定を読み込みました")

    def save_settings(self) -> None:
        values = {key: value.get() for key, value in self.setting_vars.items()}
        values["detection.auto_start_recording"] = str(self.auto_start_var.get()).lower()
        values["detection.auto_stop_recording"] = str(self.auto_stop_var.get()).lower()
        self._run(lambda: self.service.save_settings(values), lambda _config: (self.settings_status_var.set("設定を保存しました"), self.run_diagnosis()))

    def request_close(self) -> None:
        if self.closing:
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
            self._activity(event.message)
            if event.kind == "started":
                self.record_state_var.set("自動録画中")
                self.record_detail_var.set(f"録画ID: {event.recording_id or '-'}\n保存先: 履歴で確認")
            elif event.kind in {"stopped", "error"}:
                self.record_state_var.set("自動監視中" if self.service.watch_active else "待機中")
            elif event.kind == "watch" and event.state == "stopped" and not self.closing:
                self._watch_stopped()
        if not self.service.watch_active and not self.closing:
            try:
                snapshot = self.service.recording_snapshot()
            except Exception as exc:
                self._activity(f"状態確認エラー: {exc}")
            else:
                self._render_recording(snapshot)
        if not self.closing:
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

    def _activity(self, message: str) -> None:
        self.activity_list.insert(0, message)
        if self.activity_list.size() > 100:
            self.activity_list.delete(100, "end")

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


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def build_gui_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Master Duel Recorder Lite GUI")
    parser.add_argument("--project-root", type=Path, default=application_project_root())
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
