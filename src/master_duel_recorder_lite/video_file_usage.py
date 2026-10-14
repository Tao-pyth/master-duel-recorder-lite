"""Windowsの外部プレイヤーを含む、対象動画の利用状態だけを照会します。"""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import stat
import sys
from typing import Iterator


_ERROR_SUCCESS = 0
_ERROR_MORE_DATA = 234
_MAX_LIST_CALLS = 4
_MAX_PROCESSES = 4096
_DWORD = ctypes.c_uint32
_UINT = ctypes.c_uint32


class _FileTime(ctypes.Structure):
    _fields_ = [("low", _DWORD), ("high", _DWORD)]


class _UniqueProcess(ctypes.Structure):
    _fields_ = [("process_id", _DWORD), ("start_time", _FileTime)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", _UniqueProcess),
        ("app_name", ctypes.c_wchar * 256),
        ("service_name", ctypes.c_wchar * 64),
        ("application_type", ctypes.c_int32),
        ("app_status", _DWORD),
        ("session_id", _DWORD),
        ("restartable", ctypes.c_int32),
    ]


@dataclass(frozen=True, slots=True)
class FileUseStatus:
    in_use: bool
    known: bool
    process_ids: tuple[int, ...]
    message: str


@contextmanager
def exclusive_delete_guard(path: Path) -> Iterator[None]:
    """破壊操作の短い区間で、新しい読込・書込ハンドルの開始を防ぎます。"""
    if sys.platform != "win32":
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, _DWORD, _DWORD, ctypes.c_void_p, _DWORD, _DWORD, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int32
    # 削除権限で保持します。内容を読まず、読込/書込の共有を拒否してrename/unlinkを許します。
    handle = kernel.CreateFileW(str(path.resolve(strict=True)), 0x00010000, 4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def query_file_use(path: Path) -> FileUseStatus:
    """利用者を停止せず照会します。known=Falseのときは削除を見送ってください。"""
    if sys.platform != "win32":
        # Windows以外は既存の利用lease・ファイル操作の失敗判定を維持します。
        return FileUseStatus(False, True, (), "Windows以外では利用leaseとOSのファイル排他を使用します")
    try:
        source = Path(path).resolve(strict=True)
        before = source.stat()
        if not stat.S_ISREG(before.st_mode):
            return _unknown("動画ファイルの利用状態を確認できません: 通常ファイルではありません")
        result = _query_windows(source)
        after = source.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            return _unknown("利用状態の確認中に動画ファイルが変更されました", result)
        return result
    except Exception as exc:
        return _unknown(f"動画ファイルの利用状態を確認できません: {exc}")


def _unknown(message: str, result: FileUseStatus | None = None) -> FileUseStatus:
    return FileUseStatus(result.in_use if result else False, False, result.process_ids if result else (), message)


def _load_restart_manager():
    # DLL参照はセッション終了まで保持し、照会ごとに独立したセッションを使います。
    library = ctypes.WinDLL("Rstrtmgr.dll", use_last_error=True)
    library.RmStartSession.argtypes = [ctypes.POINTER(_DWORD), _DWORD, ctypes.POINTER(ctypes.c_wchar)]
    library.RmStartSession.restype = _DWORD
    library.RmRegisterResources.argtypes = [
        _DWORD, _UINT, ctypes.POINTER(ctypes.c_wchar_p), _UINT,
        ctypes.POINTER(_UniqueProcess), _UINT, ctypes.POINTER(ctypes.c_wchar_p),
    ]
    library.RmRegisterResources.restype = _DWORD
    library.RmGetList.argtypes = [
        _DWORD, ctypes.POINTER(_UINT), ctypes.POINTER(_UINT),
        ctypes.POINTER(_ProcessInfo), ctypes.POINTER(_DWORD),
    ]
    library.RmGetList.restype = _DWORD
    library.RmEndSession.argtypes = [_DWORD]
    library.RmEndSession.restype = _DWORD
    return library


def _query_windows(path: Path) -> FileUseStatus:
    library = _load_restart_manager()
    session = _DWORD()
    session_key = ctypes.create_unicode_buffer(33)
    code = library.RmStartSession(ctypes.byref(session), 0, session_key)
    if code != _ERROR_SUCCESS:
        return _unknown(f"動画利用の確認セッションを開始できません (Win32 {code})")
    result = _unknown("動画利用の確認が完了しませんでした")
    try:
        resources = (ctypes.c_wchar_p * 1)(str(path))
        code = library.RmRegisterResources(session, 1, resources, 0, None, 0, None)
        if code != _ERROR_SUCCESS:
            result = _unknown(f"動画利用の照会対象を登録できません (Win32 {code})")
        else:
            result = _get_processes(library, session)
    except Exception as exc:
        result = _unknown(f"動画利用の照会に失敗しました: {exc}")
    finally:
        try:
            code = library.RmEndSession(session)
            if code != _ERROR_SUCCESS:
                result = _unknown(f"動画利用の確認セッションを終了できません (Win32 {code})", result)
        except Exception as exc:
            result = _unknown(f"動画利用の確認セッションを終了できません: {exc}", result)
    return result


def _get_processes(library, session: _DWORD) -> FileUseStatus:
    capacity = 0
    for _ in range(_MAX_LIST_CALLS):
        needed = _UINT()
        count = _UINT(capacity)
        reasons = _DWORD()
        processes = (_ProcessInfo * capacity)() if capacity else None
        code = library.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), processes, ctypes.byref(reasons))
        if code == _ERROR_MORE_DATA:
            if not 0 < needed.value <= _MAX_PROCESSES:
                return _unknown("動画利用者の一覧サイズを安全に確認できません")
            capacity = max(needed.value, capacity + 1)
            if capacity > _MAX_PROCESSES:
                return _unknown("動画利用者の一覧が確認上限を超えています")
            continue
        if code != _ERROR_SUCCESS:
            return _unknown(f"動画利用者の一覧を確認できません (Win32 {code})")
        if count.value > capacity or needed.value > count.value:
            return _unknown("動画利用者の一覧を全件確認できません")
        process_ids = tuple(sorted({int(processes[index].process.process_id) for index in range(count.value)}))
        if any(identifier in (0, 0xFFFFFFFF) for identifier in process_ids):
            return _unknown("動画利用者のprocess番号を確認できません")
        if not process_ids and reasons.value:
            return _unknown("動画利用の確認にWindowsの制約があり、利用者不在を確定できません")
        # 自分のprocessも省きません。開いたままの読込ハンドルも削除を止めます。
        if process_ids:
            return FileUseStatus(True, True, process_ids, "動画がプレイヤーなどで使用中のため削除を見送ります")
        return FileUseStatus(False, True, (), "動画ファイルを利用しているprocessはありません")
    return _unknown("動画利用者の一覧が変化し続け、確認を完了できません")
