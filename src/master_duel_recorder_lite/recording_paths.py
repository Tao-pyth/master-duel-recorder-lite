from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .recording_profile import RecordingProfile
from .runtime_paths import RuntimePaths


Clock = Callable[[], datetime]
UuidFactory = Callable[[], UUID]


@dataclass(frozen=True)
class RecordingTarget:
    recording_id: str
    path: Path


class RecordingPathError(RuntimeError):
    """安全な録画保存先を作成できないときのエラーです。"""


def create_recording_target(
    paths: RuntimePaths,
    profile: RecordingProfile,
    *,
    clock: Clock | None = None,
    uuid_factory: UuidFactory = uuid4,
) -> RecordingTarget:
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None:
        raise RecordingPathError("録画日時にはタイムゾーンが必要です")
    utc_now = now.astimezone(timezone.utc)
    recording_id = uuid_factory().hex
    date_directory = paths.recordings / utc_now.strftime("%Y") / utc_now.strftime("%m") / utc_now.strftime("%d")
    date_directory.mkdir(parents=True, exist_ok=True)
    filename = f"{utc_now.strftime('%Y%m%dT%H%M%S_%fZ')}_{recording_id}{profile.extension}"
    output_path = (date_directory / filename).resolve()
    recordings_root = paths.recordings.resolve()
    if not output_path.is_relative_to(recordings_root):
        raise RecordingPathError("録画保存先がrecordings配下ではありません")
    if output_path.exists():
        raise RecordingPathError(f"録画保存先が既に存在します: {output_path.name}")
    return RecordingTarget(recording_id=recording_id, path=output_path)
