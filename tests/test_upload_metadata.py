import unittest

from master_duel_recorder_lite.upload_metadata import (
    UploadMetadata,
    UploadMetadataError,
    UploadPrivacy,
)


class UploadMetadataTest(unittest.TestCase):
    def test_round_trip_uses_private_by_default(self) -> None:
        metadata = UploadMetadata("対戦記録", "説明", ("Master Duel", "ランク戦"))

        restored = UploadMetadata.from_dict(metadata.to_dict())

        self.assertEqual(restored, metadata)
        self.assertIs(restored.privacy, UploadPrivacy.PRIVATE)

    def test_unlisted_must_be_explicit(self) -> None:
        metadata = UploadMetadata.from_dict(
            {"title": "対戦記録", "privacy": "unlisted", "tags": []}
        )

        self.assertIs(metadata.privacy, UploadPrivacy.UNLISTED)

    def test_public_can_be_explicit_for_youtube_upload(self) -> None:
        metadata = UploadMetadata.from_dict(
            {"title": "対戦記録", "privacy": "public", "tags": []}
        )

        self.assertIs(metadata.privacy, UploadPrivacy.PUBLIC)

    def test_invalid_lengths_duplicates_and_unknown_privacy_are_rejected(self) -> None:
        invalid_values = (
            {"title": ""},
            {"title": "x" * 101},
            {"title": "title", "tags": ["tag", "TAG"]},
            {"title": "title", "privacy": "friends"},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(UploadMetadataError):
                    UploadMetadata.from_dict(value)

    def test_secret_or_unknown_fields_are_rejected(self) -> None:
        for key in ("access_token", "client_secret", "api_key", "oauth"):
            with self.subTest(key=key):
                with self.assertRaises(UploadMetadataError):
                    UploadMetadata.from_dict({"title": "title", key: "secret"})


if __name__ == "__main__":
    unittest.main()
