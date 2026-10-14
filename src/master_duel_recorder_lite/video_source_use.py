"""原動画を利用する処理と削除サービスが、同じ利用台帳を共有するための入口。"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterator
import uuid

from .runtime_paths import RuntimePaths
from .video_lifecycle import VideoAsset, VideoAssetUse, VideoLifecycleError, VideoLifecycleRepository


class VideoSourceUseError(VideoLifecycleError):
    """原動画の利用を安全に開始・終了できない場合のエラーです。"""


@dataclass(frozen=True)
class VideoSourceUse:
    asset: VideoAsset
    lease: VideoAssetUse
    path: Path


@contextmanager
def video_source_use(paths: RuntimePaths, recording_id: str, *, purpose: str) -> Iterator[VideoSourceUse]:
    """原動画を読む直前に登録し、利用が終わったら所有者本人のleaseを必ず解除します。"""
    if not isinstance(purpose, str) or not purpose.strip():
        raise VideoSourceUseError("動画の利用目的を指定してください")
    try:
        repository = VideoLifecycleRepository.from_runtime_paths(paths)
        asset = repository.ensure_current_asset(recording_id)
        if not asset.is_current or asset.state in {"deleted", "unavailable"}:
            raise VideoSourceUseError("現在の元動画を利用できません")
        owner_id, process_id = f"{purpose}:{uuid.uuid4().hex}", os.getpid()
        lease = repository.register_use(asset_id=asset.asset_id, owner_id=owner_id, purpose=purpose, process_id=process_id)
    except (RuntimeError, OSError, ValueError) as exc:
        raise VideoSourceUseError(f"原動画の利用を開始できません: {exc}") from exc
    try:
        try:
            # 関数内importで、準備サービスと削除サービスのimport順に依存させません。
            from .recording_retention import safe_file_path

            path = safe_file_path(paths.recordings, asset.path)
            actual = path.stat()
            if actual.st_size <= 0:
                raise VideoSourceUseError("元動画が空です")
            current = repository.get_current_asset(recording_id)
            if (current is None or (current.asset_id, current.generation, current.path)
                    != (asset.asset_id, asset.generation, asset.path)
                    or current.state in {"deleted", "unavailable"}):
                raise VideoSourceUseError("元動画の世代・参照が変わりました")
            if asset.state == "present" and (
                (asset.size_bytes is not None and actual.st_size != asset.size_bytes)
                or (asset.mtime_ns is not None and actual.st_mtime_ns != asset.mtime_ns)
            ):
                raise VideoSourceUseError("検証済み元動画の内容が変わりました")
        except (RuntimeError, OSError, ValueError) as exc:
            raise VideoSourceUseError(f"原動画を安全に確認できません: {exc}") from exc
        yield VideoSourceUse(current, lease, path)
    finally:
        repository.release_use(lease.use_id, owner_id=owner_id, process_id=process_id)
