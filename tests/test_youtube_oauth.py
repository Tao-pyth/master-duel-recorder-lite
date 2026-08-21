import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import urllib.request
from unittest.mock import patch

from master_duel_recorder_lite.youtube_oauth import (
    MemoryCredentialStore,
    OAuthClientInfo,
    YouTubeCredentials,
    authorize_with_loopback,
    authorization_url,
    load_client_secrets,
    load_distributed_oauth_client,
    new_pkce_code_verifier,
    pkce_code_challenge,
)


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


if __name__ == "__main__":
    unittest.main()
