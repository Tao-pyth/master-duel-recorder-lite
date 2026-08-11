from __future__ import annotations

from collections.abc import Sequence
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
import platform
from typing import Protocol


DEFAULT_MASTER_DUEL_PROCESS_NAME = "masterduel.exe"
WINDOWS_MAX_PATH = 260
DEFAULT_DPI = 96


def scale_for_dpi(value: int, dpi: int) -> int:
    effective_dpi = dpi if dpi > 0 else DEFAULT_DPI
    return round(value * effective_dpi / DEFAULT_DPI)


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    executable_name: str


@dataclass(frozen=True)
class WindowSnapshot:
    handle: int
    pid: int
    title: str
    visible: bool
    minimized: bool
    width: int
    height: int
    left: int = 0
    top: int = 0

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)


class GameWindowStatus(str, Enum):
    NOT_RUNNING = "not_running"
    RUNNING_NO_WINDOW = "running_no_window"
    MINIMIZED = "minimized"
    VISIBLE = "visible"
    ERROR = "error"


@dataclass(frozen=True)
class GameWindowObservation:
    status: GameWindowStatus
    process: ProcessSnapshot | None
    window: WindowSnapshot | None
    candidate_count: int
    message: str


class GameWindowBackend(Protocol):
    def list_processes(self) -> Sequence[ProcessSnapshot]: ...

    def list_windows(self) -> Sequence[WindowSnapshot]: ...


class GameWindowMonitor:
    def __init__(
        self,
        *,
        process_name: str = DEFAULT_MASTER_DUEL_PROCESS_NAME,
        title_contains: str = "",
        backend: GameWindowBackend | None = None,
    ) -> None:
        normalized_process_name = process_name.strip()
        if not normalized_process_name:
            raise ValueError("process_name は空にできません")
        self.process_name = normalized_process_name
        self.title_contains = title_contains.strip()
        self.backend = backend or WindowsGameWindowBackend()

    def observe(self) -> GameWindowObservation:
        try:
            processes = [
                process
                for process in self.backend.list_processes()
                if process.executable_name.casefold() == self.process_name.casefold()
            ]
            if not processes:
                return GameWindowObservation(
                    GameWindowStatus.NOT_RUNNING,
                    None,
                    None,
                    0,
                    f"{self.process_name}は起動していません",
                )

            process_by_pid = {process.pid: process for process in processes}
            windows = [window for window in self.backend.list_windows() if window.pid in process_by_pid]
            if self.title_contains:
                windows = [
                    window for window in windows if self.title_contains.casefold() in window.title.casefold()
                ]
            if not windows:
                return GameWindowObservation(
                    GameWindowStatus.RUNNING_NO_WINDOW,
                    processes[0],
                    None,
                    0,
                    "プロセスは起動中ですが対象ウィンドウが見つかりません",
                )

            visible_windows = [
                window
                for window in windows
                if window.visible and not window.minimized and window.width > 0 and window.height > 0
            ]
            if visible_windows:
                selected = max(visible_windows, key=lambda window: (window.area, window.handle))
                return GameWindowObservation(
                    GameWindowStatus.VISIBLE,
                    process_by_pid[selected.pid],
                    selected,
                    len(windows),
                    f"対象ウィンドウを確認しました: {selected.title or '(タイトルなし)'}",
                )

            minimized_windows = [window for window in windows if window.minimized]
            if minimized_windows:
                selected = max(minimized_windows, key=lambda window: (window.area, window.handle))
                return GameWindowObservation(
                    GameWindowStatus.MINIMIZED,
                    process_by_pid[selected.pid],
                    selected,
                    len(windows),
                    "対象ウィンドウは最小化されています",
                )

            return GameWindowObservation(
                GameWindowStatus.RUNNING_NO_WINDOW,
                processes[0],
                None,
                len(windows),
                "対象ウィンドウは表示できる状態ではありません",
            )
        except (OSError, RuntimeError) as exc:
            return GameWindowObservation(
                GameWindowStatus.ERROR,
                None,
                None,
                0,
                f"プロセスまたはウィンドウを取得できません: {exc}",
            )


