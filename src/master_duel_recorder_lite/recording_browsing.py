from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import platform
import subprocess

from .recording_history import RecordingHistoryRepository


class RecordingBrowseFailure(str, Enum):
    NOT_FOUND = "not_found"
    INVALID_REFERENCE = "invalid_reference"
    MISSING = "missing"
    EMPTY = "empty"
    UNSUPPORTED = "unsupported"
    PLATFORM = "platform"
    LAUNCH_FAILED = "launch_failed"


class RecordingBrowseError(RuntimeError):
    def __init__(self, kind: RecordingBrowseFailure, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class RecordingReference:
    recording_id: str
    path: Path
    warnings: tuple[str, ...] = ()


StartFile = Callable[[str], None]
ProcessLauncher = Callable[[Sequence[str]], object]


class RecordingBrowser:
    def __init__(
        self,
        *,
        repository: RecordingHistoryRepository,
        recordings_root: Path,
        system_name: str | None = None,
        start_file: StartFile | None = None,
        process_launcher: ProcessLauncher | None = None,
    ) -> None:
        self.repository = repository
        self.recordings_root = recordings_root.expanduser().resolve()
        self.system_name = system_name or platform.system()
        self._start_file = start_file or _start_file
        self._process_launcher = process_launcher or _launch_process

    def resolve(self, recording_id: str) -> RecordingReference:
        identifier = recording_id.strip()
        if not identifier:
            raise RecordingBrowseError(
                RecordingBrowseFailure.NOT_FOUND,
                "録画IDを指定してください",
            )
        entry = self.repository.get(identifier)
        if entry is None:
            raise RecordingBrowseError(
                RecordingBrowseFailure.NOT_FOUND,
                f"録画履歴が見つかりません: {identifier}",
            )
        relative = entry.output_path
        if relative.is_absolute() or ".." in relative.parts:
            raise RecordingBrowseError(
                RecordingBrowseFailure.INVALID_REFERENCE,
                f"録画履歴のファイル参照が不正です: {identifier}",
            )
        path = (self.recordings_root / relative).resolve()
        if not path.is_relative_to(self.recordings_root):
            raise RecordingBrowseError(
                RecordingBrowseFailure.INVALID_REFERENCE,
                f"録画ファイルが保存領域外を指しています: {identifier}",
            )
        if not path.is_file():
            raise RecordingBrowseError(
                RecordingBrowseFailure.MISSING,
                f"録画ファイルが見つかりません: {relative}",
            )
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise RecordingBrowseError(
                RecordingBrowseFailure.MISSING,
                f"録画ファイルを確認できません: {relative}: {exc}",
            ) from exc
        if size <= 0:
            raise RecordingBrowseError(
                RecordingBrowseFailure.EMPTY,
                f"録画ファイルが空です: {relative}",
            )
        if path.suffix.lower() not in {".mkv", ".mp4"}:
            raise RecordingBrowseError(
                RecordingBrowseFailure.UNSUPPORTED,
                f"再生対象はMKVまたはMP4である必要があります: {relative}",
            )
        warnings: list[str] = []
        if entry.size_bytes is not None and entry.size_bytes != size:
            warnings.append(
                f"履歴のサイズ{entry.size_bytes}バイトと実ファイル{size}バイトが一致しません"
            )
        return RecordingReference(identifier, path, tuple(warnings))

    def play(self, recording_id: str) -> RecordingReference:
        reference = self.resolve(recording_id)
        self._require_windows()
        try:
            self._start_file(str(reference.path))
        except OSError as exc:
            raise RecordingBrowseError(
                RecordingBrowseFailure.LAUNCH_FAILED,
                f"Windows既定プレイヤーで録画を開けません: {exc}",
            ) from exc
        return reference

    def reveal(self, recording_id: str) -> RecordingReference:
        reference = self.resolve(recording_id)
        self._require_windows()
        try:
            self._process_launcher(("explorer.exe", f"/select,{reference.path}"))
        except OSError as exc:
            raise RecordingBrowseError(
                RecordingBrowseFailure.LAUNCH_FAILED,
                f"Explorerで録画の保存場所を開けません: {exc}",
            ) from exc
        return reference

    def _require_windows(self) -> None:
        if self.system_name != "Windows":
            raise RecordingBrowseError(
                RecordingBrowseFailure.PLATFORM,
                "録画の再生と保存場所表示はWindowsでのみ利用できます",
            )


def _start_file(path: str) -> None:
    if not hasattr(os, "startfile"):
        raise OSError("Windowsのファイル関連付けを利用できません")
    os.startfile(path)  # type: ignore[attr-defined]


def _launch_process(arguments: Sequence[str]) -> object:
    return subprocess.Popen(tuple(arguments), close_fds=True)
