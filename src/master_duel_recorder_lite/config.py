from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tomllib
import uuid

from .runtime_paths import RuntimePaths, default_runtime_paths


ALLOWED_PRIVACY_STATUSES = {"private", "unlisted"}
ALLOWED_RECORDING_FORMATS = {"mkv", "mp4"}
ALLOWED_SCREEN_INPUT_FORMATS = {"gdigrab"}
ALLOWED_AUDIO_INPUT_FORMATS = {"dshow"}
ALLOWED_CAPTURE_MODES = {"master_duel", "window", "monitor", "desktop"}


class AppConfigError(RuntimeError):
    """設定ファイルを読み込めない、または値が不正なときのエラーです。"""


@dataclass(frozen=True)
class AppConfig:
    """アプリの非シークレット設定です。

    OAuthトークンやAPIキーのような秘密情報は、この設定ファイルには入れません。
    """

    ffmpeg_path: str = "ffmpeg"
    recording_format: str = "mkv"
    screen_input: str = "desktop"
    screen_input_format: str = "gdigrab"
    capture_mode: str = "master_duel"
    capture_target_id: str = ""
    audio_input: str = ""
    audio_input_format: str = "dshow"
    video_encoder: str = "libx264"
    frame_rate: int = 30
    capture_width: int = 0
    capture_height: int = 0
    video_bitrate_kbps: int = 6000
    audio_bitrate_kbps: int = 192
    game_process_name: str = "masterduel.exe"
    game_window_title_contains: str = ""
    auto_start_recording: bool = True
    auto_stop_recording: bool = True
    start_confirmations: int = 3
    stop_confirmations: int = 5
    detection_minimum_confidence: float = 0.5
    detection_poll_interval_seconds: float = 1.0
    detection_cooldown_seconds: float = 10.0
    upload_privacy_status: str = "private"
    auto_create_user_data: bool = True


@dataclass(frozen=True)
class LoadedAppConfig:
    config: AppConfig
    config_path: Path
    config_loaded: bool


def get_default_config_path(paths: RuntimePaths) -> Path:
    return paths.config / "app.toml"


