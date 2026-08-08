from __future__ import annotations

from collections.abc import Sequence
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
import platform
from typing import Protocol

from .config import AppConfig
from .game_window import GameWindowMonitor, GameWindowStatus, WindowSnapshot, WindowsGameWindowBackend


class CaptureTargetError(RuntimeError):
    """録画対象を安全に列挙または解決できない場合のエラーです。"""


class CaptureMode(str, Enum):
    MASTER_DUEL = "master_duel"
    WINDOW = "window"
    MONITOR = "monitor"
    DESKTOP = "desktop"


@dataclass(frozen=True)
class MonitorSnapshot:
    identifier: str
    label: str
    left: int
    top: int
    width: int
    height: int
    primary: bool = False

    def __post_init__(self) -> None:
        if not self.identifier.strip() or not self.label.strip():
            raise ValueError("モニター識別子と表示名は空にできません")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("モニターの幅と高さは正数である必要があります")


@dataclass(frozen=True)
class CaptureTarget:
    mode: CaptureMode
    identifier: str
    label: str
    available: bool = True
    detail: str = ""
    window_handle: int | None = None
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        if not self.identifier.strip() or not self.label.strip():
            raise ValueError("録画対象の識別子と表示名は空にできません")
        if self.mode in {CaptureMode.WINDOW, CaptureMode.MASTER_DUEL} and self.available:
            if self.window_handle is None or self.window_handle <= 0:
                raise ValueError("ウィンドウ録画には有効なハンドルが必要です")
        if self.mode is CaptureMode.MONITOR:
            values = (self.left, self.top, self.width, self.height)
            if any(value is None for value in values):
                raise ValueError("モニター録画には座標とサイズが必要です")
            if self.width is None or self.height is None or self.width <= 0 or self.height <= 0:
                raise ValueError("モニター録画の幅と高さは正数である必要があります")


@dataclass(frozen=True)
class CaptureInput:
    input_format: str
    input_name: str
    options: tuple[str, ...] = ()
    label: str = ""

    def __post_init__(self) -> None:
        if self.input_format != "gdigrab":
            raise ValueError("Windows録画入力はgdigrabである必要があります")
        if not self.input_name or "\x00" in self.input_name:
            raise ValueError("録画入力名が不正です")
        if len(self.options) % 2:
            raise ValueError("録画入力オプションは名前と値の組である必要があります")


class CaptureTargetBackend(Protocol):
    def list_monitors(self) -> Sequence[MonitorSnapshot]: ...

    def list_windows(self) -> Sequence[WindowSnapshot]: ...


