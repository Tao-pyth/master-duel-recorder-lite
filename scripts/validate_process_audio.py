from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from master_duel_recorder_lite.audio_loopback import (
    ProcessLoopbackController,
    find_process_loopback_helper,
    new_audio_pipe_name,
)
from master_duel_recorder_lite.windows_process import subprocess_creation_flags


def validate_process_audio(
    *, process_id: int, ffmpeg: Path, output: Path, duration_seconds: float
) -> dict[str, object]:
    helper = find_process_loopback_helper()
    pipe_name = new_audio_pipe_name(f"validation-{process_id}")
    controller = ProcessLoopbackController(
        helper_path=helper,
        process_id=process_id,
        pipe_name=pipe_name,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    controller.start()
    try:
        completed = subprocess.run(
            [
                str(ffmpeg.resolve()),
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-f",
                "s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-i",
                pipe_name,
                "-t",
                f"{duration_seconds:g}",
                "-c:a",
                "pcm_s16le",
                str(output.resolve()),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess_creation_flags(),
            check=False,
        )
    finally:
        controller.stop()
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size <= 44:
        raise RuntimeError(
            f"プロセス音声の取得に失敗しました: {completed.returncode}: {completed.stderr}"
        )
    ffprobe = ffmpeg.with_name("ffprobe.exe")
    probe = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,sample_rate,channels:format=duration",
            "-of",
            "json",
            str(output.resolve()),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess_creation_flags(),
        check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError(f"取得音声を検証できません: {probe.stderr}")
    return {
        "process_id": process_id,
        "output": str(output.resolve()),
        "size_bytes": output.stat().st_size,
        "probe": json.loads(probe.stdout),
        "helper_diagnostics": controller.diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Windows Process Loopback実機検証")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=5.0)
    args = parser.parse_args()
    if args.pid <= 0 or args.duration <= 0:
        parser.error("pidとdurationは0より大きい値が必要です")
    try:
        result = validate_process_audio(
            process_id=args.pid,
            ffmpeg=args.ffmpeg,
            output=args.output,
            duration_seconds=args.duration,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
