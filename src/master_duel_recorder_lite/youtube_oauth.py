from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
import base64
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import hashlib
import re


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_CREDENTIAL_TARGET = "master-duel-recorder-lite/youtube-oauth"
YOUTUBE_OAUTH_CLIENT_ID_ENV = "MDRL_YOUTUBE_OAUTH_CLIENT_ID"
YOUTUBE_OAUTH_CLIENT_SECRET_ENV = "MDRL_YOUTUBE_OAUTH_CLIENT_SECRET"
YOUTUBE_OAUTH_BUNDLED_CLIENT_FILE = "youtube-oauth-client.json"


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
                client_secret=str(document.get("client_secret", "")).strip(),
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
    auth_uri: str
    token_uri: str
    client_secret: str = ""


@dataclass(frozen=True)
class OAuthLoopbackResult:
    credentials: YouTubeCredentials
    redirect_uri: str


@dataclass(frozen=True)
class OAuthCallbackResult:
    code: str
    state: str


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
        client_secret=str(client.get("client_secret", "")).strip(),
        auth_uri=_required_text(
            client.get("auth_uri", "https://accounts.google.com/o/oauth2/v2/auth"),
            "auth_uri",
        ),
        token_uri=_required_text(
            client.get("token_uri", "https://oauth2.googleapis.com/token"),
            "token_uri",
        ),
    )


def load_distributed_oauth_client(
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path | None = None,
) -> OAuthClientInfo:
    """配布アプリに組み込むOAuth Client情報を読み込みます。

    client_idは秘密情報ではありません。実配布ではビルド時に同梱するか、検証時だけ
    環境変数で与えます。refresh tokenはこの関数では扱いません。
    """

    environment = os.environ if environ is None else environ
    client_id = environment.get(YOUTUBE_OAUTH_CLIENT_ID_ENV, "").strip()
    if client_id:
        return OAuthClientInfo(
            client_id=client_id,
            client_secret=environment.get(YOUTUBE_OAUTH_CLIENT_SECRET_ENV, "").strip(),
            auth_uri="https://accounts.google.com/o/oauth2/v2/auth",
            token_uri="https://oauth2.googleapis.com/token",
        )
    candidates = _distributed_oauth_client_candidates(project_root=project_root)
    for candidate in candidates:
        if candidate.is_file():
            return load_client_secrets(candidate)
    raise YouTubeOAuthError(
        "YouTube OAuthクライアントが未設定です。配布ビルドへOAuth client_idを組み込んでください。"
    )


def distributed_oauth_client_configured(
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path | None = None,
) -> bool:
    try:
        load_distributed_oauth_client(environ=environ, project_root=project_root)
    except YouTubeOAuthError:
        return False
    return True


def _distributed_oauth_client_candidates(*, project_root: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if project_root is not None:
        candidates.append(project_root / "assets" / YOUTUBE_OAUTH_BUNDLED_CLIENT_FILE)
    frozen_root = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_root, str):
        candidates.append(Path(frozen_root) / "assets" / YOUTUBE_OAUTH_BUNDLED_CLIENT_FILE)
    candidates.append(Path(__file__).resolve().parents[2] / "assets" / YOUTUBE_OAUTH_BUNDLED_CLIENT_FILE)
    return candidates


def new_pkce_code_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def pkce_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def authorization_url(
    client: OAuthClientInfo,
    *,
    redirect_uri: str,
    state: str,
    code_challenge: str | None = None,
) -> str:
    query: dict[str, str] = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": YOUTUBE_UPLOAD_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    if code_challenge:
        query["code_challenge"] = code_challenge
        query["code_challenge_method"] = "S256"
    query = urllib.parse.urlencode(
        query
    )
    return f"{client.auth_uri}?{query}"


