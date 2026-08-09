from __future__ import annotations

import ctypes
from collections.abc import Callable
import platform
import subprocess
import threading
import time
from typing import Protocol, TypeVar


WINDOWS_DLL_INIT_FAILED = 0xC0000142
SEM_FAILCRITICALERRORS = 0x0001
SEM_NOGPFAULTERRORBOX = 0x0002

_error_mode_lock = threading.Lock()
_error_mode_configured = False


class ProcessResult(Protocol):
    returncode: int


ResultT = TypeVar("ResultT", bound=ProcessResult)


def configure_windows_process_errors() -> None:
    global _error_mode_configured
    if platform.system() != "Windows" or _error_mode_configured:
        return
    with _error_mode_lock:
        if _error_mode_configured:
            return
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetErrorMode.restype = ctypes.c_uint
        current_mode = int(kernel32.GetErrorMode())
        kernel32.SetErrorMode(current_mode | SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX)
        _error_mode_configured = True


def subprocess_creation_flags() -> int:
    if platform.system() != "Windows":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))


def is_transient_windows_process_failure(returncode: int) -> bool:
    return (
        platform.system() == "Windows"
        and (returncode & 0xFFFFFFFF) == WINDOWS_DLL_INIT_FAILED
    )


def run_with_windows_retry(
    operation: Callable[[], ResultT],
    *,
    sleeper: Callable[[float], None] = time.sleep,
) -> ResultT:
    for attempt in range(2):
        result = operation()
        if not is_transient_windows_process_failure(result.returncode) or attempt == 1:
            return result
        sleeper(0.2)
    raise AssertionError("unreachable")
