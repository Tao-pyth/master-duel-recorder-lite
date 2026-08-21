import json
import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.youtube_oauth import (
    MemoryCredentialStore,
    YouTubeCredentials,
    authorization_url,
    load_client_secrets,
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


if __name__ == "__main__":
    unittest.main()
