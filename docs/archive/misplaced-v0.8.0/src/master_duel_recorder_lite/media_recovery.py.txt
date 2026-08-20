from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import shutil
import subprocess
import uuid

from .ffmpeg import CommandResult, CommandRunner, run_command
from .recording_history import RecordingHistoryRepository


class MediaRecoveryError(RuntimeError):
    """録画ファイルを非破壊で検査または修復できない場合のエラーです。"""


class InspectionStatus(str, Enum):
    VALID = "valid"
    REPAIRABLE = "repairable"
    UNRECOVERABLE = "unrecoverable"
    RETRYABLE = "retryable"


@dataclass(frozen=True)
class MediaInspection:
    recording_id: str
    status: InspectionStatus
    path: Path
    message: str
    diagnostic: str
    duration_seconds: float | None = None
    stream_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class MediaRepairResult:
    recording_id: str
    succeeded: bool
    dry_run: bool
    original_path: Path
    output_path: Path
    message: str
    diagnostic: str


class MediaRecoveryService:
    def __init__(
        self,
        *,
        repository: RecordingHistoryRepository,
        ffmpeg_executable: Path,
        ffprobe_executable: Path | None = None,
        runner: CommandRunner = run_command,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds は0より大きい必要があります")
        self.repository = repository
        self.ffmpeg_executable = ffmpeg_executable.expanduser().resolve()
        self.ffprobe_executable = (
            ffprobe_executable.expanduser().resolve()
            if ffprobe_executable is not None
            else _find_ffprobe(self.ffmpeg_executable)
        )
        self.runner = runner
        self.timeout_seconds = timeout_seconds

    def inspect(self, recording_id: str) -> MediaInspection:
        entry = self.repository.get(recording_id)
        if entry is None or entry.state != "failed":
            raise MediaRecoveryError(f"復旧対象の失敗履歴が見つかりません: {recording_id}")
        path = (self.repository.recordings_root / entry.output_path).resolve()
        if not path.is_file():
            return self._record_inspection(
                recording_id,
                InspectionStatus.UNRECOVERABLE,
                path,
                "元の録画ファイルが存在しないため修復できません。",
                "source file is missing",
            )
        if path.stat().st_size <= 0:
            return self._record_inspection(
                recording_id,
                InspectionStatus.UNRECOVERABLE,
                path,
                "元の録画ファイルが空のため修復できません。",
                "source file is empty",
            )

        command = _probe_command(self.ffprobe_executable, path)
        try:
            result = self.runner(command, self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            return self._record_inspection(
                recording_id,
                InspectionStatus.RETRYABLE,
                path,
                "メディア検査がタイムアウトしました。再試行してください。",
                str(exc),
            )
        except OSError as exc:
            return self._record_inspection(
                recording_id,
                InspectionStatus.RETRYABLE,
                path,
                "メディア検査ツールを実行できません。設定を確認してください。",
                str(exc),
            )
        if result.returncode != 0:
            diagnostic = _diagnostic(result)
            return self._record_inspection(
                recording_id,
                InspectionStatus.REPAIRABLE,
                path,
                "コンテナを正常に読み取れません。別ファイルへの修復を試行できます。",
                diagnostic,
            )
        try:
            duration, stream_types = _parse_probe(result.stdout)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._record_inspection(
                recording_id,
                InspectionStatus.REPAIRABLE,
                path,
                "検査結果を確認できません。別ファイルへの修復を試行できます。",
                f"invalid ffprobe output: {exc}",
            )
        inspection = self._record_inspection(
            recording_id,
            InspectionStatus.VALID,
            path,
            "元の録画ファイルから映像情報を読み取れました。",
            "ffprobe completed successfully",
        )
        return MediaInspection(
            inspection.recording_id,
            inspection.status,
            inspection.path,
            inspection.message,
            inspection.diagnostic,
            duration,
            stream_types,
        )

    def repair(self, recording_id: str, *, dry_run: bool = False) -> MediaRepairResult:
        entry = self.repository.get(recording_id)
        if entry is None or entry.state != "failed":
            raise MediaRecoveryError(f"復旧対象の失敗履歴が見つかりません: {recording_id}")
        original = (self.repository.recordings_root / entry.output_path).resolve()
        if not original.is_file() or original.stat().st_size <= 0:
            raise MediaRecoveryError("元の録画ファイルが存在しないか空のため修復できません")
        output = original.with_name(
            f"{original.stem}.recovered.{uuid.uuid4().hex}{original.suffix.lower()}"
        )
        output.relative_to(self.repository.recordings_root)
        if dry_run:
            return MediaRepairResult(
                recording_id,
                False,
                True,
                original,
                output,
                "元ファイルを保持し、別ファイルへstream copyする予定です。",
                "dry-run",
            )

        original_stat = original.stat()
        command = (
            str(self.ffmpeg_executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(original),
            "-map",
            "0",
            "-c",
            "copy",
            "-n",
            str(output),
        )
        try:
            result = self.runner(command, self.timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._repair_failed(
                recording_id,
                original,
                output,
                str(exc),
                original_stat.st_size,
                original_stat.st_mtime_ns,
            )
        if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
            return self._repair_failed(
                recording_id,
                original,
                output,
                _diagnostic(result),
                original_stat.st_size,
                original_stat.st_mtime_ns,
            )

        try:
            probe = self.runner(_probe_command(self.ffprobe_executable, output), self.timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._repair_failed(
                recording_id,
                original,
                output,
                str(exc),
                original_stat.st_size,
                original_stat.st_mtime_ns,
            )
        if probe.returncode != 0:
            return self._repair_failed(
                recording_id,
                original,
                output,
                _diagnostic(probe),
                original_stat.st_size,
                original_stat.st_mtime_ns,
            )
        try:
            _parse_probe(probe.stdout)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._repair_failed(
                recording_id,
                original,
                output,
                f"invalid ffprobe output: {exc}",
                original_stat.st_size,
                original_stat.st_mtime_ns,
            )

        _assert_original_unchanged(original, original_stat.st_size, original_stat.st_mtime_ns)
        self.repository.add_recovery_artifact(
            artifact_id=uuid.uuid4().hex,
            recording_id=recording_id,
            output_path=output,
            kind="recovered",
            status="valid",
            size_bytes=output.stat().st_size,
            diagnostic="stream copy and ffprobe succeeded",
        )
        self.repository.set_recovery_state(
            recording_id,
            state="repaired",
            message="修復済み動画を別ファイルとして作成しました。元ファイルは保持しています。",
            diagnostic=f"recovered output: {output.name}",
            increment_attempts=True,
        )
        return MediaRepairResult(
            recording_id,
            True,
            False,
            original,
            output,
            "修復済み動画を別ファイルとして作成しました。",
            "stream copy and ffprobe succeeded",
        )

    def _record_inspection(
        self,
        recording_id: str,
        status: InspectionStatus,
        path: Path,
        message: str,
        diagnostic: str,
    ) -> MediaInspection:
        recovery_state = {
            InspectionStatus.VALID: "repairable",
            InspectionStatus.REPAIRABLE: "repairable",
            InspectionStatus.UNRECOVERABLE: "unrecoverable",
            InspectionStatus.RETRYABLE: "pending",
        }[status]
        self.repository.set_recovery_state(
            recording_id,
            state=recovery_state,
            message=message,
            diagnostic=diagnostic,
            increment_attempts=True,
        )
        return MediaInspection(recording_id, status, path, message, diagnostic)

    def _repair_failed(
        self,
        recording_id: str,
        original: Path,
        output: Path,
        diagnostic: str,
        original_size: int,
        original_modified_ns: int,
    ) -> MediaRepairResult:
        _assert_original_unchanged(original, original_size, original_modified_ns)
        if output.exists():
            self.repository.add_recovery_artifact(
                artifact_id=uuid.uuid4().hex,
                recording_id=recording_id,
                output_path=output,
                kind="partial",
                status="failed",
                size_bytes=output.stat().st_size if output.is_file() else None,
                diagnostic=diagnostic,
            )
        self.repository.set_recovery_state(
            recording_id,
            state="repairable",
            message="修復に失敗しました。元ファイルと部分成果物は保持しています。",
            diagnostic=diagnostic or "repair failed without diagnostic",
            increment_attempts=True,
        )
        return MediaRepairResult(
            recording_id,
            False,
            False,
            original,
            output,
            "修復に失敗しました。元ファイルは変更していません。",
            diagnostic,
        )


def _find_ffprobe(ffmpeg_executable: Path) -> Path:
    executable_name = "ffprobe.exe" if ffmpeg_executable.suffix.lower() == ".exe" else "ffprobe"
    adjacent = ffmpeg_executable.with_name(executable_name)
    if adjacent.is_file():
        return adjacent.resolve()
    found = shutil.which(executable_name) or shutil.which("ffprobe")
    return Path(found).resolve() if found else adjacent.resolve()


def _probe_command(executable: Path, path: Path) -> tuple[str, ...]:
    return (
        str(executable),
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=index,codec_type,codec_name",
        "-of",
        "json",
        str(path),
    )


def _parse_probe(output: str) -> tuple[float | None, tuple[str, ...]]:
    document = json.loads(output)
    if not isinstance(document, dict):
        raise ValueError("ffprobe root must be an object")
    streams = document.get("streams")
    if not isinstance(streams, list) or not streams:
        raise ValueError("media streams are missing")
    stream_types = tuple(
        item["codec_type"]
        for item in streams
        if isinstance(item, dict) and isinstance(item.get("codec_type"), str)
    )
    if "video" not in stream_types:
        raise ValueError("video stream is missing")
    format_value = document.get("format")
    duration = None
    if isinstance(format_value, dict) and format_value.get("duration") is not None:
        duration = float(format_value["duration"])
        if duration < 0:
            raise ValueError("duration is negative")
    return duration, stream_types


def _diagnostic(result: CommandResult) -> str:
    value = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    return value[-2000:]


def _assert_original_unchanged(path: Path, size_bytes: int, modified_ns: int) -> None:
    current = path.stat()
    if current.st_size != size_bytes or current.st_mtime_ns != modified_ns:
        raise MediaRecoveryError("復旧処理中に元ファイルが変更されたため結果を採用しません")
