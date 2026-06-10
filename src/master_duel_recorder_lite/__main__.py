from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .runtime_paths import default_runtime_paths, ensure_runtime_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mdrl")
    parser.add_argument("--init-user-data", action="store_true", help="user_data フォルダを作成します。")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="user_data を作成する基準フォルダです。")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    paths = default_runtime_paths(args.project_root)

    if args.init_user_data:
        ensure_runtime_dirs(paths)
        print(f"user_data を作成しました: {paths.root}")
        return 0

    print("master-duel-recorder-lite")
    print(f"version: {__version__}")
    print(f"runtime data: {paths.root}")
    print("次の確認: python -m master_duel_recorder_lite --init-user-data")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
