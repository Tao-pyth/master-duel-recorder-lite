import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
import urllib.request
from unittest.mock import patch

from master_duel_recorder_lite.youtube_oauth import (
    MemoryCredentialStore,
    OAuthClientInfo,
    WindowsCredentialStore,
    YouTubeCredentials,
    YouTubeOAuthError,
    authorize_with_loopback,
    authorization_url,
    exchange_authorization_code,
    load_client_secrets,
    load_distributed_oauth_client,
    new_pkce_code_verifier,
    pkce_code_challenge,
)


class _Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class YouTubeOAuthTest(unittest.TestCase):
    def test_memory_store_round_trips_without_config_file(self) -> None:
        store = MemoryCredentialStore()
        credentials = YouTubeCredentials(
            client_id="client",
            client_secret="secret",
            refresh_token="refresh",
        )

        store.write(credentials)
        self.assertEqual(store.read(), credentials)
        store.delete()
        self.assertIsNone(store.read())

    def test_credentials_can_round_trip_without_client_secret(self) -> None:
        credentials = YouTubeCredentials("client", "", "refresh")

        restored = YouTubeCredentials.from_json(credentials.to_json())

        self.assertEqual(restored.client_id, "client")
        self.assertEqual(restored.client_secret, "")
        self.assertEqual(restored.refresh_token, "refresh")

    def test_windows_store_writes_credential_blob_as_text(self) -> None:
        writes: list[dict[str, object]] = []
        fake_win32cred = SimpleNamespace(
            CRED_TYPE_GENERIC=1,
            CRED_PERSIST_LOCAL_MACHINE=2,
            CredWrite=lambda credential, _flags: writes.append(credential),
        )
        credentials = YouTubeCredentials("client", "secret", "refresh")

        with patch.dict(sys.modules, {"win32cred": fake_win32cred}):
            WindowsCredentialStore("target").write(credentials)

        self.assertEqual(len(writes), 1)
        blob = writes[0]["CredentialBlob"]
        self.assertIsInstance(blob, str)
        self.assertEqual(YouTubeCredentials.from_json(blob), credentials)

    def test_windows_store_reads_utf16_credential_blob(self) -> None:
        credentials = YouTubeCredentials("client", "secret", "refresh")
        fake_win32cred = SimpleNamespace(
            CRED_TYPE_GENERIC=1,
            CredRead=lambda _target, _type: {
                "CredentialBlob": credentials.to_json().encode("utf-16-le")
            },
        )

        with patch.dict(sys.modules, {"win32cred": fake_win32cred}):
            restored = WindowsCredentialStore("target").read()

        self.assertEqual(restored, credentials)

    def test_load_client_secrets_supports_installed_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "client.json"
            path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "client",
                            "client_secret": "secret",
                            "auth_uri": "https://example.test/auth",
                            "token_uri": "https://example.test/token",
                        }
                    }
                ),
                encoding="utf-8",
            )

            client = load_client_secrets(path)

        self.assertEqual(client.client_id, "client")
        self.assertEqual(client.client_secret, "secret")

    def test_authorization_url_uses_youtube_upload_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "client.json"
            path.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "client",
                            "client_secret": "secret",
                            "auth_uri": "https://example.test/auth",
                            "token_uri": "https://example.test/token",
                        }
                    }
                ),
                encoding="utf-8",
            )
            client = load_client_secrets(path)

        url = authorization_url(
            client,
            redirect_uri="http://127.0.0.1:8765/callback",
            state="state",
        )

        self.assertIn("youtube.upload", url)
        self.assertIn("access_type=offline", url)

    def test_pkce_code_challenge_is_urlsafe(self) -> None:
        verifier = new_pkce_code_verifier()
        challenge = pkce_code_challenge(verifier)

        self.assertGreaterEqual(len(verifier), 43)
        self.assertNotIn("=", challenge)
        self.assertNotEqual(verifier, challenge)

    def test_distributed_oauth_client_can_come_from_environment(self) -> None:
        client = load_distributed_oauth_client(
            environ={"MDRL_YOUTUBE_OAUTH_CLIENT_ID": "client-id"}
        )

        self.assertEqual(client.client_id, "client-id")
        self.assertEqual(client.client_secret, "")

    def test_distributed_oauth_client_can_come_from_project_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            asset = root / "assets" / "youtube-oauth-client.json"
            asset.parent.mkdir()
            asset.write_text(
                json.dumps(
                    {
                        "installed": {
                            "client_id": "asset-client",
                            "auth_uri": "https://example.test/auth",
                            "token_uri": "https://example.test/token",
                        }
                    }
                ),
                encoding="utf-8",
            )

            client = load_distributed_oauth_client(environ={}, project_root=root)

        self.assertEqual(client.client_id, "asset-client")
        self.assertEqual(client.client_secret, "")

    def test_loopback_authorization_receives_code_without_copy_paste(self) -> None:
        client = OAuthClientInfo(
            client_id="client",
            auth_uri="https://example.test/auth",
            token_uri="https://example.test/token",
        )
        opened: list[str] = []

        def open_browser(url: str) -> None:
            opened.append(url)
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            redirect = query["redirect_uri"][0]
            state = query["state"][0]
            urllib.request.urlopen(f"{redirect}?code=code-1&state={state}", timeout=5).read()

        with patch(
            "master_duel_recorder_lite.youtube_oauth.exchange_authorization_code",
            return_value=YouTubeCredentials("client", "", "refresh"),
        ) as exchange:
            result = authorize_with_loopback(
                client,
                timeout_seconds=5,
                open_browser=open_browser,
            )

        self.assertTrue(opened)
        self.assertEqual(result.credentials.refresh_token, "refresh")
        exchange.assert_called_once()
        self.assertIn("127.0.0.1", result.redirect_uri)

    def test_exchange_authorization_code_reports_redacted_google_error(self) -> None:
        client = OAuthClientInfo(
            client_id="client",
            auth_uri="https://example.test/auth",
            token_uri="https://example.test/token",
        )
        body = json.dumps(
            {
                "error": "invalid_grant",
                "error_description": "Bad Request",
                "code": "secret-code",
                "client_secret": "secret-value",
            }
        ).encode()

        def fail(_request, timeout):
            raise HTTPError(
                "https://example.test/token",
                400,
                "Bad Request",
                hdrs=None,
                fp=BytesIO(body),
            )

        with patch("urllib.request.urlopen", fail):
            with self.assertRaises(YouTubeOAuthError) as raised:
                exchange_authorization_code(
                    client,
                    code="secret-code",
                    redirect_uri="http://127.0.0.1:1234/callback",
                    code_verifier="verifier",
                )

        message = str(raised.exception)
        self.assertIn("HTTP 400 invalid_grant", message)
        self.assertIn("認可をやり直してください", message)
        self.assertNotIn("secret-code", message)
        self.assertNotIn("secret-value", message)

    def test_exchange_authorization_code_sends_pkce_and_redirect_uri(self) -> None:
        client = OAuthClientInfo(
            client_id="client",
            auth_uri="https://example.test/auth",
            token_uri="https://example.test/token",
        )
        captured: dict[str, list[str]] = {}

        def succeed(request, timeout):
            captured.update(parse_qs(request.data.decode("utf-8")))  # type: ignore[union-attr]
            return _Response(json.dumps({"refresh_token": "refresh"}).encode())

        with patch("urllib.request.urlopen", succeed):
            credentials = exchange_authorization_code(
                client,
                code="code",
                redirect_uri="http://127.0.0.1:1234/callback",
                code_verifier="verifier",
            )

        self.assertEqual(credentials.refresh_token, "refresh")
        self.assertEqual(captured["redirect_uri"], ["http://127.0.0.1:1234/callback"])
        self.assertEqual(captured["code_verifier"], ["verifier"])
        self.assertNotIn("client_secret", captured)


if __name__ == "__main__":
    unittest.main()
