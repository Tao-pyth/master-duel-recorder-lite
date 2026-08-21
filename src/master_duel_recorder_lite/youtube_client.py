from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
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
    MANUAL_REVIEW = "manual_review"


class YouTubeUserAction(str, Enum):
    RETRY = "retry"
    REAUTHORIZE = "reauthorize"
    WAIT_QUOTA = "wait_quota"
    CHECK_PERMISSION = "check_permission"
    MANUAL_REVIEW = "manual_review"
    NONE = "none"


class YouTubeClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: YouTubeUploadFailureKind,
        user_action: YouTubeUserAction | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.user_action = user_action or _default_user_action(kind)


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
        fields = {
            "client_id": credentials.client_id,
            "refresh_token": credentials.refresh_token,
            "grant_type": "refresh_token",
        }
        if credentials.client_secret:
            fields["client_secret"] = credentials.client_secret
        data = urllib.parse.urlencode(fields).encode("utf-8")
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
            raise YouTubeClientError(
                str(exc), kind=YouTubeUploadFailureKind.RETRIABLE
            ) from exc
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
            raise YouTubeClientError(
                str(exc), kind=YouTubeUploadFailureKind.RETRIABLE
            ) from exc
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
            raise YouTubeClientError(
                str(exc), kind=YouTubeUploadFailureKind.RETRIABLE
            ) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
            raise YouTubeClientError(
                "YouTube upload応答にvideo idがありません。YouTube Studioで投稿状態を確認してください。",
                kind=YouTubeUploadFailureKind.MANUAL_REVIEW,
            )
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
    detail = redact_youtube_diagnostics(error.read().decode("utf-8", errors="replace"))[-1000:]
    reason, message = _google_error_details(detail)
    normalized = f"{reason} {message} {detail}".casefold()
    if error.code == 401:
        kind = YouTubeUploadFailureKind.REAUTHORIZE
    elif error.code in {403, 429} and (
        "quota" in normalized
        or "rate" in normalized
        or reason in {"quotaExceeded", "dailyLimitExceeded", "userRateLimitExceeded"}
    ):
        kind = YouTubeUploadFailureKind.QUOTA
    elif error.code == 403:
        kind = YouTubeUploadFailureKind.FORBIDDEN
    elif 500 <= error.code <= 599:
        kind = YouTubeUploadFailureKind.RETRIABLE
    else:
        kind = YouTubeUploadFailureKind.PERMANENT
    return YouTubeClientError(f"HTTP {error.code}: {detail}", kind=kind)


def user_action_message(kind: YouTubeUploadFailureKind) -> str:
    action = _default_user_action(kind)
    return {
        YouTubeUserAction.RETRY: "通信状態を確認して再試行してください。",
        YouTubeUserAction.REAUTHORIZE: "YouTube連携をやり直してください。",
        YouTubeUserAction.WAIT_QUOTA: "YouTube APIのquotaまたはrate制限が戻ってから再試行してください。",
        YouTubeUserAction.CHECK_PERMISSION: "YouTubeチャンネル、OAuth審査、投稿権限を確認してください。",
        YouTubeUserAction.MANUAL_REVIEW: "YouTube Studioで投稿状態を確認してから再試行または手動処理してください。",
        YouTubeUserAction.NONE: "入力内容を確認してください。",
    }[action]


def redact_youtube_diagnostics(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if _is_secret_key(str(key))
            else redact_youtube_diagnostics(raw_value)
            for key, raw_value in value.items()
        }
    if isinstance(value, list):
        return [redact_youtube_diagnostics(item) for item in value]
    if isinstance(value, str):
        redacted = value
        redacted = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", redacted)
        redacted = re.sub(
            r"(authorization_code|refresh_token|access_token|client_secret)=([^&\s\"']+)",
            r"\1=[REDACTED]",
            redacted,
            flags=re.IGNORECASE,
        )
        return redacted
    return value


def _default_user_action(kind: YouTubeUploadFailureKind) -> YouTubeUserAction:
    return {
        YouTubeUploadFailureKind.RETRIABLE: YouTubeUserAction.RETRY,
        YouTubeUploadFailureKind.REAUTHORIZE: YouTubeUserAction.REAUTHORIZE,
        YouTubeUploadFailureKind.QUOTA: YouTubeUserAction.WAIT_QUOTA,
        YouTubeUploadFailureKind.FORBIDDEN: YouTubeUserAction.CHECK_PERMISSION,
        YouTubeUploadFailureKind.PERMANENT: YouTubeUserAction.NONE,
        YouTubeUploadFailureKind.MANUAL_REVIEW: YouTubeUserAction.MANUAL_REVIEW,
    }[kind]


def _is_secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return (
        normalized in {"access_token", "authorization", "authorization_code", "client_secret", "refresh_token", "token"}
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
    )


def _google_error_details(content: str) -> tuple[str, str]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return "", content
    if not isinstance(parsed, dict):
        return "", content
    error = parsed.get("error")
    if not isinstance(error, dict):
        return "", content
    message = error.get("message", "")
    reason = ""
    errors = error.get("errors", [])
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        raw_reason = errors[0].get("reason", "")
        if isinstance(raw_reason, str):
            reason = raw_reason
    return reason, message if isinstance(message, str) else ""
