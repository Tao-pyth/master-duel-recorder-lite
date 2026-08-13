from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
import time


@dataclass(frozen=True)
class NotificationMessage:
    event: str
    title: str
    message: str
    deduplication_key: str


class WindowsNotificationService:
    def __init__(
        self,
        *,
        enabled: bool = True,
        sender: Callable[[str, str], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        duplicate_window_seconds: float = 5.0,
    ) -> None:
        self.enabled = enabled
        self.sender = sender or _send_windows_notification
        self.monotonic = monotonic
        self.duplicate_window_seconds = duplicate_window_seconds
        self._sent: dict[str, float] = {}
        self._closed = False

    def notify(self, notification: NotificationMessage) -> bool:
        if not self.enabled or self._closed:
            return False
        now = self.monotonic()
        previous = self._sent.get(notification.deduplication_key)
        if previous is not None and now - previous < self.duplicate_window_seconds:
            return False
        self.sender(notification.title, notification.message)
        self._sent[notification.deduplication_key] = now
        return True

    def close(self) -> None:
        self._closed = True
        self._sent.clear()


def _send_windows_notification(title: str, message: str) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class NotifyIconData(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    data = NotifyIconData()
    data.cbSize = ctypes.sizeof(data)
    data.hWnd = ctypes.windll.user32.GetForegroundWindow()
    data.uID = 0x4D44524C
    data.uFlags = 0x10
    data.szInfo = message[:255]
    data.szInfoTitle = title[:63]
    data.dwInfoFlags = 0x1
    ctypes.windll.shell32.Shell_NotifyIconW(0x0, ctypes.byref(data))
    ctypes.windll.shell32.Shell_NotifyIconW(0x1, ctypes.byref(data))
    ctypes.windll.shell32.Shell_NotifyIconW(0x2, ctypes.byref(data))
