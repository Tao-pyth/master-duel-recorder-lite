from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .recording_history import RecordingHistoryError, RecordingHistoryRepository
from .runtime_paths import RuntimePaths
from .upload_export import UploadExporter, UploadExportResult, UploadExportStatus
from .upload_manifest import UploadManifestError, UploadManifestWriter
from .upload_metadata import UploadMetadata
from .upload_queue import (
    UploadQueueError,
    UploadQueueItem,
    UploadQueueState,
    UploadQueueStore,
)


class UploadPreparationError(RuntimeError):
    """アップロード準備フローを安全に実行できない場合のエラーです。"""


@dataclass(frozen=True)
class UploadPreparationResult:
    queue_id: str
    recording_id: str
    state: UploadQueueState
    message: str
    export_path: Path | None = None
    manifest_path: Path | None = None

    @property
    def succeeded(self) -> bool:
        return self.state is UploadQueueState.COMPLETED


class UploadPreparationService:
    def __init__(
        self,
        *,
        paths: RuntimePaths,
        repository: RecordingHistoryRepository,
        queue: UploadQueueStore,
        exporter: UploadExporter,
        manifest_writer: UploadManifestWriter,
    ) -> None:
        self.paths = paths
        self.repository = repository
        self.queue = queue
        self.exporter = exporter
        self.manifest_writer = manifest_writer

    def enqueue(self, *, recording_id: str, metadata: UploadMetadata) -> UploadQueueItem:
        history = self.repository.get(recording_id)
        if history is None:
            raise UploadPreparationError(f"録画履歴が見つかりません: {recording_id}")
        if history.state != "completed":
            raise UploadPreparationError(
                f"正常完了した録画だけを準備できます: {recording_id}: {history.state}"
            )
        source = (self.paths.recordings / history.output_path).resolve()
        if not source.is_file() or source.stat().st_size <= 0:
            raise UploadPreparationError(f"録画ファイルが存在しないか空です: {source}")
        try:
            return self.queue.enqueue(recording_id=recording_id, metadata=metadata)
        except UploadQueueError as exc:
            raise UploadPreparationError(str(exc)) from exc

    def process(
        self,
        queue_id: str | None = None,
        *,
        progress: Callable[[UploadQueueItem], None] | None = None,
    ) -> tuple[UploadPreparationResult, ...]:
        try:
            self.queue.restore_interrupted()
            if queue_id is None:
                items = tuple(
                    item for item in self.queue.list() if item.state is UploadQueueState.WAITING
                )
            else:
                item = self.queue.get(queue_id)
                if item is None:
                    raise UploadPreparationError(f"キュー項目が見つかりません: {queue_id}")
                if item.state is UploadQueueState.FAILED:
                    item = self.queue.transition(item.queue_id, UploadQueueState.WAITING)
                if item.state is not UploadQueueState.WAITING:
                    raise UploadPreparationError(
                        f"待機または失敗状態の項目だけを処理できます: {item.state.value}"
                    )
                items = (item,)
        except UploadQueueError as exc:
            raise UploadPreparationError(str(exc)) from exc

        results: list[UploadPreparationResult] = []
        for item in items:
            if progress is not None:
                progress(item)
            results.append(self._process_item(item))
        return tuple(results)

    def cancel(self, queue_id: str) -> UploadQueueItem:
        item = self.queue.get(queue_id)
        if item is None:
            raise UploadPreparationError(f"キュー項目が見つかりません: {queue_id}")
        try:
            return self.queue.transition(
                queue_id,
                UploadQueueState.CANCELLED,
                error="ユーザー操作によりキャンセルしました",
            )
        except UploadQueueError as exc:
            raise UploadPreparationError(str(exc)) from exc

    def _process_item(self, item: UploadQueueItem) -> UploadPreparationResult:
        try:
            processing = self.queue.transition(
                item.queue_id,
                UploadQueueState.PROCESSING,
                increment_attempts=True,
            )
            history = self.repository.get(processing.recording_id)
            if history is None or history.state != "completed":
                return self._fail(processing, "正常完了した録画履歴を確認できません")
            source = (self.paths.recordings / history.output_path).resolve()
            export = self.exporter.export(
                recording_id=history.recording_id,
                queue_id=processing.queue_id,
                source_path=source,
            )
            validation = _validation_document(export)
            if export.status is UploadExportStatus.CANCELLED:
                partial_relative = (
                    export.partial_path.resolve().relative_to(self.paths.root.resolve())
                    if export.partial_path is not None
                    else None
                )
                cancelled = self.queue.transition(
                    processing.queue_id,
                    UploadQueueState.CANCELLED,
                    export_path=partial_relative,
                    validation=validation,
                    error=export.message,
                )
                return UploadPreparationResult(
                    cancelled.queue_id,
                    cancelled.recording_id,
                    cancelled.state,
                    export.message,
                )
            if not export.succeeded or export.output_path is None:
                partial_relative = (
                    export.partial_path.resolve().relative_to(self.paths.root.resolve())
                    if export.partial_path is not None
                    else None
                )
                return self._fail(
                    processing,
                    export.message,
                    validation=validation,
                    export_path=partial_relative,
                )
            manifest = self.manifest_writer.write(
                item=processing,
                history=history,
                export=export,
            )
            export_relative = export.output_path.resolve().relative_to(self.paths.root.resolve())
            manifest_relative = manifest.resolve().relative_to(self.paths.root.resolve())
            completed = self.queue.transition(
                processing.queue_id,
                UploadQueueState.COMPLETED,
                export_path=export_relative,
                manifest_path=manifest_relative,
                validation=validation,
                error=None,
            )
            return UploadPreparationResult(
                completed.queue_id,
                completed.recording_id,
                completed.state,
                "アップロード準備が完了しました。",
                export.output_path,
                manifest,
            )
        except (
            OSError,
            RuntimeError,
            RecordingHistoryError,
            UploadManifestError,
            UploadQueueError,
            ValueError,
        ) as exc:
            return self._fail(item, f"アップロード準備に失敗しました: {exc}")

    def _fail(
        self,
        item: UploadQueueItem,
        message: str,
        *,
        validation: dict[str, object] | None = None,
        export_path: Path | None = None,
    ) -> UploadPreparationResult:
        current = self.queue.get(item.queue_id)
        if current is None:
            raise UploadPreparationError(f"失敗したキュー項目が見つかりません: {item.queue_id}")
        if current.state is UploadQueueState.PROCESSING:
            failed = self.queue.transition(
                current.queue_id,
                UploadQueueState.FAILED,
                export_path=export_path,
                validation=validation,
                error=message,
            )
        elif current.state is UploadQueueState.FAILED:
            failed = current
        else:
            raise UploadPreparationError(
                f"キュー失敗状態を保存できません: {current.queue_id}: {current.state.value}"
            )
        return UploadPreparationResult(
            failed.queue_id,
            failed.recording_id,
            failed.state,
            message,
        )


def _validation_document(export: UploadExportResult) -> dict[str, object]:
    source = export.source_validation
    output = export.output_validation
    return {
        "source_status": source.status.value,
        "source_warnings": list(source.warnings),
        "source_errors": list(source.errors),
        "output_status": output.status.value if output else None,
        "output_warnings": list(output.warnings) if output else [],
        "output_errors": list(output.errors) if output else [],
    }
