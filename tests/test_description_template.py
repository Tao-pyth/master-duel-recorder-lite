import unittest
import tempfile
from pathlib import Path

from master_duel_recorder_lite.description_template import (
    DescriptionTemplateContext,
    DescriptionTemplateError,
    YouTubePostingTemplate,
    load_youtube_posting_template,
    render_description_template,
    render_youtube_posting_template,
    save_youtube_posting_template,
    youtube_template_aliases,
)


class DescriptionTemplateTest(unittest.TestCase):
    def test_renders_allowed_recording_variables(self) -> None:
        context = DescriptionTemplateContext(
            title="対戦記録",
            recording_id="rec-1",
            started_at="2026-08-21T12:00:00+09:00",
            duration="60.0s",
            own_deck="青眼",
            opponent_deck="相剣",
            result="win",
            play_order="first",
            tags=("ランク戦", "共有"),
        )

        rendered = render_description_template(
            "{title}\n{recording_id}\n{own_deck} vs {opponent_deck}\n{tags}",
            context,
        )

        self.assertIn("対戦記録", rendered)
        self.assertIn("青眼 vs 相剣", rendered)
        self.assertIn("ランク戦, 共有", rendered)

    def test_unknown_or_secret_like_variables_are_rejected(self) -> None:
        context = DescriptionTemplateContext(
            title="対戦記録",
            recording_id="rec-1",
            started_at="2026-08-21T12:00:00+09:00",
            duration="60.0s",
        )

        for template in ("{unknown}", "{client_secret}"):
            with self.subTest(template=template):
                with self.assertRaises(DescriptionTemplateError):
                    render_description_template(template, context)

    def test_rendered_description_uses_upload_metadata_validation(self) -> None:
        context = DescriptionTemplateContext(
            title="対戦記録",
            recording_id="rec-1",
            started_at="2026-08-21T12:00:00+09:00",
            duration="60.0s",
        )

        with self.assertRaises(DescriptionTemplateError):
            render_description_template("x" * 5001, context)

    def test_youtube_posting_template_round_trip_and_aliases(self) -> None:
        context = DescriptionTemplateContext(
            title="自動タイトル",
            recording_id="rec-1",
            started_at="2026-08-21T12:00:00+09:00",
            duration="60.0s",
            own_deck="青眼",
            opponent_deck="相剣",
            result="win",
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = Path(tmp_dir)
            saved = save_youtube_posting_template(
                config,
                YouTubePostingTemplate(
                    title="Master Duel {deckname}",
                    description="{recordingid}\n{opponentdeck}",
                    tags="Master Duel, {deckname}, {opponent_deck}",
                ),
            )

            loaded = load_youtube_posting_template(config)
            metadata = render_youtube_posting_template(loaded, context)

        self.assertEqual(saved.title, "Master Duel {deckname}")
        self.assertEqual(metadata.title, "Master Duel 青眼")
        self.assertIn("rec-1", metadata.description)
        self.assertEqual(metadata.tags, ("Master Duel", "青眼", "相剣"))
        self.assertIn(("{deckname}", "自分デッキ"), youtube_template_aliases())

    def test_youtube_posting_template_rejects_unknown_variables(self) -> None:
        context = DescriptionTemplateContext(
            title="自動タイトル",
            recording_id="rec-1",
            started_at="2026-08-21T12:00:00+09:00",
            duration="60.0s",
        )

        with self.assertRaises(DescriptionTemplateError):
            render_youtube_posting_template(
                YouTubePostingTemplate(title="{client_secret}"),
                context,
            )


if __name__ == "__main__":
    unittest.main()
