from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Callable, Mapping

from .config import AppConfig, validate_app_config


class ConfigValueError(ValueError):
    """CLI設定キーまたは値を安全に解釈できない場合のエラーです。"""


@dataclass(frozen=True)
class ConfigField:
    attribute: str
    parser: Callable[[str], object]


def _stripped_text(value: str) -> str:
    return value.strip()


def _lower_text(value: str) -> str:
    return value.strip().lower()


def _integer(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigValueError("整数で指定してください") from exc


def _number(value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigValueError("数値で指定してください") from exc


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ConfigValueError("trueまたはfalseで指定してください")


CONFIG_FIELDS: Mapping[str, ConfigField] = MappingProxyType(
    {
        "recorder.ffmpeg_path": ConfigField("ffmpeg_path", _stripped_text),
        "recorder.recording_format": ConfigField("recording_format", _lower_text),
        "recorder.screen_input": ConfigField("screen_input", _stripped_text),
        "recorder.screen_input_format": ConfigField("screen_input_format", _lower_text),
        "recorder.capture_mode": ConfigField("capture_mode", _lower_text),
        "recorder.capture_target_id": ConfigField("capture_target_id", _stripped_text),
        "recorder.audio_input": ConfigField("audio_input", _stripped_text),
        "recorder.audio_input_format": ConfigField("audio_input_format", _lower_text),
        "recorder.audio_gain_db": ConfigField("audio_gain_db", _number),
        "recorder.audio_sample_rate": ConfigField("audio_sample_rate", _integer),
        "recorder.audio_channels": ConfigField("audio_channels", _integer),
        "recorder.video_encoder": ConfigField("video_encoder", _stripped_text),
        "recorder.frame_rate": ConfigField("frame_rate", _integer),
        "recorder.capture_width": ConfigField("capture_width", _integer),
        "recorder.capture_height": ConfigField("capture_height", _integer),
        "recorder.video_bitrate_kbps": ConfigField("video_bitrate_kbps", _integer),
        "recorder.audio_bitrate_kbps": ConfigField("audio_bitrate_kbps", _integer),
        "detection.game_process_name": ConfigField("game_process_name", _stripped_text),
        "detection.game_window_title_contains": ConfigField("game_window_title_contains", _stripped_text),
        "detection.auto_start_recording": ConfigField("auto_start_recording", _boolean),
        "detection.auto_stop_recording": ConfigField("auto_stop_recording", _boolean),
        "detection.start_confirmations": ConfigField("start_confirmations", _integer),
        "detection.stop_confirmations": ConfigField("stop_confirmations", _integer),
        "detection.minimum_confidence": ConfigField("detection_minimum_confidence", _number),
        "detection.poll_interval_seconds": ConfigField("detection_poll_interval_seconds", _number),
        "detection.cooldown_seconds": ConfigField("detection_cooldown_seconds", _number),
        "detection.visual_events_enabled": ConfigField("visual_detection_enabled", _boolean),
        "detection.visual_maximum_fps": ConfigField("visual_detection_maximum_fps", _number),
        "detection.visual_language": ConfigField("visual_detection_language", _lower_text),
        "detection.visual_minimum_confidence": ConfigField(
            "visual_detection_minimum_confidence", _number
        ),
        "upload.privacy_status": ConfigField("upload_privacy_status", _lower_text),
        "runtime.auto_create_user_data": ConfigField("auto_create_user_data", _boolean),
    }
)


def config_values(config: AppConfig) -> dict[str, object]:
    return {key: getattr(config, field.attribute) for key, field in CONFIG_FIELDS.items()}


def config_value(config: AppConfig, key: str) -> object:
    field = CONFIG_FIELDS.get(key)
    if field is None:
        raise ConfigValueError(f"未対応の設定キーです: {key}")
    return getattr(config, field.attribute)


def updated_config(config: AppConfig, key: str, raw_value: str) -> AppConfig:
    field = CONFIG_FIELDS.get(key)
    if field is None:
        raise ConfigValueError(f"未対応の設定キーです: {key}")
    try:
        value = field.parser(raw_value)
        candidate = replace(config, **{field.attribute: value})
        validate_app_config(candidate)
    except ConfigValueError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigValueError(str(exc)) from exc
    return candidate
