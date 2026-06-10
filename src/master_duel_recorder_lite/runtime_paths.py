from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    """実行時データの置き場所をまとめて扱うための小さな入れ物です。"""

    root: Path
    config: Path
    data: Path
    logs: Path
    recordings: Path
    queue: Path


def default_runtime_paths(project_root: Path | None = None) -> RuntimePaths:
    """既定の user_data 配下パスを返します。

    初心者向けの補足: アプリ本体とユーザーデータを分けると、将来アップデートするときに録画履歴や設定を守りやすくなります。
    """

    root_dir = (project_root or Path.cwd()) / "user_data"
    return RuntimePaths(
        root=root_dir,
        config=root_dir / "config",
        data=root_dir / "data",
        logs=root_dir / "logs",
        recordings=root_dir / "recordings",
        queue=root_dir / "queue",
    )


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    """必要な実行時ディレクトリを作成します。"""

    for path in (paths.config, paths.data, paths.logs, paths.recordings, paths.queue):
        path.mkdir(parents=True, exist_ok=True)
