from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .config import AppConfig, load_app_config, save_app_config
from .runtime_paths import default_runtime_paths, ensure_runtime_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mdrl")
    parser.add_argument("--init-user-data", action="store_true", help="user_data フォルダを作成します。")
    parser.add_argument("--write-default-config", action="store_true", help="既定の app.toml を作成します。")
    parser.add_argument("--show-config", action="store_true", help="現在の設定読み込み結果を表示します。")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="user_data を作成する基準フォルダです。")
    parser.add_argument("--user-data-dir", type=Path, default=None, help="user_data の場所を直接指定します。")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    paths = default_runtime_paths(project_root=args.project_root, user_data_dir=args.user_data_dir)

    if args.init_user_data:
        ensure_runtime_dirs(paths)
        print(f"user_data を作成しました: {paths.root}")

    if args.write_default_config:
        ensure_runtime_dirs(paths)
        config_path = save_app_config(paths=paths, config=AppConfig())
        print(f"既定設定を書き込みました: {config_path}")

    if args.show_config:
        loaded = load_app_config(project_root=args.project_root, user_data_dir=args.user_data_dir)
        print(f"config path: {loaded.config_path}")
        print(f"config loaded: {loaded.config_loaded}")
        print(f"ffmpeg path: {loaded.config.ffmpeg_path}")
        print(f"recording format: {loaded.config.recording_format}")
        print(f"upload privacy: {loaded.config.upload_privacy_status}")
        print(f"auto create user_data: {loaded.config.auto_create_user_data}")

    if args.init_user_data or args.write_default_config or args.show_config:
        return 0

    print("master-duel-recorder-lite")
    print(f"version: {__version__}")
    print(f"runtime data: {paths.root}")
    print("次の確認: python -m master_duel_recorder_lite --init-user-data --write-default-config --show-config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
