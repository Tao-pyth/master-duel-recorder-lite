from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import shutil
import subprocess

from .ffmpeg import CommandRunner, run_command


class MediaValidationStatus(str, Enum):
    VALID = "valid"
    WARNING = "warning"
    INVALID = "invalid"


@dataclass(frozen=True)
class UploadMediaValidation:
    status: MediaValidationStatus
    path: Path
    container: str | None
    duration_seconds: float | None
    stream_types: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return self.status in {MediaValidationStatus.VALID, MediaValidationStatus.WARNING}


class UploadMediaValidator:
    def __init__(
        self,
        *,
        ffprobe_executable: Path,
        runner: CommandRunner = run_command,
        timeout_seconds: float = 30.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds は0より大きい必要があります")
        self.ffprobe_executable = ffprobe_executable.expanduser().resolve()
        self.runner = runner
        self.timeout_seconds = timeout_seconds

    def validate(self, path: Path) -> UploadMediaValidation:
        media_path = path.expanduser().resolve()
        if media_path.suffix.lower() not in {".mkv", ".mp4"}:
            return _invalid(media_path, "対応するコンテナはMKVまたはMP4です")
        if not media_path.is_file():
            return _invalid(media_path, "動画ファイルが存在しません")
        if media_path.stat().st_size <= 0:
            return _invalid(media_path, "動画ファイルが空です")
        command = (
            str(self.ffprobe_executable),
            "-v",
            "error",
            "-show_entries",
            "format=format_name,duration:stream=index,codec_type,codec_name",
            "-of",
            "json",
            str(media_path),
        )
        try:
            result = self.runner(command, self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return _invalid(media_path, "メディア検証がタイムアウトしました")
        except OSError as exc:
            return _invalid(media_path, f"ffprobeを実行できません: {exc}")
        if result.returncode != 0:
            diagnostic = result.stderr.strip()[-1000:] or f"exit code {result.returncode}"
            return _invalid(media_path, f"動画を解析できません: {diagnostic}")
        try:
            parsed = parse_upload_probe(media_path, result.stdout)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return _invalid(media_path, f"ffprobe結果が不正です: {exc}")
        container_names = set((parsed.container or "").split(","))
        expected = {"matroska", "webm"} if media_path.suffix.lower() == ".mkv" else {"mov", "mp4"}
        if not container_names.intersection(expected):
            return _invalid(
                media_path,
                f"拡張子とコンテナが一致しません: {media_path.suffix} / {parsed.container}",
            )
        return parsed


def find_ffprobe(ffmpeg_executable: Path) -> Path:
    executable_name = "ffprobe.exe" if ffmpeg_executable.suffix.lower() == ".exe" else "ffprobe"
    adjacent = ffmpeg_executable.with_name(executable_name)
    if adjacent.is_file():
        return adjacent.resolve()
    found = shutil.which(executable_name) or shutil.which("ffprobe")
    return Path(found).resolve() if found else adjacent.resolve()


def parse_upload_probe(path: Path, output: str) -> UploadMediaValidation:
    document = json.loads(output)
    if not isinstance(document, dict):
        raise ValueError("root must be an object")
    streams = document.get("streams")
    if not isinstance(streams, list):
        raise ValueError("streams must be an array")
    stream_types = tuple(
        item["codec_type"]
        for item in streams
        if isinstance(item, dict) and isinstance(item.get("codec_type"), str)
    )
    errors: list[str] = []
    warnings: list[str] = []
    if "video" not in stream_types:
        errors.append("映像ストリームがありません")
    if "audio" not in stream_types:
        warnings.append("音声ストリームがありません")
    format_value = document.get("format")
    if not isinstance(format_value, dict):
        raise ValueError("format must be an object")
    container = format_value.get("format_name")
    if not isinstance(container, str) or not container:
        raise ValueError("format_name is missing")
    duration_raw = format_value.get("duration")
    if duration_raw is None:
        errors.append("動画の長さを取得できません")
        duration = None
    else:
        duration = float(duration_raw)
        if duration <= 0:
            errors.append("動画の長さが0秒以下です")
    status = (
        MediaValidationStatus.INVALID
        if errors
        else MediaValidationStatus.WARNING
        if warnings
        else MediaValidationStatus.VALID
    )
    return UploadMediaValidation(
        status,
        path,
        container,
        duration,
        stream_types,
        tuple(warnings),
        tuple(errors),
    )


def _invalid(path: Path, error: str) -> UploadMediaValidation:
    return UploadMediaValidation(
        MediaValidationStatus.INVALID,
        path,
        None,
        None,
        (),
        (),
        (error,),
    )