def load_app_config(
    *,
    project_root: Path | None = None,
    user_data_dir: Path | None = None,
) -> LoadedAppConfig:
    """`user_data/config/app.toml` から設定を読み込みます。

    設定ファイルが無い場合は既定値で起動します。初心者向けに言うと、最初の起動で設定ファイルがまだ無くても、アプリは止まらないようにします。
    """

    paths = default_runtime_paths(project_root=project_root, user_data_dir=user_data_dir)
    config_path = get_default_config_path(paths)
    if not config_path.exists():
        return LoadedAppConfig(config=AppConfig(), config_path=config_path, config_loaded=False)

    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        recorder_table = _table(raw, "recorder")
        detection_table = _table(raw, "detection")
        upload_table = _table(raw, "upload")
        runtime_table = _table(raw, "runtime")

        config = AppConfig(
            ffmpeg_path=_string_value(recorder_table, "ffmpeg_path", AppConfig.ffmpeg_path),
            recording_format=_recording_format(
                _string_value(recorder_table, "recording_format", AppConfig.recording_format)
            ),
            screen_input=_required_string_value(recorder_table, "screen_input", AppConfig.screen_input),
            screen_input_format=_input_format(
                _required_string_value(recorder_table, "screen_input_format", AppConfig.screen_input_format),
                allowed=ALLOWED_SCREEN_INPUT_FORMATS,
                key="screen_input_format",
            ),
            capture_mode=_capture_mode(
                _string_value(recorder_table, "capture_mode", AppConfig.capture_mode)
            ),
            capture_target_id=_optional_string_value(
                recorder_table, "capture_target_id", AppConfig.capture_target_id
            ),
            audio_input=_optional_string_value(recorder_table, "audio_input", AppConfig.audio_input),
            audio_input_format=_input_format(
                _required_string_value(recorder_table, "audio_input_format", AppConfig.audio_input_format),
                allowed=ALLOWED_AUDIO_INPUT_FORMATS,
                key="audio_input_format",
            ),
            video_encoder=_encoder_name(
                _required_string_value(recorder_table, "video_encoder", AppConfig.video_encoder)
            ),
            frame_rate=_int_value(recorder_table, "frame_rate", AppConfig.frame_rate),
            capture_width=_int_value(recorder_table, "capture_width", AppConfig.capture_width),
            capture_height=_int_value(recorder_table, "capture_height", AppConfig.capture_height),
            video_bitrate_kbps=_int_value(
                recorder_table, "video_bitrate_kbps", AppConfig.video_bitrate_kbps
            ),
            audio_bitrate_kbps=_int_value(
                recorder_table, "audio_bitrate_kbps", AppConfig.audio_bitrate_kbps
            ),
            game_process_name=_required_string_value(
                detection_table, "game_process_name", AppConfig.game_process_name
            ),
            game_window_title_contains=_optional_string_value(
                detection_table,
                "game_window_title_contains",
                AppConfig.game_window_title_contains,
            ),
            auto_start_recording=_bool_value(
                detection_table, "auto_start_recording", AppConfig.auto_start_recording
            ),
            auto_stop_recording=_bool_value(
                detection_table, "auto_stop_recording", AppConfig.auto_stop_recording
            ),
            start_confirmations=_int_value(
                detection_table, "start_confirmations", AppConfig.start_confirmations
            ),
            stop_confirmations=_int_value(
                detection_table, "stop_confirmations", AppConfig.stop_confirmations
            ),
            detection_minimum_confidence=_float_value(
                detection_table,
                "minimum_confidence",
                AppConfig.detection_minimum_confidence,
            ),
            detection_poll_interval_seconds=_float_value(
                detection_table,
                "poll_interval_seconds",
                AppConfig.detection_poll_interval_seconds,
            ),
            detection_cooldown_seconds=_float_value(
                detection_table,
                "cooldown_seconds",
                AppConfig.detection_cooldown_seconds,
            ),
            upload_privacy_status=_privacy_status(
                _string_value(upload_table, "privacy_status", AppConfig.upload_privacy_status)
            ),
            auto_create_user_data=_bool_value(
                runtime_table, "auto_create_user_data", AppConfig.auto_create_user_data
            ),
        )
        _recording_number_values(config)
        _detection_values(config)
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        raise AppConfigError(f"設定ファイルを読み込めません: {config_path}: {exc}") from exc

    return LoadedAppConfig(config=config, config_path=config_path, config_loaded=True)


def validate_app_config(config: AppConfig) -> None:
    """設定全体を保存時と同じ規則で検証します。"""

    if not isinstance(config.ffmpeg_path, str):
        raise ValueError("ffmpeg_path は文字列である必要があります")
    _required_value(config.ffmpeg_path, "ffmpeg_path")
    _recording_format(config.recording_format)
    _required_value(config.screen_input, "screen_input")
    _input_format(config.screen_input_format, allowed=ALLOWED_SCREEN_INPUT_FORMATS, key="screen_input_format")
    _capture_mode(config.capture_mode)
    if config.capture_mode in {"window", "monitor"}:
        _required_value(config.capture_target_id, "capture_target_id")
    _input_format(config.audio_input_format, allowed=ALLOWED_AUDIO_INPUT_FORMATS, key="audio_input_format")
    _encoder_name(config.video_encoder)
    _recording_number_values(config)
    _detection_values(config)
    _privacy_status(config.upload_privacy_status)
    if not isinstance(config.auto_create_user_data, bool):
        raise ValueError("auto_create_user_data はtrueまたはfalseである必要があります")


