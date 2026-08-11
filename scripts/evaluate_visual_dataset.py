from __future__ import annotations

import argparse
import json
from pathlib import Path

from master_duel_recorder_lite.visual_dataset import (
    evaluate_visual_dataset,
    load_visual_dataset,
    render_evaluation_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Master Duel画面判定データセットを評価します")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=Path("ffmpeg"))
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args()

    report = evaluate_visual_dataset(
        load_visual_dataset(args.manifest),
        args.ffmpeg,
        sample_fps=args.fps,
        max_workers=args.workers,
    )
    markdown = render_evaluation_markdown(report)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown, encoding="utf-8")
    print(markdown)
    return 1 if any(video.status == "error" for video in report.videos) else 0


if __name__ == "__main__":
    raise SystemExit(main())
