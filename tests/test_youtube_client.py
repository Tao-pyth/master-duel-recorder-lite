import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from master_duel_recorder_lite.upload_metadata import UploadMetadata, UploadPrivacy
from master_duel_recorder_lite.youtube_client import (
    FakeYouTubeClient,
    HttpYouTubeClient,
    YouTubeClientError,
    YouTubeUploadFailureKind,
    _classified_http_error,
    redact_youtube_diagnostics,
    user_action_message,
)
from master_duel_recorder_lite.youtube_oauth import YouTubeCredentials


class YouTubeClientTest(unittest.TestCase):
    def test_fake_client_returns_watch_url(self) -> None:
        client = FakeYouTubeClient()
        with tempfile.TemporaryDirectory() as tmp_dir:
            media = Path(tmp_dir) / "video.mp4"
            media.write_bytes(b"video")

            result = client.upload_video(
                credentials=YouTubeCredentials("client", "secret", "refresh"),
                metadata=UploadMetadata("title", privacy=UploadPrivacy.PUBLIC),
                media_path=media,
            )

        self.assertEqual(result.video_id, "fake-video-id")
        self.assertEqual(result.watch_url, "https://youtu.be/fake-video-id")
        self.assertEqual(result.privacy_status, "public")

    def test_fake_client_can_raise_classified_failures(self) -> None:
        client = FakeYouTubeClient(failures=(YouTubeUploadFailureKind.QUOTA,))
        with tempfile.TemporaryDirectory() as tmp_dir:
            media = Path(tmp_dir) / "video.mp4"
            media.write_bytes(b"video")

            with self.assertRaises(YouTubeClientError) as raised:
                client.upload_video(
                    credentials=YouTubeCredentials("client", "secret", "refresh"),
                    metadata=UploadMetadata("title"),
                    media_path=media,
                )

        self.assertEqual(raised.exception.kind, YouTubeUploadFailureKind.QUOTA)

    def test_http_error_classifies_quota_and_redacts_secret_text(self) -> None:
        error = HTTPError(
            "https://example.test",
            403,
            "Forbidden",
            {},
            BytesIO(
                b'{"error":{"message":"quota exceeded client_secret=abc",'
                b'"errors":[{"reason":"quotaExceeded"}]}}'
            ),
        )

        classified = _classified_http_error(error)

        self.assertEqual(classified.kind, YouTubeUploadFailureKind.QUOTA)
        self.assertIn("quota", user_action_message(classified.kind))
        self.assertNotIn("abc", str(classified))

    def test_redacts_nested_secret_diagnostics(self) -> None:
        redacted = redact_youtube_diagnostics(
            {
                "refresh_token": "secret",
                "nested": {"message": "Bearer abc"},
                "plain": "safe",
            }
        )

        self.assertEqual(redacted["refresh_token"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["message"], "Bearer [REDACTED]")
        self.assertEqual(redacted["plain"], "safe")

    def test_invalid_grant_refresh_requires_reauthorization_without_starting_upload(self) -> None:
        error = HTTPError(
            "https://oauth2.googleapis.com/token", 400, "Bad Request", {},
            BytesIO(b'{"error":"invalid_grant","error_description":"Bad Request"}'),
        )
        client = HttpYouTubeClient()
        with patch("urllib.request.urlopen", side_effect=error) as request, patch.object(
            client, "_start_resumable_upload"
        ) as start:
            with self.assertRaises(YouTubeClientError) as raised:
                client.upload_video(
                    credentials=YouTubeCredentials("fake-client", "fake-secret", "fake-refresh"),
                    metadata=UploadMetadata("title"), media_path=Path("unused.mp4"),
                )
        self.assertEqual(raised.exception.kind, YouTubeUploadFailureKind.REAUTHORIZE)
        self.assertEqual(request.call_count, 1)
        start.assert_not_called()
        self.assertIn("設定 → YouTube", user_action_message(raised.exception.kind))

    def test_http_classification_preserves_other_failures(self) -> None:
        cases = (
            (400, b'{"error":"invalid_client"}', YouTubeUploadFailureKind.PERMANENT),
            (400, b'{"error":{"message":"invalid title"}}', YouTubeUploadFailureKind.PERMANENT),
            (401, b'{"error":{"message":"unauthorized"}}', YouTubeUploadFailureKind.REAUTHORIZE),
            (403, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}', YouTubeUploadFailureKind.QUOTA),
            (403, b'{"error":{"message":"access denied"}}', YouTubeUploadFailureKind.FORBIDDEN),
            (429, b'{"error":{"errors":[{"reason":"rateLimitExceeded"}]}}', YouTubeUploadFailureKind.QUOTA),
            (503, b'{"error":{"message":"unavailable"}}', YouTubeUploadFailureKind.RETRIABLE),
        )
        for status, body, kind in cases:
            with self.subTest(status=status, body=body):
                error = HTTPError("https://example.test", status, "failure", {}, BytesIO(body))
                self.assertEqual(_classified_http_error(error).kind, kind)

    def test_json_oauth_diagnostics_redact_nested_credentials(self) -> None:
        error = HTTPError(
            "https://oauth2.googleapis.com/token", 400, "Bad Request", {},
            BytesIO(
                b'{"error":"invalid_grant","client_secret":"secret-value",'
                b'"nested":{"refresh_token":"refresh-value","message":"Bearer access-value"}}'
            ),
        )
        classified = _classified_http_error(error)
        self.assertEqual(classified.kind, YouTubeUploadFailureKind.REAUTHORIZE)
        for secret in ("secret-value", "refresh-value", "access-value"):
            self.assertNotIn(secret, str(classified))
        self.assertIn("[REDACTED]", str(classified))


if __name__ == "__main__":
    unittest.main()
