from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from master_duel_recorder_lite.live_validation import (  # noqa: E402
    evaluate_live_diagnostics,
    render_live_validation_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="自動監視の実戦連続試験を数値診断から集計します"
    )
    parser.add_argument("--diagnostics", type=Path, default=_default_diagnostics())
    parser.add_argument(
        "--since",
        type=_parse_since,
        required=True,
        help="試験開始時刻（タイムゾーン付きISO 8601）",
    )
    parser.add_argument("--required-consecutive", type=int, default=10)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args()
    if args.required_consecutive <= 0:
        parser.error("--required-consecutiveは1以上で指定してください")

    report = evaluate_live_diagnostics(args.diagnostics, since=args.since)
    document = report.to_document(args.required_consecutive)
    markdown = render_live_validation_markdown(report, args.required_consecutive)
    if args.json_out is not None:
        _write_atomic(
            args.json_out,
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        )
    if args.markdown_out is not None:
        _write_atomic(args.markdown_out, markdown)
    print(markdown, end="")
    return 0 if report.gate_passed(args.required_consecutive) else 1


def _default_diagnostics() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "MasterDuelRecorderLite" / "logs" / "visual-monitor"
    return PROJECT_ROOT / "user_data" / "logs" / "visual-monitor"


def _parse_since(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ISO 8601形式で指定してください") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("タイムゾーンを含めてください")
    return parsed


def _write_atomic(path: Path, content: str) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, target)


if __name__ == "__main__":
    raise SystemExit(main())