class WindowsGameWindowBackend:
    TH32CS_SNAPPROCESS = 0x00000002
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * WINDOWS_MAX_PATH),
        ]

    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("Windows以外のプロセス・ウィンドウ監視には対応していません")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._configure_signatures()
        self._physical_coordinates_enabled = self._enable_physical_pixel_coordinates()

    def list_processes(self) -> tuple[ProcessSnapshot, ...]:
        snapshot = self.kernel32.CreateToolhelp32Snapshot(self.TH32CS_SNAPPROCESS, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle:
            raise ctypes.WinError(ctypes.get_last_error())

        entry = self.PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        processes: list[ProcessSnapshot] = []
        try:
            success = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            if not success:
                error = ctypes.get_last_error()
                if error == 18:
                    return ()
                raise ctypes.WinError(error)
            while success:
                processes.append(ProcessSnapshot(int(entry.th32ProcessID), entry.szExeFile))
                success = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            error = ctypes.get_last_error()
            if error not in {0, 18}:
                raise ctypes.WinError(error)
        finally:
            self.kernel32.CloseHandle(snapshot)
        return tuple(processes)

    def list_windows(self) -> tuple[WindowSnapshot, ...]:
        windows: list[WindowSnapshot] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def collect_window(handle: int, _parameter: int) -> bool:
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            title_length = self.user32.GetWindowTextLengthW(handle)
            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            self.user32.GetWindowTextW(handle, title_buffer, len(title_buffer))
            rectangle = wintypes.RECT()
            has_rectangle = bool(self.user32.GetClientRect(handle, ctypes.byref(rectangle)))
            width = rectangle.right - rectangle.left if has_rectangle else 0
            height = rectangle.bottom - rectangle.top if has_rectangle else 0
            client_origin = wintypes.POINT(0, 0)
            has_origin = has_rectangle and bool(
                self.user32.ClientToScreen(handle, ctypes.byref(client_origin))
            )
            left = int(client_origin.x) if has_origin else 0
            top = int(client_origin.y) if has_origin else 0
            if has_origin and not self._physical_coordinates_enabled:
                dpi_reader = getattr(self.user32, "GetDpiForWindow", None)
                dpi = int(dpi_reader(handle)) if dpi_reader is not None else DEFAULT_DPI
                left = scale_for_dpi(left, dpi)
                top = scale_for_dpi(top, dpi)
                width = scale_for_dpi(width, dpi)
                height = scale_for_dpi(height, dpi)
            windows.append(
                WindowSnapshot(
                    handle=int(handle),
                    pid=int(pid.value),
                    title=title_buffer.value,
                    visible=bool(self.user32.IsWindowVisible(handle)),
                    minimized=bool(self.user32.IsIconic(handle)),
                    width=width,
                    height=height,
                    left=left,
                    top=top,
                )
            )
            return True

        callback = callback_type(collect_window)
        if not self.user32.EnumWindows(callback, 0):
            error = ctypes.get_last_error()
            if error:
                raise ctypes.WinError(error)
        return tuple(windows)

    def _configure_signatures(self) -> None:
        self.kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(self.PROCESSENTRY32W)]
        self.kernel32.Process32FirstW.restype = wintypes.BOOL
        self.kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(self.PROCESSENTRY32W)]
        self.kernel32.Process32NextW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetClientRect.restype = wintypes.BOOL
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.ClientToScreen.restype = wintypes.BOOL
        dpi_reader = getattr(self.user32, "GetDpiForWindow", None)
        if dpi_reader is not None:
            dpi_reader.argtypes = [wintypes.HWND]
            dpi_reader.restype = wintypes.UINT

    def _enable_physical_pixel_coordinates(self) -> bool:
        setter = getattr(self.user32, "SetThreadDpiAwarenessContext", None)
        if setter is None:
            return False
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        return bool(setter(self.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
