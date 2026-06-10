from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from .runtime_paths import RuntimePaths, default_runtime_paths


ALLOWED_PRIVACY_STATUSES = {"private", "unlisted"}
ALLOWED_RECORDING_FORMATS = {"mkv", "mp4"}


class AppConfigError(RuntimeError):
    """設定ファイルを読み込めない、または値が不正なときのエラーです。"""


@dataclass(frozen=True)
class AppConfig:
    """アプリの非シークレット設定です。

    OAuthトークンやAPIキーのような秘密情報は、この設定ファイルには入れません。
    """

    ffmpeg_path: str = "ffmpeg"
    recording_format: str = "mkv"
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
        upload_table = _table(raw, "upload")
        runtime_table = _table(raw, "runtime")

        config = AppConfig(
            ffmpeg_path=_string_value(recorder_table, "ffmpeg_path", AppConfig.ffmpeg_path),
            recording_format=_recording_format(
                _string_value(recorder_table, "recording_format", AppConfig.recording_format)
            ),
            upload_privacy_status=_privacy_status(
                _string_value(upload_table, "privacy_status", AppConfig.upload_privacy_status)
            ),
            auto_create_user_data=_bool_value(
                runtime_table, "auto_create_user_data", AppConfig.auto_create_user_data
            ),
        )
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        raise AppConfigError(f"設定ファイルを読み込めません: {config_path}: {exc}") from exc

    return LoadedAppConfig(config=config, config_path=config_path, config_loaded=True)


def save_app_config(*, paths: RuntimePaths, config: AppConfig) -> Path:
    """非シークレット設定を `user_data/config/app.toml` に保存します。"""

    _recording_format(config.recording_format)
    _privacy_status(config.upload_privacy_status)
    config_path = get_default_config_path(paths)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "\n".join(
            [
                "[recorder]",
                f"ffmpeg_path = {_toml_string(config.ffmpeg_path)}",
                f"recording_format = {_toml_string(config.recording_format)}",
                "",
                "[upload]",
                f"privacy_status = {_toml_string(config.upload_privacy_status)}",
                "",
                "[runtime]",
                f"auto_create_user_data = {_toml_bool(config.auto_create_user_data)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


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


def _bool_value(table: dict[str, object], key: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} はtrueまたはfalseである必要があります")
    return value


def _recording_format(value: str) -> str:
    normalized = value.lower()
    if normalized not in ALLOWED_RECORDING_FORMATS:
        raise ValueError("recording_format は mkv または mp4 である必要があります")
    return normalized


def _privacy_status(value: str) -> str:
    normalized = value.lower()
    if normalized not in ALLOWED_PRIVACY_STATUSES:
        raise ValueError("privacy_status は private または unlisted である必要があります")
    return normalized


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"
