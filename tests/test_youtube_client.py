import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

from master_duel_recorder_lite.upload_metadata import UploadMetadata, UploadPrivacy
from master_duel_recorder_lite.youtube_client import (
    FakeYouTubeClient,
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


if __name__ == "__main__":
    unittest.main()