def exchange_authorization_code(
    client: OAuthClientInfo,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str | None = None,
) -> YouTubeCredentials:
    fields = {
        "client_id": client.client_id,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client.client_secret:
        fields["client_secret"] = client.client_secret
    if code_verifier:
        fields["code_verifier"] = code_verifier
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        client.token_uri,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise YouTubeOAuthError(_oauth_http_error_message(exc)) from exc
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


def authorize_with_loopback(
    client: OAuthClientInfo,
    *,
    timeout_seconds: float = 180.0,
    open_browser: Callable[[str], None] = open_authorization_url,
    state: str | None = None,
) -> OAuthLoopbackResult:
    verifier = new_pkce_code_verifier()
    expected_state = state or secrets.token_urlsafe(24)
    server = _OAuthCallbackServer(("127.0.0.1", _free_loopback_port()), expected_state)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
    url = authorization_url(
        client,
        redirect_uri=redirect_uri,
        state=expected_state,
        code_challenge=pkce_code_challenge(verifier),
    )
    thread = threading.Thread(
        target=server.serve_until_callback,
        name="mdrl-youtube-oauth",
        daemon=True,
    )
    thread.start()
    try:
        open_browser(url)
        callback = server.wait_for_callback(timeout_seconds)
    finally:
        server.stop()
        thread.join(timeout=1.0)
        server.server_close()
    credentials = exchange_authorization_code(
        client,
        code=callback.code,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
    )
    return OAuthLoopbackResult(credentials=credentials, redirect_uri=redirect_uri)


def _required_text(value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise YouTubeOAuthError(f"{key} は空でない文字列である必要があります")
    return value.strip()


def _oauth_http_error_message(error: urllib.error.HTTPError) -> str:
    raw_detail = error.read().decode("utf-8", errors="replace")
    reason, description = _oauth_error_details(raw_detail)
    description = _redact_oauth_diagnostics(description) if description else ""
    detail = _redact_oauth_diagnostics(raw_detail)[-1000:]
    guidance = _oauth_error_guidance(reason)
    message = f"OAuth token交換に失敗しました: HTTP {error.code}"
    if reason:
        message = f"{message} {reason}"
    if description:
        message = f"{message}: {description}"
    if guidance:
        message = f"{message}。{guidance}"
    if detail and detail not in {reason, description}:
        message = f"{message} / detail: {detail}"
    return message


def _oauth_error_details(content: str) -> tuple[str, str]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return "", content
    if not isinstance(parsed, dict):
        return "", content
    raw_error = parsed.get("error", "")
    reason = raw_error if isinstance(raw_error, str) else ""
    raw_description = parsed.get("error_description", "")
    description = raw_description if isinstance(raw_description, str) else ""
    return reason, description


def _oauth_error_guidance(reason: str) -> str:
    return {
        "invalid_grant": (
            "認可をやり直してください。再発する場合はredirect_uriとPKCE設定を確認してください"
        ),
        "redirect_uri_mismatch": (
            "Google Cloud ConsoleのOAuth ClientがDesktop app用か確認してください"
        ),
        "invalid_client": (
            "配布EXEに組み込まれたOAuth client_idが有効か確認してください"
        ),
        "invalid_request": (
            "OAuth要求パラメータが不足または不正です。配布者へ診断情報を共有してください"
        ),
        "access_denied": "Google認可画面で許可されていません。許可して再試行してください",
    }.get(reason, "")


def _redact_oauth_diagnostics(value: str) -> str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        return json.dumps(_redact_oauth_value(parsed), ensure_ascii=False, sort_keys=True)
    redacted = value
    redacted = re.sub(
        r"(?i)(code|authorization_code|refresh_token|access_token|client_secret)"
        r"([\"'\s:=]+)([^&\s\"',}]+)",
        r"\1\2[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(code|authorization_code|refresh_token|access_token|client_secret)=([^&\s\"']+)",
        r"\1=[REDACTED]",
        redacted,
    )
    return redacted


def _redact_oauth_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if _is_oauth_secret_key(str(key))
            else _redact_oauth_value(raw_value)
            for key, raw_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_oauth_value(item) for item in value]
    return value


def _is_oauth_secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return (
        normalized
        in {
            "access_token",
            "authorization",
            "authorization_code",
            "client_secret",
            "code",
            "refresh_token",
            "token",
        }
        or normalized.endswith("_token")
        or normalized.endswith("_secret")
    )


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _OAuthCallbackServer(HTTPServer):
    def __init__(self, server_address: tuple[str, int], expected_state: str) -> None:
        super().__init__(server_address, _OAuthCallbackHandler)
        self.expected_state = expected_state
        self.callback: OAuthCallbackResult | None = None
        self.error: YouTubeOAuthError | None = None
        self._event = threading.Event()
        self._stop_event = threading.Event()
        self.timeout = 0.2

    def serve_until_callback(self) -> None:
        while not self._event.is_set() and not self._stop_event.is_set():
            self.handle_request()

    def stop(self) -> None:
        self._stop_event.set()

    def wait_for_callback(self, timeout_seconds: float) -> OAuthCallbackResult:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._event.wait(0.05):
                break
        if not self._event.is_set():
            raise YouTubeOAuthError("YouTube OAuth認証がタイムアウトしました")
        if self.error is not None:
            raise self.error
        if self.callback is None:
            raise YouTubeOAuthError("YouTube OAuth認証コードを受信できませんでした")
        return self.callback

    def complete(self, callback: OAuthCallbackResult | None, error: YouTubeOAuthError | None) -> None:
        self.callback = callback
        self.error = error
        self._event.set()


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: _OAuthCallbackServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        if parsed.path != "/callback":
            self._respond(HTTPStatus.NOT_FOUND, "Master Duel Recorder Lite OAuth callback path is invalid.")
            return
        error = query.get("error", [""])[0]
        if error:
            self.server.complete(None, YouTubeOAuthError(f"YouTube OAuth認証が拒否されました: {error}"))
            self._respond(HTTPStatus.BAD_REQUEST, "YouTube authorization was rejected. You can close this tab.")
            return
        state = query.get("state", [""])[0]
        code = query.get("code", [""])[0]
        if state != self.server.expected_state:
            self.server.complete(None, YouTubeOAuthError("YouTube OAuth stateが一致しません"))
            self._respond(HTTPStatus.BAD_REQUEST, "OAuth state mismatch. You can close this tab.")
            return
        if not code:
            self.server.complete(None, YouTubeOAuthError("YouTube OAuth認証コードがありません"))
            self._respond(HTTPStatus.BAD_REQUEST, "OAuth authorization code is missing. You can close this tab.")
            return
        self.server.complete(OAuthCallbackResult(code=code, state=state), None)
        self._respond(HTTPStatus.OK, "YouTube authorization completed. You can close this tab.")

    def log_message(self, _format: str, *args: object) -> None:
        return

    def _respond(self, status: HTTPStatus, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
