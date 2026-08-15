from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import platform
import shutil
import tempfile

from .config import AppConfig
from .audio_loopback import process_loopback_capability
from .ffmpeg import (
    CommandRunner,
    FfmpegDiscoveryResult,
    PathLookup,
    discover_ffmpeg,
    enumerate_windows_inputs,
    probe_ffmpeg_capabilities,
    run_command,
    validate_ffmpeg_capabilities,
)
from .runtime_paths import RuntimePathError, RuntimePaths, ensure_runtime_dirs


MINIMUM_FREE_BYTES = 1024**3
MUXER_BY_RECORDING_FORMAT = {"mkv": "matroska", "mp4": "mp4"}


class CheckStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class PreflightCheck:
    code: str
    label: str
    status: CheckStatus
    message: str


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]

    @property
    def succeeded(self) -> bool:
        return all(check.status is not CheckStatus.ERROR for check in self.checks)

    @property
    def exit_code(self) -> int:
        return 0 if self.succeeded else 2

    @property
    def error_summary(self) -> str:
        return " / ".join(
            f"{check.label}: {check.message}"
            for check in self.checks
            if check.status is CheckStatus.ERROR
        )


DiskUsage = Callable[[Path], object]
WriteProbe = Callable[[Path], None]


def run_preflight(
    *,
    paths: RuntimePaths,
    config: AppConfig,
    config_loaded: bool,
    runner: CommandRunner = run_command,
    path_lookup: PathLookup = shutil.which,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    disk_usage: DiskUsage = shutil.disk_usage,
    write_probe: WriteProbe | None = None,
) -> PreflightReport:
    environment = os.environ if environ is None else environ
    system_name = platform.system() if platform_name is None else platform_name
    checks: list[PreflightCheck] = []

    checks.append(
        PreflightCheck(
            code="config",
            label="設定",
            status=CheckStatus.OK if config_loaded else CheckStatus.WARNING,
            message="app.tomlを読み込みました" if config_loaded else "app.tomlがないため既定設定を使用します",
        )
    )

    discovery = discover_ffmpeg(
        config.ffmpeg_path,
        runner=runner,
        path_lookup=path_lookup,
        environ=environment,
        platform_name=system_name,
    )
    if not discovery.found:
        checks.append(
            PreflightCheck(
                code="ffmpeg",
                label="FFmpeg",
                status=CheckStatus.ERROR,
                message=_discovery_failure_message(discovery),
            )
        )
        checks.append(
            PreflightCheck(
                code="capabilities",
                label="録画能力",
                status=CheckStatus.ERROR,
                message="FFmpegが見つからないため確認できません",
            )
        )
        checks.append(
            PreflightCheck(
                code="inputs",
                label="手動録画対象",
                status=CheckStatus.ERROR,
                message="FFmpegが見つからないため確認できません",
            )
        )
    else:
        assert discovery.executable is not None
        assert discovery.version is not None
        checks.append(
            PreflightCheck(
                code="ffmpeg",
                label="FFmpeg",
                status=CheckStatus.OK,
                message=f"{discovery.version.display}を{discovery.source}から検出しました",
            )
        )
        _append_capability_and_input_checks(
            checks,
            discovery.executable,
            config,
            runner=runner,
            platform_name=system_name,
        )

    _append_visual_detection_check(checks, config, discovery)

    storage_ready = _append_storage_check(checks, paths, config, write_probe or _probe_write_access)
    if storage_ready:
        _append_disk_space_check(checks, paths.recordings, disk_usage)
    else:
        checks.append(
            PreflightCheck(
                code="disk-space",
                label="空き容量",
                status=CheckStatus.ERROR,
                message="録画保存先を利用できないため確認できません",
            )
        )

    return PreflightReport(tuple(checks))


def _append_visual_detection_check(
    checks: list[PreflightCheck],
    config: AppConfig,
    discovery: FfmpegDiscoveryResult,
) -> None:
    if not config.visual_detection_enabled:
        checks.append(
            PreflightCheck(
                "visual-detection",
                "自動監視対象",
                CheckStatus.WARNING,
                "設定で無効です。録画は継続できます",
            )
        )
        return
    if config.capture_mode != "master_duel":
        checks.append(
            PreflightCheck(
                "visual-detection",
                "自動監視対象",
                CheckStatus.OK,
                (
                    "手動録画対象の選択とは独立して、Master Duelクライアント領域を"
                    f"最大{config.visual_detection_maximum_fps:g}fpsで監視します"
                ),
            )
        )
        return
    if not discovery.found:
        checks.append(
            PreflightCheck(
                "visual-detection",
                "自動監視対象",
                CheckStatus.WARNING,
                "FFmpegがないためフレームを取得できません。録画可否とは別に表示しています",
            )
        )
        return
    checks.append(
        PreflightCheck(
            "visual-detection",
            "自動監視対象",
            CheckStatus.OK,
                (
                    "Master Duelクライアント領域の常駐ROI特徴判定と座標録画を"
                    f"最大{config.visual_detection_maximum_fps:g}fps、"
                    f"閾値{config.visual_detection_minimum_confidence:.2f}で利用できます。"
                    "手動録画対象の選択には影響されません。"
                    "別ウィンドウを重ねると録画に映ります"
                ),
        )
    )