def save_app_config(
    *,
    paths: RuntimePaths,
    config: AppConfig,
    overwrite: bool = True,
) -> Path:
    """非シークレット設定を検証し、既存内容を保持して原子的に保存します。"""

    validate_app_config(config)
    config_path = get_default_config_path(paths)
    data = _serialize_app_config(config)
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists() and not overwrite:
            raise AppConfigError(f"設定ファイルは既に存在します: {config_path}")
        if config_path.exists():
            _atomic_replace(config_path.with_name(f"{config_path.name}.previous"), config_path.read_bytes())
        _atomic_replace(config_path, data)
        _sync_directory(config_path.parent)
    except AppConfigError:
        raise
    except OSError as exc:
        raise AppConfigError(f"設定ファイルを保存できません: {config_path}: {exc}") from exc
    return config_path


def _serialize_app_config(config: AppConfig) -> bytes:
    return (
        "\n".join(
            [
                "[recorder]",
                f"ffmpeg_path = {_toml_string(config.ffmpeg_path)}",
                f"recording_format = {_toml_string(config.recording_format)}",
                f"screen_input = {_toml_string(config.screen_input)}",
                f"screen_input_format = {_toml_string(config.screen_input_format)}",
                f"capture_mode = {_toml_string(config.capture_mode)}",
                f"capture_target_id = {_toml_string(config.capture_target_id)}",
                f"audio_input = {_toml_string(config.audio_input)}",
                f"audio_input_format = {_toml_string(config.audio_input_format)}",
                f"video_encoder = {_toml_string(config.video_encoder)}",
                f"frame_rate = {config.frame_rate}",
                f"capture_width = {config.capture_width}",
                f"capture_height = {config.capture_height}",
                f"video_bitrate_kbps = {config.video_bitrate_kbps}",
                f"audio_bitrate_kbps = {config.audio_bitrate_kbps}",
                "",
                "[detection]",
                f"game_process_name = {_toml_string(config.game_process_name)}",
                f"game_window_title_contains = {_toml_string(config.game_window_title_contains)}",
                f"auto_start_recording = {_toml_bool(config.auto_start_recording)}",
                f"auto_stop_recording = {_toml_bool(config.auto_stop_recording)}",
                f"start_confirmations = {config.start_confirmations}",
                f"stop_confirmations = {config.stop_confirmations}",
                f"minimum_confidence = {config.detection_minimum_confidence}",
                f"poll_interval_seconds = {config.detection_poll_interval_seconds}",
                f"cooldown_seconds = {config.detection_cooldown_seconds}",
                "",
                "[upload]",
                f"privacy_status = {_toml_string(config.upload_privacy_status)}",
                "",
                "[runtime]",
                f"auto_create_user_data = {_toml_bool(config.auto_create_user_data)}",
                "",
            ]
        )
        + "\n"
    ).encode("utf-8")


def _atomic_replace(destination: Path, data: bytes) -> None:
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _table(raw: dict[str, object], key: str) -> dict[str, object]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{key}] はTOMLテーブルである必要があります")
    return value


def _string_value(table: dict[str, object], key: str, default: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列である必要があります")
    return value.strip() or default


def _required_string_value(table: dict[str, object], key: str, default: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列である必要があります")
    return _required_value(value, key)


def _optional_string_value(table: dict[str, object], key: str, default: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} は文字列である必要があります")
    return value.strip()


def _bool_value(table: dict[str, object], key: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} はtrueまたはfalseである必要があります")
    return value


def _int_value(table: dict[str, object], key: str, default: int) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} は整数である必要があります")
    return value


def _float_value(table: dict[str, object], key: str, default: float) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} は数値である必要があります")
    return float(value)


def _recording_format(value: str) -> str:
    normalized = value.lower()
    if normalized not in ALLOWED_RECORDING_FORMATS:
        raise ValueError("recording_format は mkv または mp4 である必要があります")
    return normalized


def _capture_mode(value: str) -> str:
    normalized = value.lower()
    if normalized not in ALLOWED_CAPTURE_MODES:
        raise ValueError("capture_modeはmaster_duel、window、monitor、desktopのいずれかである必要があります")
    return normalized


