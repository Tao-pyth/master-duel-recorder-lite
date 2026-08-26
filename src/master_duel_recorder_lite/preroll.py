from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import time
import uuid

from .capture_targets import CaptureInput
from .recording_profile import RecordingProfile
from .recording_session import (
    MonotonicClock,
    ProcessFactory,
    RecordingResult,
    RecordingSession,
    RecordingState,
    _default_process_factory,
)
from .runtime_paths import RuntimePaths
from .windows_process import configure_windows_process_errors, subprocess_creation_flags


CommandRunner = Callable[[Sequence[str]], tuple[int, str]]


@dataclass(frozen=True)
class FrozenPreroll:
    segments: tuple[Path, ...]
    offset_ms: int
    diagnostics: tuple[str, ...] = ()


class PrerollCaptureBuffer:
    """自動録画開始前の短い映像だけを一時segmentとして保持します。"""

    def __init__(
        self,
        *,
        command: Sequence[str],
        directory: Path,
        max_bytes: int,
        max_segments: int | None = None,
        segment_seconds: float = 1.0,
        process_factory: ProcessFactory = _default_process_factory,
        monotonic_clock: MonotonicClock = time.monotonic,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes は0より大きい必要があります")
        if segment_seconds <= 0:
            raise ValueError("segment_seconds は0より大きい必要があります")
        if max_segments is not None and max_segments <= 0:
            raise ValueError("max_segments は0より大きい必要があります")
        self.command = tuple(command)
        self.directory = directory
        self.max_bytes = max_bytes
        self.max_segments = max_segments
        self.segment_seconds = segment_seconds
        self._process_factory = process_factory
        self._monotonic_clock = monotonic_clock
        self._process = None
        self._started_at: float | None = None
        self._diagnostics: list[str] = []

    @property
    def active(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._diagnostics)

    def start(self) -> None:
        if self.active:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        configure_windows_process_errors()
        self._process = self._process_factory(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess_creation_flags(),
        )
        self._started_at = self._monotonic_clock()

    def poll(self) -> None:
        if self._process is None:
            return
        returncode = self._process.poll()
        if returncode is not None:
            self._diagnostics.append(f"プリロールバッファが終了しました: {returncode}")
            self._drain_stderr()

    def freeze(self, *, timeout_seconds: float = 5.0) -> FrozenPreroll:
        if self._process is not None and self._process.poll() is None:
            try:
                if self._process.stdin is not None:
                    self._process.stdin.write("q\n")
                    self._process.stdin.flush()
            except (OSError, ValueError) as exc:
                self._diagnostics.append(f"プリロール停止要求に失敗しました: {exc}")
            try:
                self._process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                try:
                    self._process.kill()
                except OSError as exc:
                    self._diagnostics.append(f"プリロールを強制終了できません: {exc}")
        self._drain_stderr()
        segments = self._select_segments()
        elapsed = 0.0
        if self._started_at is not None:
            elapsed = max(0.0, self._monotonic_clock() - self._started_at)
        offset_ms = round(min(elapsed, len(segments) * self.segment_seconds) * 1000)
        return FrozenPreroll(
            segments=segments,
            offset_ms=offset_ms,
            diagnostics=self.diagnostics,
        )

    def discard(self) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.kill()
            except OSError as exc:
                self._diagnostics.append(f"プリロール破棄時に停止できません: {exc}")
        shutil.rmtree(self.directory, ignore_errors=True)

    def _select_segments(self) -> tuple[Path, ...]:
        candidates = [path for path in self.directory.glob("segment_*") if path.is_file()]
        candidates.sort(key=lambda path: (path.stat().st_mtime_ns, path.name))
        selected: list[Path] = []
        total = 0
        for path in reversed(candidates):
            size = path.stat().st_size
            if size <= 0:
                continue
            if self.max_segments is not None and len(selected) >= self.max_segments:
                break
            if selected and total + size > self.max_bytes:
                break
            selected.append(path)
            total += size
        selected.reverse()
        return tuple(selected)

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr.readlines()[-20:]:
                normalized = line.strip()
                if normalized:
                    self._diagnostics.append(normalized[:1000])
        except (OSError, ValueError):
            return


class PrerollRecordingSession:
    """本録画停止後に凍結済みプリロールと本録画を結合するsession wrapperです。"""

    def __init__(
        self,
        *,
        main_session: RecordingSession,
        main_output_path: Path,
        final_output_path: Path,
        frozen_preroll: FrozenPreroll,
        executable: Path,
        recording_format: str,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.main_session = main_session
        self.main_output_path = main_output_path
        self.output_path = final_output_path
        self.frozen_preroll = frozen_preroll
        self.executable = executable
        self.recording_format = recording_format
        self._command_runner = command_runner or _run_command
        self._diagnostics: list[str] = list(frozen_preroll.diagnostics)
        self.state = RecordingState.CREATED
        self.result: RecordingResult | None = None

    @property
    def started_at(self):
        return self.main_session.started_at

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return (*self.main_session.diagnostics, *self._diagnostics)

    @property
    def audio_warning(self) -> str | None:
        return self.main_session.audio_warning

    def add_diagnostic(self, line: str) -> None:
        self.main_session.add_diagnostic(line)

    def start(self) -> RecordingState:
        self.state = self.main_session.start()
        return self.state

    def poll(self) -> RecordingState:
        self.state = self.main_session.poll()
        if self.state in {RecordingState.COMPLETED, RecordingState.FAILED}:
            self._finalize_result(self.main_session.result)
        return self.state

    def stop(self, *, timeout_seconds: float = 10.0) -> RecordingResult:
        main_result = self.main_session.stop(timeout_seconds=timeout_seconds)
        self._finalize_result(main_result)
        assert self.result is not None
        return self.result

    def _finalize_result(self, main_result: RecordingResult | None) -> None:
        if self.result is not None or main_result is None:
            return
        if not main_result.succeeded:
            self.result = main_result
            self.state = main_result.state
            return
        self._merge_or_fallback()
        size = self.output_path.stat().st_size if self.output_path.is_file() else 0
        self.state = RecordingState.COMPLETED if size > 0 else RecordingState.FAILED
        error = None if size > 0 else "プリロール結合後の録画出力が存在しません"
        self.result = RecordingResult(
            state=self.state,
            output_path=self.output_path,
            returncode=0 if size > 0 else None,
            started_at=main_result.started_at,
            ended_at=main_result.ended_at,
            size_bytes=size,
            error=error,
            diagnostics=self.diagnostics,
        )

    def _merge_or_fallback(self) -> None:
        segments = tuple(path for path in self.frozen_preroll.segments if path.is_file())
        if not segments:
            self._move_main_to_final()
            return
        work_dir = self.main_output_path.parent
        concat_file = work_dir / f".{self.output_path.stem}.concat.{uuid.uuid4().hex}.txt"
        temporary_output = self.output_path.with_name(
            f".{self.output_path.stem}.merged.{uuid.uuid4().hex}{self.output_path.suffix}"
        )
        try:
            concat_file.write_text(
                "".join(
                    f"file '{_ffconcat_path(path)}'\n"
                    for path in (*segments, self.main_output_path)
                ),
                encoding="utf-8",
            )
            command = [
                str(self.executable.resolve()),
                "-hide_banner",
                "-loglevel",
                "warning",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
            ]
            if self.recording_format == "mp4":
                command.extend(["-movflags", "+faststart"])
            command.append(str(temporary_output))
            returncode, stderr = self._command_runner(command)
            if returncode == 0 and temporary_output.is_file() and temporary_output.stat().st_size > 0:
                os.replace(temporary_output, self.output_path)
                self._cleanup_after_success()
                return
            detail = stderr.strip() or f"ffmpeg exit {returncode}"
            self._diagnostics.append(
                f"プリロール結合に失敗しました。本録画のみ保存します: {detail[:1000]}"
            )
        except OSError as exc:
            self._diagnostics.append(
                f"プリロール結合を実行できません。本録画のみ保存します: {exc}"
            )
        finally:
            concat_file.unlink(missing_ok=True)
            temporary_output.unlink(missing_ok=True)
        self._move_main_to_final()

    def _move_main_to_final(self) -> None:
        if self.output_path.exists():
            self._cleanup_preroll_segments()
            return
        if self.main_output_path.is_file():
            os.replace(self.main_output_path, self.output_path)
        self._cleanup_preroll_segments()

    def _cleanup_after_success(self) -> None:
        self.main_output_path.unlink(missing_ok=True)
        self._cleanup_preroll_segments()

    def _cleanup_preroll_segments(self) -> None:
        for path in self.frozen_preroll.segments:
            path.unlink(missing_ok=True)
        parents = {path.parent for path in self.frozen_preroll.segments}
        for parent in parents:
            try:
                parent.rmdir()
            except OSError:
                pass


def build_preroll_segment_command(
    *,
    executable: Path,
    profile: RecordingProfile,
    capture_input: CaptureInput,
    output_pattern: Path,
    segment_count: int,
    segment_seconds: int = 1,
) -> tuple[str, ...]:
    command = [
        str(executable.resolve()),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-fflags",
        "+genpts",
        "-thread_queue_size",
        "512",
        "-f",
        capture_input.input_format,
        "-framerate",
        str(profile.frame_rate),
    ]
    command.extend(capture_input.options)
    command.extend(["-i", capture_input.input_name])
    if profile.has_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=channel_layout=stereo:sample_rate={profile.audio_sample_rate}",
            ]
        )
    command.extend(["-map", "0:v:0", "-c:v", profile.video_encoder])
    if profile.video_encoder == "libx264":
        command.extend(["-preset", "ultrafast"])
    command.extend(["-b:v", f"{profile.video_bitrate_kbps}k", "-pix_fmt", "yuv420p"])
    if profile.width is not None and profile.height is not None:
        command.extend(["-vf", f"scale={profile.width}:{profile.height}"])
    else:
        command.extend(["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"])
    if profile.has_audio:
        command.extend(
            [
                "-map",
                "1:a:0",
                "-c:a",
                profile.audio_encoder,
                "-ar",
                str(profile.audio_sample_rate),
                "-ac",
                str(profile.audio_channels),
                "-b:a",
                f"{profile.audio_bitrate_kbps}k",
            ]
        )
    else:
        command.append("-an")
    command.extend(
        [
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-segment_wrap",
            str(segment_count),
            "-reset_timestamps",
            "1",
            str(output_pattern),
        ]
    )
    return tuple(command)


def new_preroll_buffer(
    *,
    paths: RuntimePaths,
    executable: Path,
    profile: RecordingProfile,
    capture_input: CaptureInput,
    seconds: int,
    max_megabytes: int,
) -> PrerollCaptureBuffer:
    token = uuid.uuid4().hex
    directory = paths.data / "preroll" / token
    segment_count = max(1, seconds + 1)
    output_pattern = directory / f"segment_%03d{profile.extension}"
    command = build_preroll_segment_command(
        executable=executable,
        profile=profile,
        capture_input=capture_input,
        output_pattern=output_pattern,
        segment_count=segment_count,
    )
    return PrerollCaptureBuffer(
        command=command,
        directory=directory,
        max_bytes=max_megabytes * 1024 * 1024,
        max_segments=seconds,
    )


def _run_command(command: Sequence[str]) -> tuple[int, str]:
    completed = subprocess.run(
        list(command),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess_creation_flags(),
        check=False,
    )
    return completed.returncode, completed.stderr


def _ffconcat_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
