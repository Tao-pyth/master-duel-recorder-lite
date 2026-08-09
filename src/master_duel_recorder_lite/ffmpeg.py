from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

from .runtime_paths import local_application_data_root
from .windows_process import (
    configure_windows_process_errors,
    run_with_windows_retry,
    subprocess_creation_flags,
)


MINIMUM_FFMPEG_VERSION = (6, 0, 0)
MINIMUM_LIBAVUTIL_MAJOR = 58


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str], float], CommandResult]
PathLookup = Callable[[str], str | None]


def run_command(command: Sequence[str], timeout_seconds: float) -> CommandResult:
    configure_windows_process_errors()
    completed = run_with_windows_retry(
        lambda: subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess_creation_flags(),
        )
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


@dataclass(frozen=True)
class FfmpegVersion:
    identifier: str
    semantic: tuple[int, int, int] | None
    libavutil_major: int | None

    @property
    def is_supported(self) -> bool:
        if self.semantic is not None and self.semantic >= MINIMUM_FFMPEG_VERSION:
            return True
        return self.libavutil_major is not None and self.libavutil_major >= MINIMUM_LIBAVUTIL_MAJOR

    @property
    def display(self) -> str:
        if self.semantic is None:
            return self.identifier
        return ".".join(str(part) for part in self.semantic)


@dataclass(frozen=True)
class FfmpegDiscoveryAttempt:
    candidate: str
    source: str
    result: str


@dataclass(frozen=True)
class FfmpegDiscoveryResult:
    executable: Path | None
    source: str | None
    version: FfmpegVersion | None
    attempts: tuple[FfmpegDiscoveryAttempt, ...]

    @property
    def found(self) -> bool:
        return self.executable is not None


@dataclass(frozen=True)
class FfmpegCapabilities:
    version: FfmpegVersion
    demuxers: frozenset[str]
    muxers: frozenset[str]
    encoders: frozenset[str]


@dataclass(frozen=True)
class CapabilityValidation:
    errors: tuple[str, ...]

    @property
    def supported(self) -> bool:
        return not self.errors


class FfmpegProbeError(RuntimeError):
    """FFmpegの能力を取得できないときのエラーです。"""


@dataclass(frozen=True)
class CaptureInput:
    kind: str
    display_name: str
    identifier: str
    input_format: str


@dataclass(frozen=True)
class InputEnumerationResult:
    inputs: tuple[CaptureInput, ...]
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return not self.errors


