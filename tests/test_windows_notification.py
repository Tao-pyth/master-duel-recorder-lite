import unittest

from master_duel_recorder_lite.windows_notification import (
    NotificationMessage,
    WindowsNotificationService,
)


class WindowsNotificationServiceTest(unittest.TestCase):
    def test_disabled_and_duplicate_notifications_are_suppressed(self) -> None:
        sent: list[tuple[str, str]] = []
        now = [10.0]
        notification = NotificationMessage("started", "録画開始", "録画中", "record-1:start")
        disabled = WindowsNotificationService(
            enabled=False, sender=lambda title, message: sent.append((title, message))
        )
        self.assertFalse(disabled.notify(notification))
        service = WindowsNotificationService(
            sender=lambda title, message: sent.append((title, message)),
            monotonic=lambda: now[0],
        )
        self.assertTrue(service.notify(notification))
        self.assertFalse(service.notify(notification))
        now[0] += 6
        self.assertTrue(service.notify(notification))
        self.assertEqual(len(sent), 2)

    def test_consecutive_recordings_use_independent_keys(self) -> None:
        sent: list[tuple[str, str]] = []
        service = WindowsNotificationService(sender=lambda *item: sent.append(item))

        self.assertTrue(
            service.notify(NotificationMessage("started", "MDRL", "first", "one:start"))
        )
        self.assertTrue(
            service.notify(NotificationMessage("started", "MDRL", "second", "two:start"))
        )
        self.assertEqual(len(sent), 2)

    def test_close_prevents_late_notifications(self) -> None:
        sent: list[tuple[str, str]] = []
        service = WindowsNotificationService(sender=lambda *item: sent.append(item))
        service.close()

        delivered = service.notify(
            NotificationMessage("failed", "MDRL", "late", "late:failed")
        )

        self.assertFalse(delivered)
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
