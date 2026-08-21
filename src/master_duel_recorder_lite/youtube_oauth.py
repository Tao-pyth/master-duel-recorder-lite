from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import urllib.parse
import urllib.request
import webbrowser


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_CREDENTIAL_TARGET = "master-duel-recorder-lite/youtube-oauth"


class YouTubeOAuthError(RuntimeError):
    """YouTube OAuth資格情報を安全に扱えない場合のエラーです。"""


@dataclass(frozen=True)
class YouTubeCredentials:
    client_id: str
    client_secret: str
    refresh_token: str
    scope: str = YOUTUBE_UPLOAD_SCOPE

    def to_json(self) -> str:
        return json.dumps(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "scope": self.scope,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, value: str) -> YouTubeCredentials:
        try:
            document = json.loads(value)
        except json.JSONDecodeError as exc:
            raise YouTubeOAuthError("保存済みYouTube資格情報のJSONが不正です") from exc
        if not isinstance(document, dict):
            raise YouTubeOAuthError("保存済みYouTube資格情報がobjectではありません")
        try:
            return cls(
                client_id=_required_text(document.get("client_id"), "client_id"),
                client_secret=_required_text(document.get("client_secret"), "client_secret"),
                refresh_token=_required_text(document.get("refresh_token"), "refresh_token"),
                scope=_required_text(document.get("scope", YOUTUBE_UPLOAD_SCOPE), "scope"),
            )
        except TypeError as exc:
            raise YouTubeOAuthError(str(exc)) from exc


class CredentialStore:
    def read(self) -> YouTubeCredentials | None:
        raise NotImplementedError

    def write(self, credentials: YouTubeCredentials) -> None:
        raise NotImplementedError

    def delete(self) -> None:
        raise NotImplementedError


class WindowsCredentialStore(CredentialStore):
    def __init__(self, target: str = YOUTUBE_CREDENTIAL_TARGET) -> None:
        self.target = target

    def _module(self):
        try:
            import win32cred  # type: ignore[import-not-found]
        except ImportError as exc:
            raise YouTubeOAuthError(
                "Windows Credential Managerを利用するにはpywin32が必要です"
            ) from exc
        return win32cred

    def read(self) -> YouTubeCredentials | None:
        win32cred = self._module()
        try:
            credential = win32cred.CredRead(self.target, win32cred.CRED_TYPE_GENERIC)
        except Exception:
            return None
        blob = credential.get("CredentialBlob", b"")
        if isinstance(blob, bytes):
            text = blob.decode("utf-8")
        else:
            text = str(blob)
        return YouTubeCredentials.from_json(text)

    def write(self, credentials: YouTubeCredentials) -> None:
        win32cred = self._module()
        win32cred.CredWrite(
            {
                "Type": win32cred.CRED_TYPE_GENERIC,
                "TargetName": self.target,
                "CredentialBlob": credentials.to_json().encode("utf-8"),
                "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
                "UserName": "youtube",
            },
            0,
        )

    def delete(self) -> None:
        win32cred = self._module()
        try:
            win32cred.CredDelete(self.target, win32cred.CRED_TYPE_GENERIC)
        except Exception:
            return


class MemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        self.credentials: YouTubeCredentials | None = None

    def read(self) -> YouTubeCredentials | None:
        return self.credentials

    def write(self, credentials: YouTubeCredentials) -> None:
        self.credentials = credentials

    def delete(self) -> None:
        self.credentials = None


@dataclass(frozen=True)
class OAuthClientInfo:
    client_id: str
    client_secret: str
    auth_uri: str
    token_uri: str


def load_client_secrets(path: Path) -> OAuthClientInfo:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise YouTubeOAuthError(f"OAuth client secretsを読めません: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise YouTubeOAuthError("OAuth client secretsのルートがobjectではありません")
    client = document.get("installed") or document.get("web")
    if not isinstance(client, dict):
        raise YouTubeOAuthError("client secretsにはinstalledまたはweb objectが必要です")
    return OAuthClientInfo(
        client_id=_required_text(client.get("client_id"), "client_id"),
        client_secret=_required_text(client.get("client_secret"), "client_secret"),
        auth_uri=_required_text(
            client.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth"),
            "auth_uri",
        ),
        token_uri=_required_text(
            client.get("token_uri", "https://oauth2.googleapis.com/token"),
            "token_uri",
        ),
    )


def authorization_url(client: OAuthClientInfo, *, redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": YOUTUBE_UPLOAD_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"{client.auth_uri}?{query}"


def exchange_authorization_code(
    client: OAuthClientInfo,
    *,
    code: str,
    redirect_uri: str,
) -> YouTubeCredentials:
    data = urllib.parse.urlencode(
        {
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        client.token_uri,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise YouTubeOAuthError(f"OAuth token交換に失敗しました: {exc}") from exc
    if not isinstance(payload, dict):
        raise YouTubeOAuthError("OAuth token応答がobjectではありません")
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token.strip():
        raise YouTubeOAuthError("refresh_tokenを取得できませんでした。再認可してください")
    return YouTubeCredentials(
        client_id=client.client_id,
        client_secret=client.client_secret,
        refresh_token=refresh_token.strip(),
    )


def open_authorization_url(url: str) -> None:
    webbrowser.open(url)


def _required_text(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise YouTubeOAuthError(f"{key} は空でない文字列である必要があります")
    return value.strip()
