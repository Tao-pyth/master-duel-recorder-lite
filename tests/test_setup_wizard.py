import unittest

from master_duel_recorder_lite.setup_wizard import (
    WizardStep,
    WizardStepStatus,
    initial_wizard_state,
    update_wizard_step,
)


class SetupWizardTest(unittest.TestCase):
    def test_initial_state_tracks_next_step(self) -> None:
        state = initial_wizard_state()

        self.assertFalse(state.completed)
        self.assertEqual(state.next_step, WizardStep.FFMPEG)

    def test_completed_config_marks_all_steps_done(self) -> None:
        state = initial_wizard_state(completed=True)

        self.assertTrue(state.completed)
        self.assertIsNone(state.next_step)

    def test_update_step_keeps_other_steps(self) -> None:
        state = update_wizard_step(
            initial_wizard_state(),
            step=WizardStep.FFMPEG,
            status=WizardStepStatus.COMPLETED,
            message="FFmpeg確認済み",
        )

        self.assertEqual(state.steps[0].status, WizardStepStatus.COMPLETED)
        self.assertEqual(state.steps[1].status, WizardStepStatus.WAITING)


if __name__ == "__main__":
    unittest.main()