class WindowsCaptureTargetBackend:
    MONITORINFOF_PRIMARY = 0x00000001

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    def __init__(self) -> None:
        if platform.system() != "Windows":
            raise RuntimeError("録画対象の列挙はWindowsでのみ利用できます")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._window_backend = WindowsGameWindowBackend()

    def list_monitors(self) -> tuple[MonitorSnapshot, ...]:
        snapshots: list[MonitorSnapshot] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HMONITOR,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        def collect(handle: int, _dc: int, _rect: object, _parameter: int) -> bool:
            info = self.MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(info)
            if not self.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
                return True
            rect = info.rcMonitor
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            device = str(info.szDevice)
            snapshots.append(
                MonitorSnapshot(
                    identifier=device,
                    label=f"{device} ({width}x{height})",
                    left=int(rect.left),
                    top=int(rect.top),
                    width=width,
                    height=height,
                    primary=bool(info.dwFlags & self.MONITORINFOF_PRIMARY),
                )
            )
            return True

        callback = callback_type(collect)
        if not self.user32.EnumDisplayMonitors(0, 0, callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return tuple(sorted(snapshots, key=lambda item: (not item.primary, item.left, item.top)))

    def list_windows(self) -> tuple[WindowSnapshot, ...]:
        return self._window_backend.list_windows()


class CaptureTargetCatalog:
    def __init__(self, backend: CaptureTargetBackend | None = None) -> None:
        self.backend = backend or WindowsCaptureTargetBackend()

    def list_targets(
        self,
        *,
        master_duel_monitor: GameWindowMonitor | None = None,
    ) -> tuple[CaptureTarget, ...]:
        targets: list[CaptureTarget] = [
            CaptureTarget(CaptureMode.DESKTOP, "desktop", "デスクトップ全体", detail="すべての画面を録画します")
        ]
        for monitor in self.backend.list_monitors():
            targets.append(
                CaptureTarget(
                    CaptureMode.MONITOR,
                    f"monitor:{monitor.identifier}",
                    f"モニター: {monitor.label}",
                    detail="プライマリ" if monitor.primary else "",
                    left=monitor.left,
                    top=monitor.top,
                    width=monitor.width,
                    height=monitor.height,
                )
            )

        if master_duel_monitor is not None:
            observation = master_duel_monitor.observe()
            window = observation.window
            targets.insert(
                0,
                CaptureTarget(
                    CaptureMode.MASTER_DUEL,
                    "master_duel",
                    "Master Duelウィンドウ",
                    available=observation.status is GameWindowStatus.VISIBLE and window is not None,
                    detail=observation.message,
                    window_handle=window.handle if window is not None else None,
                    width=window.width if window is not None else None,
                    height=window.height if window is not None else None,
                ),
            )

        windows = [
            window
            for window in self.backend.list_windows()
            if window.visible
            and not window.minimized
            and window.width >= 160
            and window.height >= 120
            and window.title.strip()
            and window.title.strip().casefold() != "program manager"
        ]
        for window in sorted(windows, key=lambda item: (-item.area, item.handle)):
            title = window.title.strip()
            targets.append(
                CaptureTarget(
                    CaptureMode.WINDOW,
                    f"window:{window.handle}",
                    f"ウィンドウ: {title} (PID {window.pid}, HWND {window.handle})",
                    detail=f"{window.width}x{window.height}",
                    window_handle=window.handle,
                    width=window.width,
                    height=window.height,
                )
            )
        return tuple(targets)


def capture_input_for_target(target: CaptureTarget) -> CaptureInput:
    if not target.available:
        raise CaptureTargetError(f"録画対象を利用できません: {target.label}")
    if target.mode is CaptureMode.DESKTOP:
        return CaptureInput("gdigrab", "desktop", label=target.label)
    if target.mode in {CaptureMode.WINDOW, CaptureMode.MASTER_DUEL}:
        if target.window_handle is None or target.window_handle <= 0:
            raise CaptureTargetError("録画対象ウィンドウのハンドルが不正です")
        return CaptureInput("gdigrab", f"hwnd={target.window_handle}", label=target.label)
    if target.mode is CaptureMode.MONITOR:
        if None in {target.left, target.top, target.width, target.height}:
            raise CaptureTargetError("録画対象モニターの座標が不完全です")
        assert target.left is not None and target.top is not None
        assert target.width is not None and target.height is not None
        return CaptureInput(
            "gdigrab",
            "desktop",
            (
                "-offset_x",
                str(target.left),
                "-offset_y",
                str(target.top),
                "-video_size",
                f"{target.width}x{target.height}",
            ),
            target.label,
        )
    raise CaptureTargetError(f"未対応の録画対象です: {target.mode.value}")


def find_target(targets: Sequence[CaptureTarget], mode: CaptureMode, identifier: str) -> CaptureTarget:
    if mode in {CaptureMode.DESKTOP, CaptureMode.MASTER_DUEL}:
        identifier = mode.value
    for target in targets:
        if target.mode is mode and target.identifier == identifier:
            return target
    raise CaptureTargetError(f"設定された録画対象が見つかりません: {mode.value} / {identifier or '-'}")


def resolve_configured_capture(
    config: AppConfig,
    *,
    master_duel_window_handle: int | None = None,
    catalog: CaptureTargetCatalog | None = None,
) -> CaptureInput:
    try:
        mode = CaptureMode(config.capture_mode)
    except ValueError as exc:
        raise CaptureTargetError(f"未対応の録画対象モードです: {config.capture_mode}") from exc

    if mode is CaptureMode.DESKTOP:
        return capture_input_for_target(CaptureTarget(mode, "desktop", "デスクトップ全体"))
    if mode is CaptureMode.MASTER_DUEL:
        window_handle = master_duel_window_handle
        window_title = ""
        if window_handle is None:
            observation = GameWindowMonitor(
                process_name=config.game_process_name,
                title_contains=config.game_window_title_contains,
            ).observe()
            if observation.status is not GameWindowStatus.VISIBLE or observation.window is None:
                raise CaptureTargetError(f"Master Duelウィンドウを録画できません: {observation.message}")
            window_handle = observation.window.handle
            window_title = observation.window.title
        return capture_input_for_target(
            CaptureTarget(
                mode,
                "master_duel",
                f"Master Duel: {window_title or '選択済み'}",
                window_handle=window_handle,
            )
        )

    target_catalog = catalog or CaptureTargetCatalog()
    targets = target_catalog.list_targets()
    return capture_input_for_target(find_target(targets, mode, config.capture_target_id))
