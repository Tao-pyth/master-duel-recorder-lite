import unittest

from master_duel_recorder_lite.clip_export import ClipExportError, resolve_clip_range


class ClipExportTest(unittest.TestCase):
    def test_clip_range_is_clamped_to_recording_bounds(self) -> None:
        clip = resolve_clip_range(
            center_seconds=10.0,
            before_seconds=30.0,
            after_seconds=30.0,
            duration_seconds=45.0,
        )

        self.assertEqual(clip.start_seconds, 0.0)
        self.assertEqual(clip.duration_seconds, 40.0)

    def test_clip_range_clamps_end_to_duration(self) -> None:
        clip = resolve_clip_range(
            center_seconds=40.0,
            before_seconds=5.0,
            after_seconds=30.0,
            duration_seconds=45.0,
        )

        self.assertEqual(clip.start_seconds, 35.0)
        self.assertEqual(clip.duration_seconds, 10.0)

    def test_negative_values_are_rejected(self) -> None:
        with self.assertRaises(ClipExportError):
            resolve_clip_range(
                center_seconds=1.0,
                before_seconds=-1.0,
                after_seconds=1.0,
                duration_seconds=10.0,
            )


if __name__ == "__main__":
    unittest.main()
