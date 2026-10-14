"""投稿結果と、元動画の削除・再試行結果を別々に管理します。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from .config import LoadedAppConfig, load_app_config
from .recording_history import RecordingHistoryRepository
from .runtime_paths import RuntimePaths
from .upload_manifest import validate_upload_manifest
from .upload_queue import UploadQueueItem, UploadQueueState, UploadQueueStore
from .youtube_client import YouTubeUploadResult
from .youtube_uploads import YouTubeUpload, YouTubeUploadRepository, YouTubeUploadState

if TYPE_CHECKING:
    from .recording_retention import RecordingDeletionService
    from .video_lifecycle import UploadCleanupRequest, VideoAsset, VideoLifecycleRepository


PROVENANCE_SCHEMA = "mdrl-upload-cleanup-v1"
MAX_MANIFEST_BYTES = 1024 * 1024


@dataclass(frozen=True)
class UploadCleanupOutcome:
    status: str
    reason: str
    message: str
    retry_request_id: str | None = None


class _CleanupRejected(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class PostUploadCleanupHandler:
    def __init__(
        self,
        paths: RuntimePaths,
        *,
        deletion_service: RecordingDeletionService | None = None,
        lifecycle_repository: VideoLifecycleRepository | None = None,
        upload_repository: YouTubeUploadRepository | None = None,
        queue: UploadQueueStore | None = None,
        config_loader: Callable[[], LoadedAppConfig] | None = None,
    ) -> None:
        self.paths = paths
        self.config_loader = config_loader or (lambda: load_app_config(user_data_dir=paths.root))
        if lifecycle_repository is None:
            from .video_lifecycle import VideoLifecycleRepository

            lifecycle_repository = VideoLifecycleRepository.from_runtime_paths(paths)
        self.lifecycle = lifecycle_repository
        self.uploads = upload_repository or YouTubeUploadRepository.from_runtime_paths(paths)
        self.queue = queue or UploadQueueStore(paths)
        if deletion_service is None:
            from .recording_retention import RecordingDeletionService

            deletion_service = RecordingDeletionService(
                paths, repository=self.lifecycle, config_loader=self.config_loader,
            )
        self.deletion = deletion_service

    def __call__(
        self,
        upload: YouTubeUpload,
        prepared: UploadQueueItem,
        result: YouTubeUploadResult,
    ) -> UploadCleanupOutcome:
        """Futureは、この同期後処理の終端を待ってから完了します。"""
        try:
            saved = self._saved_upload(upload.upload_id, upload.recording_id, prepared.queue_id)
            if saved.video_id != result.video_id or saved.watch_url != result.watch_url:
                raise _CleanupRejected("completion_mismatch", "保存された投稿結果が応答と一致しません")
            if not self._enabled():
                return UploadCleanupOutcome("disabled", "setting_off", "投稿後の動画自動削除は無効です")
            asset = self.lifecycle.ensure_current_asset(saved.recording_id)
            existing = self.lifecycle.deletion_for_asset(asset.asset_id)
            if existing is not None:
                return UploadCleanupOutcome("already_deleted", existing.reason, "元動画は既に削除済みです")
            if self.lifecycle.get_protection(saved.recording_id).protected:
                return UploadCleanupOutcome("skipped", "protected", "保護されている元動画を保持しました")
            provenance = self._provenance(saved, prepared, result, asset)
            return self._delete(saved, asset, provenance)
        except _CleanupRejected as exc:
            return UploadCleanupOutcome("skipped", exc.reason, f"元動画を保持しました: {exc}")
        except Exception as exc:
            return UploadCleanupOutcome("failed", "cleanup_failed", f"元動画の後処理に失敗しました: {exc}")

    def retry_request(self, request: UploadCleanupRequest) -> UploadCleanupOutcome:
        """永続要求だけを再試行し、過去のcompleted一覧から要求を作りません。"""
        try:
            saved_request = self.lifecycle.get_cleanup_request(request.request_id)
            if saved_request is None:
                return UploadCleanupOutcome("skipped", "request_missing", "保存された再試行要求がありません")
            request = saved_request
            if request.state != "pending":
                return UploadCleanupOutcome("skipped", "request_not_pending", "再試行対象の要求ではありません")
            self.lifecycle.update_cleanup_request(request.request_id, increment_attempts=True)
            if not self._enabled():
                return self._keep_request(request, "setting_off", "設定OFFのため再試行を見送りました")
            provenance = request.provenance
            if not isinstance(provenance, dict) or provenance.get("schema") != PROVENANCE_SCHEMA:
                raise _CleanupRejected("provenance_mismatch", "再試行の出自情報が不正です")
            upload = self._saved_upload(request.upload_id, request.recording_id, request.prepare_queue_id)
            asset = self.lifecycle.get_current_asset(request.recording_id)
            if (asset is None or asset.asset_id != request.asset_id
                    or asset.generation != request.generation):
                raise _CleanupRejected("asset_changed", "元動画の実体または世代が変わりました")
            existing = self.lifecycle.deletion_for_asset(asset.asset_id)
            if existing is not None:
                outcome = UploadCleanupOutcome("already_deleted", existing.reason, "元動画は既に削除済みです")
                self.lifecycle.update_cleanup_request(request.request_id, state="completed")
                return outcome
            if self.lifecycle.get_protection(request.recording_id).protected:
                return self._keep_request(request, "protected", "保護中のため再試行を見送りました")
            prepared = self.queue.get(str(provenance.get("queue_id", "")))
            if prepared is None:
                raise _CleanupRejected("provenance_mismatch", "準備済みMP4のキューを確認できません")
            export = provenance.get("export")
            if not isinstance(export, dict):
                raise _CleanupRejected("provenance_mismatch", "実送信MP4の証拠がありません")
            result = YouTubeUploadResult(
                video_id=str(provenance.get("video_id", "")),
                watch_url=str(provenance.get("watch_url", "")),
                privacy_status=upload.metadata.privacy.value,
                media_path=_safe_path(self.paths.exports, export.get("path")),
                media_size_bytes=export.get("size_bytes"),
                media_sha256=export.get("sha256"),
            )
            current_provenance = self._provenance(upload, prepared, result, asset)
            if current_provenance != provenance:
                raise _CleanupRejected("provenance_mismatch", "保存された出自と現在のファイル・投稿結果が一致しません")
            outcome = self._delete(upload, asset, provenance, request=request)
            if outcome.status in {"deleted", "already_deleted"}:
                self.lifecycle.update_cleanup_request(request.request_id, state="completed")
            elif outcome.status != "retry_pending":
                # journal開始後の障害は新規要求を作らず、通常起動の回復へ渡します。
                self.lifecycle.update_cleanup_request(request.request_id, skip_reason=outcome.reason)
            return outcome
        except _CleanupRejected as exc:
            try:
                self.lifecycle.update_cleanup_request(
                    request.request_id, state="cancelled", skip_reason=exc.reason,
                )
            except Exception as record_error:
                return UploadCleanupOutcome("failed", "retry_record_failed", f"再試行取消を記録できません: {record_error}")
            return UploadCleanupOutcome("skipped", exc.reason, f"元動画を保持しました: {exc}")
        except Exception as exc:
            return UploadCleanupOutcome("failed", "cleanup_failed", f"再試行の後処理に失敗しました: {exc}")

    def _enabled(self) -> bool:
        loaded = self.config_loader()
        enabled = loaded.config.upload_delete_source_after_upload
        if not isinstance(enabled, bool):
            raise _CleanupRejected("config_invalid", "投稿後削除設定がboolではありません")
        if loaded.config_loaded is not True:
            return False
        return enabled

    def _saved_upload(self, upload_id: str, recording_id: str, queue_id: str | None) -> YouTubeUpload:
        saved = self.uploads.get(upload_id)
        if (saved is None or saved.state is not YouTubeUploadState.COMPLETED
                or not saved.video_id or not saved.watch_url
                or saved.recording_id != recording_id or not queue_id
                or saved.prepare_queue_id != queue_id):
            raise _CleanupRejected("completion_not_verified", "完了した投稿結果の保存・参照を確認できません")
        return saved

    def _provenance(
        self, upload: YouTubeUpload, prepared: UploadQueueItem,
        result: YouTubeUploadResult, asset: VideoAsset,
    ) -> dict[str, object]:
        current = self.queue.get(prepared.queue_id)
        if (current is None or current.state is not UploadQueueState.COMPLETED
                or current.recording_id != upload.recording_id
                or current.queue_id != upload.prepare_queue_id
                or current.export_path != prepared.export_path
                or current.manifest_path != prepared.manifest_path
                or current.export_path is None or current.manifest_path is None):
            raise _CleanupRejected("provenance_mismatch", "投稿に使った準備キューの参照が一致しません")
        export_path = _safe_path(self.paths.root, current.export_path)
        manifest_path = _safe_path(self.paths.root, current.manifest_path)
        _relative(export_path, self.paths.exports)
        _relative(manifest_path, self.paths.queue)
        if (result.media_path is None or not isinstance(result.media_size_bytes, int)
                or isinstance(result.media_size_bytes, bool)
                or result.media_size_bytes <= 0 or not isinstance(result.media_sha256, str)
                or result.media_path.resolve() != export_path):
            raise _CleanupRejected("sent_media_unverified", "実際に送信したMP4の証拠を確認できません")
        export_proof = _file_proof(export_path, self.paths.exports)
        if (export_proof["size_bytes"] != result.media_size_bytes
                or export_proof["sha256"] != result.media_sha256):
            raise _CleanupRejected("export_changed", "投稿したMP4の内容が変わりました")
        manifest_proof, document = _manifest_proof(manifest_path, self.paths.queue)
        validate_upload_manifest(document)
        if (document["queue_id"] != current.queue_id
                or document["recording_id"] != upload.recording_id):
            raise _CleanupRejected("provenance_mismatch", "マニフェストの録画・キュー参照が一致しません")
        source_document = document["source"]
        export_document = document["export"]
        assert isinstance(source_document, dict) and isinstance(export_document, dict)
        manifest_export = _safe_path(self.paths.root, export_document["path"])
        if (manifest_export != export_path or export_document["size_bytes"] != result.media_size_bytes
                or export_document["sha256"] != result.media_sha256):
            raise _CleanupRejected("provenance_mismatch", "マニフェストが実送信MP4と一致しません")
        history = RecordingHistoryRepository.from_runtime_paths(self.paths).get(upload.recording_id)
        if history is None or history.output_path.as_posix() != Path(asset.path).as_posix():
            raise _CleanupRejected("asset_changed", "現在の録画履歴と元動画の実体が一致しません")
        source_path = _safe_path(self.paths.recordings, asset.path)
        if _safe_path(self.paths.root, source_document["path"]) != source_path:
            raise _CleanupRejected("source_changed", "準備時と現在の元動画の保存先が一致しません")
        source_proof = _file_proof(source_path, self.paths.recordings)
        if (source_document["size_bytes"] != source_proof["size_bytes"]
                or source_document["sha256"] != source_proof["sha256"]):
            raise _CleanupRejected("source_changed", "準備時と現在の元動画の内容が一致しません")
        return {
            "schema": PROVENANCE_SCHEMA, "source": source_proof, "export": export_proof,
            "manifest": manifest_proof, "upload_id": upload.upload_id,
            "queue_id": current.queue_id, "recording_id": upload.recording_id,
            "video_id": upload.video_id, "watch_url": upload.watch_url,
            "asset_id": asset.asset_id, "generation": asset.generation,
        }

    def _delete(
        self, upload: YouTubeUpload, asset: VideoAsset, provenance: dict[str, object],
        *, request: UploadCleanupRequest | None = None,
    ) -> UploadCleanupOutcome:
        source = provenance["source"]
        assert isinstance(source, dict)
        deletion = self.deletion.delete_video(
            upload.recording_id, reason="youtube_uploaded",
            expected_asset_id=asset.asset_id, expected_generation=asset.generation,
            expected_sha256=source["sha256"], upload_id=upload.upload_id,
            prepare_queue_id=upload.prepare_queue_id, provenance=provenance,
        )
        if (deletion.status in {"skipped", "failed"}
                and deletion.retryable and not deletion.journal_started):
            try:
                if request is None:
                    request = self.lifecycle.request_cleanup(
                        recording_id=upload.recording_id, asset_id=asset.asset_id,
                        generation=asset.generation, upload_id=upload.upload_id,
                        prepare_queue_id=upload.prepare_queue_id, provenance=provenance,
                        skip_reason=deletion.reason,
                    )
                else:
                    self.lifecycle.update_cleanup_request(request.request_id, skip_reason=deletion.reason)
            except Exception as exc:
                return UploadCleanupOutcome("failed", "retry_record_failed", f"元動画を保持しました。再試行要求を記録できません: {exc}")
            return UploadCleanupOutcome(
                "retry_pending", deletion.reason,
                f"{deletion.message} 次回起動時の再試行を記録しました。", request.request_id,
            )
        if request is not None and deletion.journal_started and deletion.status == "failed":
            self.lifecycle.update_cleanup_request(
                request.request_id, state="cancelled",
                skip_reason=f"{deletion.reason}: 削除journalの回復へ引き継ぎました",
            )
        return UploadCleanupOutcome(deletion.status, deletion.reason, deletion.message)

    def _keep_request(self, request: UploadCleanupRequest, reason: str, message: str) -> UploadCleanupOutcome:
        self.lifecycle.update_cleanup_request(request.request_id, skip_reason=reason)
        return UploadCleanupOutcome("skipped", reason, message, request.request_id)


def _safe_path(root: Path, value: object) -> Path:
    if not isinstance(value, (str, Path)) or not str(value):
        raise _CleanupRejected("provenance_mismatch", "ファイルの相対パスがありません")
    relative = Path(value)
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise _CleanupRejected("unsafe_path", "ファイルのパスが安全な相対パスではありません")
    path = (root / relative).resolve()
    _relative(path, root)
    return path


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise _CleanupRejected("unsafe_path", "ファイルが許可された保存先の外側を指しています") from exc


def _stable_identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    if not path.is_file() or stat.st_size <= 0:
        raise _CleanupRejected("file_missing", "ファイルが存在しないか空です")
    return stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino


def _file_proof(path: Path, root: Path) -> dict[str, object]:
    before = _stable_identity(path)
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if _stable_identity(path) != before:
        raise _CleanupRejected("file_changed", "ファイルの内容確認中に実体が変わりました")
    return {"path": _relative(path, root), "size_bytes": before[0], "sha256": digest}


def _manifest_proof(path: Path, root: Path) -> tuple[dict[str, object], object]:
    before = _stable_identity(path)
    if before[0] > MAX_MANIFEST_BYTES:
        raise _CleanupRejected("manifest_invalid", "マニフェストが安全上限を超えています")
    with path.open("rb") as handle:
        data = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES or _stable_identity(path) != before:
        raise _CleanupRejected("file_changed", "マニフェストの読込中に実体が変わりました")
    proof = {"path": _relative(path, root), "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return proof, json.loads(data.decode("utf-8"))
