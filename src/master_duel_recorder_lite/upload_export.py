from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import re
import subprocess
import uuid

from .ffmpeg import CommandRunner, run_command
from .runtime_paths import RuntimePaths
from .upload_media import UploadMediaValidation, UploadMediaValidator


class UploadExportStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class UploadExportResult:
    status: UploadExportStatus
    source_path: Path
    output_path: Path | None
    partial_path: Path | None
    source_validation: UploadMediaValidation
    output_validation: UploadMediaValidation | None
    message: str
    diagnostic: str
    reused: bool = False

    @property
    def succeeded(self) -> bool:
        return self.status is UploadExportStatus.COMPLETED


class UploadExporter:
    def __init__(
        self,
        *,
        paths: RuntimePaths,
        ffmpeg_executable: Path,
        validator: UploadMediaValidator,
        runner: CommandRunner = run_command,
        timeout_seconds: float = 300.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds は0より大きい必要があります")
        self.paths = paths
        self.ffmpeg_executable = ffmpeg_executable.expanduser().resolve()
        self.validator = validator
        self.runner = runner
        self.timeout_seconds = timeout_seconds

    def export(
        self,
        *,
        recording_id: str,
        queue_id: str,
        source_path: Path,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> UploadExportResult:
        recording = _safe_identifier(recording_id, "recording_id")
        queue = _safe_identifier(queue_id, "queue_id")
        source = source_path.expanduser().resolve()
        try:
            source.relative_to(self.paths.recordings.resolve())
        except ValueError as exc:
            raise ValueError("source_path はrecordings配下である必要があります") from exc
        source_validation = self.validator.validate(source)
        if not source_validation.eligible:
            return UploadExportResult(
                UploadExportStatus.FAILED,
                source,
                None,
                None,
                source_validation,
                None,
                "元録画がアップロード準備の検証に失敗しました。",
                "; ".join(source_validation.errors),
            )
        destination_dir = (self.paths.exports / recording).resolve()
        destination_dir.relative_to(self.paths.exports.resolve())
        destination_dir.mkdir(parents=True, exist_ok=True)
        final_path = destination_dir / f"{queue}.mp4"
        if final_path.exists():
            existing_validation = self.validator.validate(final_path)
            if existing_validation.eligible:
                return UploadExportResult(
                    UploadExportStatus.COMPLETED,
                    source,
                    final_path,
                    None,
                    source_validation,
                    existing_validation,
                    "既存の検証済みエクスポートを再利用しました。",
                    "validated existing export",
                    reused=True,
                )
            return UploadExportResult(
                UploadExportStatus.FAILED,
                source,
                final_path,
                None,
                source_validation,
                existing_validation,
                "既存エクスポートが不正なため上書きしません。",
                "; ".join(existing_validation.errors),
            )
        partial_path = destination_dir / f".{queue}.{uuid.uuid4().hex}.partial.mp4"
        checker = cancel_requested or (lambda: False)
        if checker():
            return UploadExportResult(
                UploadExportStatus.CANCELLED,
                source,
                None,
                None,
                source_validation,
                None,
                "エクスポート開始前にキャンセルしました。",
                "cancelled before ffmpeg",
            )

        source_stat = source.stat()
        command = (
            str(self.ffmpeg_executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-n",
            str(partial_path),
        )
        try:
            result = self.runner(command, self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            return self._failed(source, partial_path, source_validation, "エクスポートがタイムアウトしました。", str(exc), source_stat)
        except OSError as exc:
            return self._failed(source, partial_path, source_validation, "FFmpegを実行できません。", str(exc), source_stat)
        if result.returncode != 0 or not partial_path.is_file() or partial_path.stat().st_size <= 0:
            diagnostic = result.stderr.strip()[-2000:] or f"exit code {result.returncode}"
            return self._failed(source, partial_path, source_validation, "エクスポートに失敗しました。", diagnostic, source_stat)
        if checker():
            _assert_unchanged(source, source_stat.st_size, source_stat.st_mtime_ns)
            return UploadExportResult(
                UploadExportStatus.CANCELLED,
                source,
                None,
                partial_path,
                source_validation,
                None,
                "検証前にキャンセルしました。部分出力は確定していません。",
                "cancelled after ffmpeg",
            )
        output_validation = self.validator.validate(partial_path)
        if not output_validation.eligible:
            return self._failed(
                source,
                partial_path,
                source_validation,
                "エクスポート結果の再検証に失敗しました。",
                "; ".join(output_validation.errors),
                source_stat,
                output_validation=output_validation,
            )
        _assert_unchanged(source, source_stat.st_size, source_stat.st_mtime_ns)
        os.replace(partial_path, final_path)
        return UploadExportResult(
            UploadExportStatus.COMPLETED,
            source,
            final_path,
            None,
            source_validation,
            output_validation,
            "検証済みMP4をエクスポートしました。",
            "remux and validation succeeded",
        )

    def _failed(
        self,
        source: Path,
        partial: Path,
        source_validation: UploadMediaValidation,
        message: str,
        diagnostic: str,
        source_stat: os.stat_result,
        *,
        output_validation: UploadMediaValidation | None = None,
    ) -> UploadExportResult:
        _assert_unchanged(source, source_stat.st_size, source_stat.st_mtime_ns)
        return UploadExportResult(
            UploadExportStatus.FAILED,
            source,
            None,
            partial if partial.exists() else None,
            source_validation,
            output_validation,
            message,
            diagnostic,
        )


def _safe_identifier(value: str, key: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None:
        raise ValueError(f"{key} はASCII英数字、ピリオド、ハイフン、アンダースコアで指定してください")
    return value


def _assert_unchanged(path: Path, size: int, modified_ns: int) -> None:
    current = path.stat()
    if current.st_size != size or current.st_mtime_ns != modified_ns:
        raise RuntimeError("エクスポート中に元録画が変更されたため結果を確定しません")
