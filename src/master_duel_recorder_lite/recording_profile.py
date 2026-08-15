from __future__ import annotations

from dataclasses import dataclass
import re

from .config import AppConfig


class RecordingProfileError(ValueError):
    """録画プロファイルの値または組合せが不正なときのエラーです。"""


@dataclass(frozen=True)
class RecordingProfile:
    recording_format: str = "mkv"
    screen_input: str = "desktop"
    screen_input_format: str = "gdigrab"
    audio_input: str = ""
    audio_input_format: str = "dshow"
    audio_mode: str = "none"
    audio_gain_db: float = 0.0
    audio_sample_rate: int = 48_000
    audio_channels: int = 2
    video_encoder: str = "libx264"
    frame_rate: int = 30
    width: int | None = None
    height: int | None = None
    video_bitrate_kbps: int = 6000
    audio_encoder: str = "aac"
    audio_bitrate_kbps: int = 192

    def __post_init__(self) -> None:
        if self.recording_format not in {"mkv", "mp4"}:
            raise RecordingProfileError("recording_format は mkv または mp4 である必要があります")
        if self.screen_input_format != "gdigrab" or not self.screen_input:
            raise RecordingProfileError("画面入力は空でないgdigrab入力である必要があります")
        if self.audio_mode not in {"process", "system", "device", "none"}:
            raise RecordingProfileError("未対応の音声モードです")
        if self.audio_mode in {"system", "device"} and not self.audio_input:
            raise RecordingProfileError("選択した音声モードには入力デバイスが必要です")
        if self.audio_input and self.audio_input_format != "dshow":
            raise RecordingProfileError("音声入力を使う場合はdshowである必要があります")
        if isinstance(self.audio_gain_db, bool) or not isinstance(
            self.audio_gain_db, (int, float)
        ):
            raise RecordingProfileError("audio_gain_db は数値である必要があります")
        if not -30.0 <= float(self.audio_gain_db) <= 30.0:
            raise RecordingProfileError("audio_gain_db は-30.0から30.0である必要があります")
        if self.audio_sample_rate not in {44_100, 48_000}:
            raise RecordingProfileError("audio_sample_rate は44100または48000である必要があります")
        if self.audio_channels not in {1, 2}:
            raise RecordingProfileError("audio_channels は1または2である必要があります")
        if re.fullmatch(r"[A-Za-z0-9_]+", self.video_encoder) is None:
            raise RecordingProfileError("video_encoder はASCII英数字またはアンダースコアである必要があります")
        if re.fullmatch(r"[A-Za-z0-9_]+", self.audio_encoder) is None:
            raise RecordingProfileError("audio_encoder はASCII英数字またはアンダースコアである必要があります")
        for name, value in (
            ("frame_rate", self.frame_rate),
            ("video_bitrate_kbps", self.video_bitrate_kbps),
            ("audio_bitrate_kbps", self.audio_bitrate_kbps),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise RecordingProfileError(f"{name} は整数である必要があります")
        if not 1 <= self.frame_rate <= 120:
            raise RecordingProfileError("frame_rate は1から120である必要があります")
        if not 500 <= self.video_bitrate_kbps <= 100_000:
            raise RecordingProfileError("video_bitrate_kbps は500から100000である必要があります")
        if not 32 <= self.audio_bitrate_kbps <= 512:
            raise RecordingProfileError("audio_bitrate_kbps は32から512である必要があります")
        if (self.width is None) != (self.height is None):
            raise RecordingProfileError("width と height は同時に指定する必要があります")
        if self.width is not None and self.height is not None:
            if (
                isinstance(self.width, bool)
                or isinstance(self.height, bool)
                or not isinstance(self.width, int)
                or not isinstance(self.height, int)
            ):
                raise RecordingProfileError("width と height は整数である必要があります")
            if not 320 <= self.width <= 7680 or not 240 <= self.height <= 4320:
                raise RecordingProfileError("解像度は幅320-7680、高さ240-4320である必要があります")
            if self.width % 2 or self.height % 2:
                raise RecordingProfileError("width と height は偶数である必要があります")

    @property
    def has_audio(self) -> bool:
        return self.audio_mode != "none"

    @property
    def extension(self) -> str:
        return f".{self.recording_format}"

    @property
    def audio_label(self) -> str:
        if self.audio_mode == "process":
            return "Master Duelのみ"
        if self.audio_mode == "system":
            return f"PC全体: {self.audio_input}"
        if self.audio_mode == "device":
            return f"入力デバイス: {self.audio_input}"
        return ""

    @classmethod
    def from_config(cls, config: AppConfig) -> RecordingProfile:
        width = config.capture_width or None
        height = config.capture_height or None
        try:
            return cls(
                recording_format=config.recording_format,
                screen_input=config.screen_input,
                screen_input_format=config.screen_input_format,
                audio_input=config.audio_input,
                audio_input_format=config.audio_input_format,
                audio_mode=config.audio_mode,
                audio_gain_db=config.audio_gain_db,
                audio_sample_rate=config.audio_sample_rate,
                audio_channels=config.audio_channels,
                video_encoder=config.video_encoder,
                frame_rate=config.frame_rate,
                width=width,
                height=height,
                video_bitrate_kbps=config.video_bitrate_kbps,
                audio_bitrate_kbps=config.audio_bitrate_kbps,
            )
        except RecordingProfileError:
            raise
        except (TypeError, ValueError) as exc:
            raise RecordingProfileError(f"設定から録画プロファイルを作成できません: {exc}") from exc
