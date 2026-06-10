from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


USER_DATA_ENV = "MDRL_USER_DATA_DIR"


class RuntimePathError(RuntimeError):
    """実行時データ用フォルダを準備できないときのエラーです。"""


@dataclass(frozen=True)
class RuntimePaths:
    """実行時データの置き場所をまとめて扱うための小さな入れ物です。"""

    root: Path
    config: Path
    data: Path
    logs: Path
    db: Path
    recordings: Path
    screenshots: Path
    exports: Path
    queue: Path


def default_runtime_root(project_root: Path | None = None) -> Path:
    """既定の user_data ルートを返します。

    `MDRL_USER_DATA_DIR` が設定されている場合は、その場所を優先します。初心者向けに言うと、普段はリポジトリ直下の `user_data` を使い、必要な人だけ保存先を変更できる仕組みです。
    """

    override = os.getenv(USER_DATA_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return (project_root or Path.cwd()).resolve() / "user_data"


def default_runtime_paths(project_root: Path | None = None, user_data_dir: Path | None = None) -> RuntimePaths:
    """既定の user_data 配下パスを返します。"""

    root_dir = (user_data_dir.expanduser().resolve() if user_data_dir else default_runtime_root(project_root))
    data_dir = root_dir / "data"
    return RuntimePaths(
        root=root_dir,
        config=root_dir / "config",
        data=data_dir,
        logs=root_dir / "logs",
        db=data_dir / "db",
        recordings=data_dir / "recordings",
        screenshots=data_dir / "screenshots",
        exports=data_dir / "exports",
        queue=data_dir / "queue",
    )


def _mkdir_or_raise(path: Path, label: str) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimePathError(f"{label} フォルダを作成できません: {path}") from exc
    if not path.is_dir():
        raise RuntimePathError(f"{label} はフォルダである必要があります: {path}")


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    """必要な実行時ディレクトリを作成します。

    何度実行しても既存データは削除しません。アップデート時に録画履歴や設定を守るため、作成だけを行います。
    """

    for label, path in (
        ("user_data", paths.root),
        ("config", paths.config),
        ("data", paths.data),
        ("logs", paths.logs),
        ("db", paths.db),
        ("recordings", paths.recordings),
        ("screenshots", paths.screenshots),
        ("exports", paths.exports),
        ("queue", paths.queue),
    ):
        _mkdir_or_raise(path, label)
