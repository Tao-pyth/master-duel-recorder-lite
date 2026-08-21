from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time

from .runtime_paths import RuntimePaths
from .upload_metadata import UploadMetadata
from .upload_preparation import UploadPreparationResult, UploadPreparationService
from .upload_queue import UploadQueueItem, UploadQueueState, UploadQueueStore
from .youtube_client import (
    HttpYouTubeClient,
    YouTubeClient,
    YouTubeClientError,
    YouTubeUploadFailureKind,
)
from .youtube_oauth import CredentialStore, WindowsCredentialStore
from .youtube_uploads import (
    YouTubeUpload,
    YouTubeUploadError,
    YouTubeUploadRepository,
    YouTubeUploadState,
)


class YouTubeServiceError(RuntimeError):
    """YouTube投稿フローを完了できない場合のエラーです。"""


@dataclass(frozen=True)
class YouTubeUploadOutcome:
    upload: YouTubeUpload
    message: str


PrepareRunner = Callable[[str, UploadMetadata], UploadQueueItem]


class YouTubeUploadService:
    def __init__(
        self,
        *,
        paths: RuntimePaths,
        upload_repository: YouTubeUploadRepository,
        queue: UploadQueueStore,
        credential_store: CredentialStore | None = None,
        youtube_client: YouTubeClient | None = None,
        preparation_service: UploadPreparationService | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.paths = paths
        self.upload_repository = upload_repository
        self.queue = queue
        self.credential_store = credential_store or WindowsCredentialStore()
        self.youtube_client = youtube_client or HttpYouTubeClient()
        self.preparation_service = preparation_service
        self.sleep = sleep

    def upload_recording(
        self,
        *,
        recording_id: str,
        metadata: UploadMetadata,
        force_new_upload: bool = False,
        max_attempts: int = 3,
    ) -> YouTubeUploadOutcome:
        upload = self.upload_repository.create(
            recording_id=recording_id,
            metadata=metadata,
            force_new=force_new_upload,
        )
        return self._process_upload(upload, max_attempts=max_attempts)

    def run_waiting(self, *, limit: int = 20, max_attempts: int = 3) -> tuple[YouTubeUploadOutcome, ...]:
        outcomes: list[YouTubeUploadOutcome] = []
        for upload in self.upload_repository.list(state=YouTubeUploadState.WAITING)[:limit]:
            outcomes.append(self._process_upload(upload, max_attempts=max_attempts))
        return tuple(outcomes)

    def _process_upload(
        self,
        upload: YouTubeUpload,
        *,
        max_attempts: int,
    ) -> YouTubeUploadOutcome:
        credentials = self.credential_store.read()
        if credentials is None:
            failed = self.upload_repository.update(
                upload,
                state=YouTubeUploadState.FAILED,
                error="YouTube OAuth連携がありません。mdrl youtube connectを実行してください。",
            )
            return YouTubeUploadOutcome(failed, failed.error or "OAuth未接続")
        try:
            prepared = self._ensure_prepared(upload)
            media_path = self._media_path(prepared)
            current = self.upload_repository.update(
                upload,
                state=YouTubeUploadState.UPLOADING,
                prepare_queue_id=prepared.queue_id,
                increment_attempts=True,
            )
            while True:
                try:
                    result = self.youtube_client.upload_video(
                        credentials=credentials,
                        metadata=current.metadata,
                        media_path=media_path,
                    )
                    completed = self.upload_repository.update(
                        current,
                        state=YouTubeUploadState.COMPLETED,
                        video_id=result.video_id,
                        watch_url=result.watch_url,
                        error=result.warning,
                    )
                    return YouTubeUploadOutcome(
                        completed,
                        result.warning or f"YouTubeアップロードが完了しました: {result.watch_url}",
                    )
                except YouTubeClientError as exc:
                    if exc.kind is YouTubeUploadFailureKind.RETRIABLE and current.attempts < max_attempts:
                        delay = min(60.0, 2.0 ** max(0, current.attempts - 1))
                        self.sleep(delay)
                        current = self.upload_repository.update(
                            current,
                            state=YouTubeUploadState.UPLOADING,
                            error=str(exc),
                            increment_attempts=True,
                        )
                        continue
                    failed = self.upload_repository.update(
                        current,
                        state=YouTubeUploadState.FAILED,
                        error=f"{exc.kind.value}: {exc}",
                    )
                    return YouTubeUploadOutcome(failed, failed.error or str(exc))
        except (OSError, RuntimeError, ValueError, YouTubeUploadError) as exc:
            failed = self.upload_repository.update(
                upload,
                state=YouTubeUploadState.FAILED,
                error=str(exc),
            )
            return YouTubeUploadOutcome(failed, str(exc))

    def _ensure_prepared(self, upload: YouTubeUpload) -> UploadQueueItem:
        existing = self._completed_prepare(upload.recording_id)
        if existing is not None:
            self.upload_repository.update(
                upload,
                state=YouTubeUploadState.PREPARING,
                prepare_queue_id=existing.queue_id,
            )
            return existing
        if self.preparation_service is None:
            raise YouTubeServiceError("アップロード準備サービスが設定されていません")
        preparing = self.upload_repository.update(
            upload,
            state=YouTubeUploadState.PREPARING,
        )
        item = self.preparation_service.enqueue(
            recording_id=preparing.recording_id,
            metadata=preparing.metadata,
        )
        results: tuple[UploadPreparationResult, ...] = self.preparation_service.process(item.queue_id)
        result = next((value for value in results if value.queue_id == item.queue_id), None)
        if result is None or not result.succeeded:
            raise YouTubeServiceError(
                result.message if result is not None else "アップロード準備結果を確認できません"
            )
        completed = self.queue.get(item.queue_id)
        if completed is None or completed.state is not UploadQueueState.COMPLETED:
            raise YouTubeServiceError("アップロード準備キューがcompletedになっていません")
        return completed

    def _completed_prepare(self, recording_id: str) -> UploadQueueItem | None:
        return next(
            (
                item
                for item in self.queue.list()
                if item.recording_id == recording_id
                and item.state is UploadQueueState.COMPLETED
                and item.export_path is not None
            ),
            None,
        )

    def _media_path(self, item: UploadQueueItem) -> Path:
        if item.export_path is None:
            raise YouTubeServiceError("アップロード対象MP4がありません")
        path = (self.paths.root / item.export_path).resolve()
        path.relative_to(self.paths.root.resolve())
        if not path.is_file() or path.stat().st_size <= 0:
            raise YouTubeServiceError(f"アップロード対象MP4が存在しないか空です: {path}")
        return path
