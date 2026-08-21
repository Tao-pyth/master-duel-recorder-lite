import unittest

from master_duel_recorder_lite.description_template import (
    DescriptionTemplateContext,
    DescriptionTemplateError,
    render_description_template,
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


if __name__ == "__main__":
    unittest.main()
