from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import BinaryIO


class RecordingBusyError(RuntimeError):
    """別の録画セッションがOSロックを保持しているときのエラーです。"""


class RecordingLock:
    def __init__(self, path: Path, handle: BinaryIO) -> None:
        self.path = path
        self._handle = handle
        self._released = False

    @classmethod
    def acquire(cls, path: Path, *, recording_id: str) -> RecordingLock:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            _lock_one_byte(handle)
        except OSError as exc:
            handle.close()
            raise RecordingBusyError("別の録画セッションが実行中です") from exc

        metadata = {
            "recording_id": recording_id,
            "pid": os.getpid(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            handle.seek(0)
            handle.truncate()
            handle.write((json.dumps(metadata, ensure_ascii=False) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
            handle.seek(0)
        except (OSError, TypeError, ValueError):
            try:
                try:
                    handle.seek(0)
                except OSError:
                    pass
                try:
                    _unlock_one_byte(handle)
                except OSError:
                    pass
            finally:
                handle.close()
            raise
        return cls(path=path, handle=handle)

    def release(self) -> None:
        if self._released:
            return
        try:
            self._handle.seek(0)
            _unlock_one_byte(self._handle)
        finally:
            self._handle.close()
            self._released = True

    @property
    def released(self) -> bool:
        return self._released

    def __enter__(self) -> RecordingLock:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()


def _lock_one_byte(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_one_byte(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