def _privacy_status(value: str) -> str:
    normalized = value.lower()
    if normalized not in ALLOWED_PRIVACY_STATUSES:
        raise ValueError("privacy_status は private または unlisted である必要があります")
    return normalized


def _input_format(value: str, *, allowed: set[str], key: str) -> str:
    normalized = value.lower()
    if normalized not in allowed:
        choices = " または ".join(sorted(allowed))
        raise ValueError(f"{key} は {choices} である必要があります")
    return normalized


def _encoder_name(value: str) -> str:
    normalized = _required_value(value, "video_encoder")
    if re.fullmatch(r"[A-Za-z0-9_]+", normalized) is None:
        raise ValueError("video_encoder は英数字またはアンダースコアである必要があります")
    return normalized


def _required_value(value: str, key: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{key} は空にできません")
    return normalized


def _recording_number_values(config: AppConfig) -> None:
    integer_values = {
        "frame_rate": config.frame_rate,
        "capture_width": config.capture_width,
        "capture_height": config.capture_height,
        "video_bitrate_kbps": config.video_bitrate_kbps,
        "audio_bitrate_kbps": config.audio_bitrate_kbps,
    }
    for key, value in integer_values.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} は整数である必要があります")
    if not 1 <= config.frame_rate <= 120:
        raise ValueError("frame_rate は1から120である必要があります")
    if not 500 <= config.video_bitrate_kbps <= 100_000:
        raise ValueError("video_bitrate_kbps は500から100000である必要があります")
    if not 32 <= config.audio_bitrate_kbps <= 512:
        raise ValueError("audio_bitrate_kbps は32から512である必要があります")
    dimensions = (config.capture_width, config.capture_height)
    if dimensions == (0, 0):
        return
    if 0 in dimensions:
        raise ValueError("capture_width と capture_height は同時に指定する必要があります")
    if not 320 <= config.capture_width <= 7680 or not 240 <= config.capture_height <= 4320:
        raise ValueError("解像度は幅320-7680、高さ240-4320である必要があります")
    if config.capture_width % 2 or config.capture_height % 2:
        raise ValueError("capture_width と capture_height は偶数である必要があります")


def _detection_values(config: AppConfig) -> None:
    boolean_values = {
        "auto_start_recording": config.auto_start_recording,
        "auto_stop_recording": config.auto_stop_recording,
    }
    for key, value in boolean_values.items():
        if not isinstance(value, bool):
            raise ValueError(f"{key} はtrueまたはfalseである必要があります")

    integer_values = {
        "start_confirmations": config.start_confirmations,
        "stop_confirmations": config.stop_confirmations,
    }
    for key, value in integer_values.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} は整数である必要があります")

    numeric_values = {
        "minimum_confidence": config.detection_minimum_confidence,
        "poll_interval_seconds": config.detection_poll_interval_seconds,
        "cooldown_seconds": config.detection_cooldown_seconds,
    }
    for key, value in numeric_values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} は数値である必要があります")

    if re.fullmatch(r"[A-Za-z0-9_.-]+", config.game_process_name) is None:
        raise ValueError("game_process_name はASCII英数字、ピリオド、ハイフン、アンダースコアで指定してください")
    if not 1 <= config.start_confirmations <= 60:
        raise ValueError("start_confirmations は1から60である必要があります")
    if not 1 <= config.stop_confirmations <= 60:
        raise ValueError("stop_confirmations は1から60である必要があります")
    if not 0.0 <= config.detection_minimum_confidence <= 1.0:
        raise ValueError("minimum_confidence は0.0から1.0である必要があります")
    if not 0.1 <= config.detection_poll_interval_seconds <= 60.0:
        raise ValueError("poll_interval_seconds は0.1から60.0である必要があります")
    if not 0.0 <= config.detection_cooldown_seconds <= 300.0:
        raise ValueError("cooldown_seconds は0.0から300.0である必要があります")


def _toml_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"