def _append_capability_and_input_checks(
    checks: list[PreflightCheck],
    executable: Path,
    config: AppConfig,
    *,
    runner: CommandRunner,
    platform_name: str,
) -> None:
    required_demuxers = [config.screen_input_format]
    if config.audio_mode in {"system", "device"}:
        required_demuxers.append(config.audio_input_format)
    elif config.audio_mode == "process":
        required_demuxers.append("s16le")

    try:
        capabilities = probe_ffmpeg_capabilities(executable, runner=runner)
    except RuntimeError as exc:
        checks.append(PreflightCheck("capabilities", "録画能力", CheckStatus.ERROR, str(exc)))
        checks.append(
            PreflightCheck(
                code="inputs",
                label="手動録画対象",
                status=CheckStatus.ERROR,
                message="FFmpeg能力を確認できないため入力列挙を中止しました",
            )
        )
        return

    validation = validate_ffmpeg_capabilities(
        capabilities,
        required_demuxers=required_demuxers,
        required_encoder=config.video_encoder,
        required_muxer=MUXER_BY_RECORDING_FORMAT[config.recording_format],
        required_audio_encoder="aac" if config.audio_mode != "none" else None,
    )
    capability_summary = [config.screen_input_format, config.video_encoder]
    if config.audio_mode in {"system", "device"}:
        capability_summary.extend([config.audio_input_format, "aac"])
    elif config.audio_mode == "process":
        capability_summary.extend(["process-loopback", "s16le", "aac"])
    capability_summary.append(config.recording_format)
    checks.append(
        PreflightCheck(
            code="capabilities",
            label="録画能力",
            status=CheckStatus.OK if validation.supported else CheckStatus.ERROR,
            message=(
                f"{' / '.join(capability_summary)}を利用できます"
                if validation.supported
                else "、".join(validation.errors)
            ),
        )
    )

    enumeration = enumerate_windows_inputs(executable, runner=runner, platform_name=platform_name)
    screen_matches = [
        item
        for item in enumeration.inputs
        if item.kind == "screen" and item.identifier == config.screen_input and item.input_format == config.screen_input_format
    ]
    audio_matches = [
        item
        for item in enumeration.inputs
        if item.kind == "audio"
        and config.audio_input in {item.identifier, item.display_name}
        and item.input_format == config.audio_input_format
    ]

    errors: list[str] = []
    warnings = list(enumeration.warnings)
    if not screen_matches:
        errors.append(f"画面入力 {config.screen_input} が見つかりません")
    if config.audio_mode in {"system", "device"} and not audio_matches:
        errors.append(f"音声入力 {config.audio_input} が見つかりません")
    if config.audio_mode == "process":
        process_audio = process_loopback_capability()
        if process_audio.supported:
            warnings.append(process_audio.message)
        else:
            warnings.append(
                f"Master Duel単体音声は利用できません: {process_audio.message}。映像のみ継続できます"
            )
    if config.audio_mode == "none":
        warnings.append("音声入力は無効です")
    if enumeration.errors:
        if config.audio_mode in {"system", "device"}:
            errors.extend(enumeration.errors)
        else:
            warnings.extend(enumeration.errors)

    if errors:
        status = CheckStatus.ERROR
        message = "、".join(errors)
    elif warnings:
        status = CheckStatus.WARNING
        message = "、".join(dict.fromkeys(warnings))
    else:
        status = CheckStatus.OK
        message = "設定した画面・音声入力を利用できます"
    checks.append(PreflightCheck("inputs", "手動録画対象", status, message))


def _append_storage_check(
    checks: list[PreflightCheck],
    paths: RuntimePaths,
    config: AppConfig,
    write_probe: WriteProbe,
) -> bool:
    try:
        if config.auto_create_user_data:
            ensure_runtime_dirs(paths)
        elif not paths.recordings.is_dir():
            raise RuntimePathError("録画保存先が存在せず、自動作成が無効です")
        write_probe(paths.recordings)
    except (OSError, RuntimePathError) as exc:
        checks.append(PreflightCheck("storage", "保存先", CheckStatus.ERROR, f"録画保存先へ書き込めません: {exc}"))
        return False

    checks.append(PreflightCheck("storage", "保存先", CheckStatus.OK, "録画保存先へ書き込めます"))
    return True


def _append_disk_space_check(checks: list[PreflightCheck], recordings_path: Path, disk_usage: DiskUsage) -> None:
    try:
        usage = disk_usage(recordings_path)
        free_bytes = int(getattr(usage, "free"))
    except (OSError, TypeError, ValueError, AttributeError) as exc:
        checks.append(PreflightCheck("disk-space", "空き容量", CheckStatus.ERROR, f"空き容量を取得できません: {exc}"))
        return

    free_gib = free_bytes / 1024**3
    if free_bytes < MINIMUM_FREE_BYTES:
        checks.append(
            PreflightCheck(
                "disk-space",
                "空き容量",
                CheckStatus.ERROR,
                f"空き容量が{free_gib:.1f} GiBです。1.0 GiB以上必要です",
            )
        )
        return
    checks.append(PreflightCheck("disk-space", "空き容量", CheckStatus.OK, f"{free_gib:.1f} GiB利用できます"))


def _probe_write_access(directory: Path) -> None:
    with tempfile.NamedTemporaryFile(prefix=".mdrl-preflight-", dir=directory, delete=True) as probe:
        probe.write(b"mdrl")
        probe.flush()


def _discovery_failure_message(discovery: FfmpegDiscoveryResult) -> str:
    details = "、".join(f"{attempt.source}: {attempt.result}" for attempt in discovery.attempts)
    return (
        "FFmpegが未導入または起動できません。GUIの「FFmpegを導入」を使うか、"
        f"FFmpeg 6.0以上のパスを設定してください ({details})"
    )
