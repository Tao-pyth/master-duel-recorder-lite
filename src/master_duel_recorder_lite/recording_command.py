from __future__ import annotations

from pathlib import Path

from .recording_profile import RecordingProfile


class RecordingCommandError(ValueError):
    """安全なFFmpeg録画コマンドを構築できないときのエラーです。"""


def build_recording_command(
    *,
    executable: Path,
    profile: RecordingProfile,
    output_path: Path,
    recordings_root: Path,
) -> tuple[str, ...]:
    output = output_path.resolve()
    root = recordings_root.resolve()
    if not output.is_relative_to(root):
        raise RecordingCommandError("出力先はrecordings配下である必要があります")
    if output.suffix.lower() != profile.extension:
        raise RecordingCommandError(f"出力拡張子は{profile.extension}である必要があります")
    if output.exists():
        raise RecordingCommandError("既存の録画ファイルは上書きできません")

    command = [
        str(executable.resolve()),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-n",
        "-thread_queue_size",
        "512",
        "-f",
        profile.screen_input_format,
        "-framerate",
        str(profile.frame_rate),
        "-i",
        profile.screen_input,
    ]
    if profile.has_audio:
        command.extend(
            [
                "-thread_queue_size",
                "512",
                "-f",
                profile.audio_input_format,
                "-i",
                f"audio={profile.audio_input}",
            ]
        )

    command.extend(
        [
            "-map",
            "0:v:0",
            "-c:v",
            profile.video_encoder,
            "-b:v",
            f"{profile.video_bitrate_kbps}k",
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if profile.width is not None and profile.height is not None:
        command.extend(["-vf", f"scale={profile.width}:{profile.height}"])

    if profile.has_audio:
        command.extend(
            [
                "-map",
                "1:a:0",
                "-c:a",
                profile.audio_encoder,
                "-b:a",
                f"{profile.audio_bitrate_kbps}k",
            ]
        )
    else:
        command.append("-an")

    command.extend(["-max_muxing_queue_size", "1024"])
    if profile.recording_format == "mp4":
        command.extend(["-movflags", "+faststart"])
    command.append(str(output))
    return tuple(command)