def discover_ffmpeg(
    configured_path: str,
    *,
    runner: CommandRunner = run_command,
    path_lookup: PathLookup = shutil.which,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
) -> FfmpegDiscoveryResult:
    """設定、PATH、既知の場所の順で実行可能なFFmpegを探します。"""

    environment = os.environ if environ is None else environ
    system_name = platform.system() if platform_name is None else platform_name
    configured = Path(configured_path).expanduser()
    explicit_path = configured.is_absolute() or configured.parent != Path(".")
    candidates: list[tuple[Path, str]] = []

    if explicit_path:
        candidates.append((configured, "config"))
    else:
        resolved = path_lookup(configured_path)
        if resolved:
            candidates.append((Path(resolved), "PATH"))
        if configured_path.lower() in {"ffmpeg", "ffmpeg.exe"}:
            candidates.extend((candidate, "known-location") for candidate in known_ffmpeg_candidates(environment, system_name))

    attempts: list[FfmpegDiscoveryAttempt] = []
    seen: set[str] = set()
    for candidate, source in candidates:
        resolved_candidate = candidate.expanduser().resolve()
        identity = os.path.normcase(str(resolved_candidate))
        if identity in seen:
            continue
        seen.add(identity)

        if not resolved_candidate.is_file():
            attempts.append(FfmpegDiscoveryAttempt(str(resolved_candidate), source, "ファイルが存在しません"))
            continue
        if not os.access(resolved_candidate, os.X_OK):
            attempts.append(FfmpegDiscoveryAttempt(str(resolved_candidate), source, "実行権限がありません"))
            continue

        try:
            command_result = runner((str(resolved_candidate), "-version"), 15.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            attempts.append(FfmpegDiscoveryAttempt(str(resolved_candidate), source, f"実行できません: {exc}"))
            continue

        combined_output = _combined_output(command_result)
        version = parse_ffmpeg_version(combined_output)
        if command_result.returncode != 0:
            attempts.append(
                FfmpegDiscoveryAttempt(
                    str(resolved_candidate), source, f"-versionが終了コード{command_result.returncode}を返しました"
                )
            )
            continue
        if version is None:
            attempts.append(FfmpegDiscoveryAttempt(str(resolved_candidate), source, "FFmpegのバージョン出力ではありません"))
            continue

        attempts.append(FfmpegDiscoveryAttempt(str(resolved_candidate), source, "利用可能"))
        return FfmpegDiscoveryResult(resolved_candidate, source, version, tuple(attempts))

    if not candidates:
        attempts.append(FfmpegDiscoveryAttempt(configured_path, "config/PATH", "候補が見つかりません"))
    return FfmpegDiscoveryResult(None, None, None, tuple(attempts))


def known_ffmpeg_candidates(environ: Mapping[str, str], platform_name: str) -> tuple[Path, ...]:
    if platform_name != "Windows":
        return ()

    candidates: list[Path] = [
        local_application_data_root(environ=dict(environ))
        / "tools"
        / "ffmpeg"
        / "bin"
        / "ffmpeg.exe",
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
    ]
    for variable, suffix in (
        ("LOCALAPPDATA", "Microsoft/WinGet/Links/ffmpeg.exe"),
        ("ProgramFiles", "ffmpeg/bin/ffmpeg.exe"),
        ("ProgramFiles(x86)", "ffmpeg/bin/ffmpeg.exe"),
    ):
        base = environ.get(variable)
        if base:
            candidates.append(Path(base) / suffix)
    return tuple(candidates)


def parse_ffmpeg_version(output: str) -> FfmpegVersion | None:
    identifier_match = re.search(r"(?m)^ffmpeg version\s+(?P<identifier>\S+)", output)
    if identifier_match is None:
        return None

    identifier = identifier_match.group("identifier")
    semantic_match = re.search(r"(?<!\d)n?(\d+)\.(\d+)(?:\.(\d+))?", identifier, flags=re.IGNORECASE)
    semantic = None
    if semantic_match is not None:
        semantic = tuple(int(part or 0) for part in semantic_match.groups())

    libavutil_match = re.search(r"(?m)^libavutil\s+(\d+)\.\s*\d+\.\s*\d+", output)
    libavutil_major = int(libavutil_match.group(1)) if libavutil_match is not None else None
    return FfmpegVersion(identifier=identifier, semantic=semantic, libavutil_major=libavutil_major)


def probe_ffmpeg_capabilities(
    executable: Path,
    *,
    runner: CommandRunner = run_command,
) -> FfmpegCapabilities:
    version_output = _run_probe(executable, ("-version",), runner)
    version = parse_ffmpeg_version(version_output)
    if version is None:
        raise FfmpegProbeError("FFmpegのバージョンを解析できません")

    demuxers = _parse_component_names(_run_probe(executable, ("-hide_banner", "-demuxers"), runner), "D")
    muxers = _parse_component_names(_run_probe(executable, ("-hide_banner", "-muxers"), runner), "E")
    encoders = _parse_encoder_names(_run_probe(executable, ("-hide_banner", "-encoders"), runner))
    return FfmpegCapabilities(
        version=version,
        demuxers=frozenset(demuxers),
        muxers=frozenset(muxers),
        encoders=frozenset(encoders),
    )


def validate_ffmpeg_capabilities(
    capabilities: FfmpegCapabilities,
    *,
    required_demuxers: Sequence[str],
    required_encoder: str,
    required_muxer: str,
    required_audio_encoder: str | None = None,
) -> CapabilityValidation:
    errors: list[str] = []
    if not capabilities.version.is_supported:
        errors.append("FFmpeg 6.0以上またはlibavutil 58以上が必要です")
    for demuxer in required_demuxers:
        if demuxer not in capabilities.demuxers:
            errors.append(f"入力方式 {demuxer} を利用できません")
    if required_encoder not in capabilities.encoders:
        errors.append(f"映像エンコーダー {required_encoder} を利用できません")
    if required_audio_encoder is not None and required_audio_encoder not in capabilities.encoders:
        errors.append(f"音声エンコーダー {required_audio_encoder} を利用できません")
    if required_muxer not in capabilities.muxers:
        errors.append(f"出力コンテナ {required_muxer} を利用できません")
    return CapabilityValidation(tuple(errors))


def enumerate_windows_inputs(
    executable: Path,
    *,
    runner: CommandRunner = run_command,
    platform_name: str | None = None,
) -> InputEnumerationResult:
    system_name = platform.system() if platform_name is None else platform_name
    if system_name != "Windows":
        return InputEnumerationResult(inputs=(), errors=("Windows以外の入力列挙には対応していません",))

    screen = CaptureInput(kind="screen", display_name="デスクトップ", identifier="desktop", input_format="gdigrab")
    try:
        result = runner(
            (str(executable), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"),
            10.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return InputEnumerationResult(inputs=(screen,), errors=(f"音声入力を列挙できません: {exc}",))

    output = _combined_output(result)
    if "Unknown input format" in output and "dshow" in output:
        return InputEnumerationResult(inputs=(screen,), errors=("FFmpegがdshow入力に対応していません",))

    audio_inputs = tuple(device for device in parse_dshow_devices(output) if device.kind == "audio")
    warnings = () if audio_inputs else ("音声入力候補が見つかりません",)
    return InputEnumerationResult(inputs=(screen, *audio_inputs), warnings=warnings)


def parse_dshow_devices(output: str) -> tuple[CaptureInput, ...]:
    devices: list[CaptureInput] = []
    pattern = re.compile(r'^\s*\[dshow[^]]*\]\s+"(?P<name>.+)"\s+\((?P<kind>audio|video)\)\s*$')
    for line in output.splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        kind = match.group("kind")
        name = match.group("name")
        devices.append(CaptureInput(kind=kind, display_name=name, identifier=name, input_format="dshow"))
    return tuple(devices)


def _run_probe(executable: Path, arguments: Sequence[str], runner: CommandRunner) -> str:
    try:
        result = runner((str(executable), *arguments), 10.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FfmpegProbeError(f"FFmpegを実行できません: {exc}") from exc
    if result.returncode != 0:
        raise FfmpegProbeError(f"FFmpeg能力検査が終了コード{result.returncode}で失敗しました")
    return _combined_output(result)


def _parse_component_names(output: str, required_flag: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0] != required_flag:
            continue
        # FFmpeg 9 adds a separate device flag, as in "D d gdigrab".
        name_index = 2 if len(fields) >= 3 and fields[1] in {"d", "."} else 1
        names.update(fields[name_index].split(","))
    return names


def _parse_encoder_names(output: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2 and len(fields[0]) == 6 and fields[0][0] in {"V", "A", "S"}:
            names.add(fields[1])
    return names


def _combined_output(result: CommandResult) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)
