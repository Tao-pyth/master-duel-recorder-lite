from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from .upload_metadata import UploadMetadata
from .youtube_oauth import YouTubeCredentials


class YouTubeUploadFailureKind(str, Enum):
    RETRIABLE = "retriable"
    REAUTHORIZE = "reauthorize"
    QUOTA = "quota"
    FORBIDDEN = "forbidden"
    PERMANENT = "permanent"


class YouTubeClientError(RuntimeError):
    def __init__(self, message: str, *, kind: YouTubeUploadFailureKind) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class YouTubeUploadResult:
    video_id: str
    watch_url: str
    privacy_status: str
    warning: str | None = None


class YouTubeClient:
    def upload_video(
        self,
        *,
        credentials: YouTubeCredentials,
        metadata: UploadMetadata,
        media_path: Path,
    ) -> YouTubeUploadResult:
        raise NotImplementedError


class FakeYouTubeClient(YouTubeClient):
    def __init__(self, *, failures: tuple[YouTubeUploadFailureKind, ...] = ()) -> None:
        self.failures = list(failures)
        self.uploaded: list[tuple[UploadMetadata, Path]] = []

    def upload_video(
        self,
        *,
        credentials: YouTubeCredentials,
        metadata: UploadMetadata,
        media_path: Path,
    ) -> YouTubeUploadResult:
        if self.failures:
            kind = self.failures.pop(0)
            raise YouTubeClientError(kind.value, kind=kind)
        self.uploaded.append((metadata, media_path))
        return YouTubeUploadResult(
            video_id="fake-video-id",
            watch_url="https://youtu.be/fake-video-id",
            privacy_status=metadata.privacy.value,
        )


class HttpYouTubeClient(YouTubeClient):
    def __init__(self, *, chunk_size: int = 8 * 1024 * 1024) -> None:
        self.chunk_size = chunk_size

    def upload_video(
        self,
        *,
        credentials: YouTubeCredentials,
        metadata: UploadMetadata,
        media_path: Path,
    ) -> YouTubeUploadResult:
        access_token = self._refresh_access_token(credentials)
        upload_url = self._start_resumable_upload(
            access_token=access_token,
            metadata=metadata,
            media_path=media_path,
        )
        return self._send_media(
            access_token=access_token,
            upload_url=upload_url,
            media_path=media_path,
            requested_privacy=metadata.privacy.value,
        )

    def _refresh_access_token(self, credentials: YouTubeCredentials) -> str:
        data = urllib.parse.urlencode(
            {
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "refresh_token": credentials.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise _classified_http_error(exc) from exc
        except OSError as exc:
            raise YouTubeClientError(str(exc), kind=YouTubeUploadFailureKind.RETRIABLE) from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise YouTubeClientError(
                "access_tokenを取得できません",
                kind=YouTubeUploadFailureKind.REAUTHORIZE,
            )
        return token

    def _start_resumable_upload(
        self,
        *,
        access_token: str,
        metadata: UploadMetadata,
        media_path: Path,
    ) -> str:
        body = json.dumps(
            {
                "snippet": {
                    "title": metadata.title,
                    "description": metadata.description,
                    "tags": list(metadata.tags),
                },
                "status": {
                    "privacyStatus": metadata.privacy.value,
                    "selfDeclaredMadeForKids": False,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status&notifySubscribers=false",
            data=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(media_path.stat().st_size),
                "X-Upload-Content-Type": "video/mp4",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                location = response.headers.get("Location")
        except urllib.error.HTTPError as exc:
            raise _classified_http_error(exc) from exc
        except OSError as exc:
            raise YouTubeClientError(str(exc), kind=YouTubeUploadFailureKind.RETRIABLE) from exc
        if not location:
            raise YouTubeClientError(
                "resumable upload URLを取得できません",
                kind=YouTubeUploadFailureKind.RETRIABLE,
            )
        return location

    def _send_media(
        self,
        *,
        access_token: str,
        upload_url: str,
        media_path: Path,
        requested_privacy: str,
    ) -> YouTubeUploadResult:
        size = media_path.stat().st_size
        with media_path.open("rb") as handle:
            data = handle.read()
        request = urllib.request.Request(
            upload_url,
            data=data,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Length": str(size),
                "Content-Type": "video/mp4",
            },
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise _classified_http_error(exc) from exc
        except OSError as exc:
            raise YouTubeClientError(str(exc), kind=YouTubeUploadFailureKind.RETRIABLE) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            raise YouTubeClientError("YouTube upload応答にvideo idがありません", kind=YouTubeUploadFailureKind.PERMANENT)
        video_id = payload["id"]
        actual_privacy = (
            payload.get("status", {}).get("privacyStatus")
            if isinstance(payload.get("status"), dict)
            else requested_privacy
        )
        warning = None
        if isinstance(actual_privacy, str) and actual_privacy != requested_privacy:
            warning = f"YouTube側で公開範囲が{actual_privacy}へ変更されました"
        return YouTubeUploadResult(
            video_id=video_id,
            watch_url=f"https://youtu.be/{video_id}",
            privacy_status=actual_privacy if isinstance(actual_privacy, str) else requested_privacy,
            warning=warning,
        )


def _classified_http_error(error: urllib.error.HTTPError) -> YouTubeClientError:
    detail = error.read().decode("utf-8", errors="replace")[-1000:]
    if error.code == 401:
        kind = YouTubeUploadFailureKind.REAUTHORIZE
    elif error.code == 403 and ("quota" in detail.casefold() or "rate" in detail.casefold()):
        kind = YouTubeUploadFailureKind.QUOTA
    elif error.code == 403:
        kind = YouTubeUploadFailureKind.FORBIDDEN
    elif 500 <= error.code <= 599:
        kind = YouTubeUploadFailureKind.RETRIABLE
    else:
        kind = YouTubeUploadFailureKind.PERMANENT
    return YouTubeClientError(f"HTTP {error.code}: {detail}", kind=kind)
