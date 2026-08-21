import tempfile
import unittest
from pathlib import Path

from master_duel_recorder_lite.upload_metadata import UploadMetadata, UploadPrivacy
from master_duel_recorder_lite.youtube_client import (
    FakeYouTubeClient,
    YouTubeClientError,
    YouTubeUploadFailureKind,
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


if __name__ == "__main__":
    unittest.main()
