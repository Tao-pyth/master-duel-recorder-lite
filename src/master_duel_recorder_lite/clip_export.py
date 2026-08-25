from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import re
import subprocess
import uuid

from .ffmpeg import CommandRunner, run_command
from .recording_history import RecordingHistoryRepository
from .runtime_paths import RuntimePaths
from .upload_media import UploadMediaValidator


class ClipExportError(RuntimeError):
    """投稿用クリップを安全に出力できない場合のエラーです。"""


@dataclass(frozen=True)
class ClipRange:
    start_seconds: float
    duration_seconds: float


@dataclass(frozen=True)
class ClipExportResult:
    recording_id: str
    output_path: Path
    clip_range: ClipRange


def resolve_clip_range(
    *,
    center_seconds: float,
    before_seconds: float = 30.0,
    after_seconds: float = 30.0,
    duration_seconds: float | None,
) -> ClipRange:
    for key, value in (
        ("center_seconds", center_seconds),
        ("before_seconds", before_seconds),
        ("after_seconds", after_seconds),
    ):
        if isinstance(value, bool) or value < 0:
            raise ClipExportError(f"{key} は0以上の数値である必要があります")
    start = max(0.0, center_seconds - before_seconds)
    end = center_seconds + after_seconds
    if duration_seconds is not None:
        if duration_seconds <= 0:
            raise ClipExportError("録画長は0より大きい必要があります")
        end = min(duration_seconds, end)
    clip_duration = max(0.1, end - start)
    return ClipRange(round(start, 3), round(clip_duration, 3))


class ClipExportService:
    def __init__(
        self,
        *,
        paths: RuntimePaths,
        repository: RecordingHistoryRepository,
        ffmpeg_executable: Path,
        validator: UploadMediaValidator,
        runner: CommandRunner = run_command,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.paths = paths
        self.repository = repository
        self.ffmpeg_executable = ffmpeg_executable.expanduser().resolve()
        self.validator = validator
        self.runner = runner
        self.timeout_seconds = timeout_seconds

    def export_clip(
        self,
        *,
        recording_id: str,
        center_seconds: float,
        before_seconds: float = 30.0,
        after_seconds: float = 30.0,
    ) -> ClipExportResult:
        identifier = _safe_identifier(recording_id)
        history = self.repository.get(identifier)
        if history is None:
            raise ClipExportError(f"録画履歴が見つかりません: {identifier}")
        if history.state != "completed":
            raise ClipExportError(f"正常完了した録画だけをクリップ出力できます: {history.state}")
        source = (self.paths.recordings / history.output_path).resolve()
        source.relative_to(self.paths.recordings.resolve())
        if not source.is_file() or source.stat().st_size <= 0:
            raise ClipExportError(f"元録画が存在しないか空です: {source}")
        validation = self.validator.validate(source)
        if not validation.eligible:
            raise ClipExportError("元録画を検証できません: " + "; ".join(validation.errors))
        clip_range = resolve_clip_range(
            center_seconds=center_seconds,
            before_seconds=before_seconds,
            after_seconds=after_seconds,
            duration_seconds=history.duration_seconds or validation.duration_seconds,
        )
        output_dir = self.paths.exports / identifier / "clips"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / (
            f"{identifier}-{int(center_seconds * 1000):010d}-{uuid.uuid4().hex[:8]}.mp4"
        )
        partial = output.with_name(f".{output.stem}.partial{output.suffix}")
        source_stat = source.stat()
        command = (
            str(self.ffmpeg_executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{clip_range.start_seconds:.3f}",
            "-i",
            str(source),
            "-t",
            f"{clip_range.duration_seconds:.3f}",
            "-map",
            "0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-n",
            str(partial),
        )
        try:
            completed = self.runner(command, self.timeout_seconds)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ClipExportError(f"クリップ出力に失敗しました: {exc}") from exc
        _assert_unchanged(source, source_stat)
        if completed.returncode != 0 or not partial.is_file() or partial.stat().st_size <= 0:
            diagnostic = completed.stderr.strip()[-1000:] or f"exit code {completed.returncode}"
            raise ClipExportError(f"クリップ出力に失敗しました: {diagnostic}")
        output_validation = self.validator.validate(partial)
        if not output_validation.eligible:
            raise ClipExportError(
                "クリップ出力の検証に失敗しました: " + "; ".join(output_validation.errors)
            )
        os.replace(partial, output)
        return ClipExportResult(identifier, output, clip_range)


def _safe_identifier(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None:
        raise ClipExportError("recording_id はASCII英数字、ピリオド、ハイフン、アンダースコアで指定してください")
    return value


def _assert_unchanged(path: Path, stat: os.stat_result) -> None:
    current = path.stat()
    if current.st_size != stat.st_size or current.st_mtime_ns != stat.st_mtime_ns:
        raise ClipExportError("クリップ出力中に元録画が変更されたため結果を確定しません")
