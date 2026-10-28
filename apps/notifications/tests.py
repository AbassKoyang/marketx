from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from firebase_admin import messaging
from rest_framework.test import APIClient

from .models import Notification, UserDevice
from .services import notify_user, notify_users, send_user_notification


class NotificationApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            email="notify-user@example.com",
            password="password",
            username="notify-user",
        )
        cls.other = User.objects.create_user(
            email="notify-other@example.com",
            password="password",
            username="notify-other",
        )
        cls.unread = Notification.objects.create(
            user=cls.user,
            event_type=Notification.EventType.ORDER_FILLED,
            title="Order filled",
            body="Your order filled.",
            data={"order_id": 1},
        )
        cls.read = Notification.objects.create(
            user=cls.user,
            event_type=Notification.EventType.ORDER_CANCELLED,
            title="Order cancelled",
            body="Your order was cancelled.",
            read_at=timezone.now(),
        )
        cls.other_notification = Notification.objects.create(
            user=cls.other,
            event_type=Notification.EventType.SYSTEM,
            title="Other user's notification",
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)

    def test_list_returns_only_the_current_users_notifications(self):
        response = self.api.get("/api/notifications/")

        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data["results"]}
        self.assertEqual(ids, {self.unread.id, self.read.id})

    def test_list_can_filter_unread_notifications(self):
        response = self.api.get("/api/notifications/?unread=true")

        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.data["results"]]
        self.assertEqual(ids, [self.unread.id])

    def test_list_can_filter_by_event_type(self):
        response = self.api.get("/api/notifications/?event_type=order_cancelled")

        self.assertEqual(response.status_code, 200)
        ids = [row["id"] for row in response.data["results"]]
        self.assertEqual(ids, [self.read.id])

    def test_unread_count(self):
        response = self.api.get("/api/notifications/unread-count/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"unread_count": 1})

    def test_mark_one_notification_read(self):
        response = self.api.post(f"/api/notifications/{self.unread.id}/read/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_read"])
        self.unread.refresh_from_db()
        self.assertIsNotNone(self.unread.read_at)

    def test_mark_all_notifications_read(self):
        response = self.api.post("/api/notifications/mark-all-read/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"updated": 1})
        self.assertEqual(
            Notification.objects.filter(
                user=self.user, read_at__isnull=True
            ).count(),
            0,
        )

    def test_cannot_read_another_users_notification(self):
        response = self.api.post(
            f"/api/notifications/{self.other_notification.id}/read/"
        )

        self.assertEqual(response.status_code, 404)
        self.other_notification.refresh_from_db()
        self.assertIsNone(self.other_notification.read_at)

    def test_requires_authentication(self):
        response = APIClient().get("/api/notifications/")

        self.assertIn(response.status_code, (401, 403))


class NotificationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.user = User.objects.create_user(
            email="notify-service@example.com",
            password="password",
            username="notify-service",
        )
        cls.other = User.objects.create_user(
            email="notify-service-other@example.com",
            password="password",
            username="notify-service-other",
        )

    @patch("apps.notifications.services.send_user_notification")
    def test_notify_user_persists_and_schedules_push_after_commit(self, mock_send):
        with self.captureOnCommitCallbacks() as callbacks:
            notification = notify_user(
                user=self.user,
                event_type=Notification.EventType.SYSTEM,
                title="Hello",
                body="World",
                data={"reference": "abc"},
            )
            self.assertFalse(mock_send.called)

        self.assertEqual(len(callbacks), 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(notification.data, {"reference": "abc"})

        callbacks[0]()

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["title"], "Hello")
        self.assertEqual(kwargs["body"], "World")
        self.assertEqual(kwargs["data"]["event_type"], "system")
        self.assertEqual(kwargs["data"]["reference"], "abc")
        self.assertEqual(kwargs["data"]["notification_id"], str(notification.id))

    @patch("apps.notifications.services.send_user_notification")
    def test_notify_users_deduplicates_recipients(self, mock_send):
        with self.captureOnCommitCallbacks() as callbacks:
            notifications = notify_users(
                user_ids=[self.user.id, self.user.id, self.other.id],
                event_type=Notification.EventType.MARKET_CLOSED,
                title="Market closed",
                body="Trading ended.",
            )

        self.assertEqual(len(notifications), 2)
        self.assertTrue(all(notification.id for notification in notifications))
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(len(callbacks), 2)

        for callback in callbacks:
            callback()
        self.assertEqual(mock_send.call_count, 2)

    @patch("apps.notifications.services.initialize_firebase", return_value=None)
    @patch("apps.notifications.services.messaging.send_each_for_multicast")
    def test_send_skips_when_firebase_is_not_configured(self, mock_send, _mock_init):
        result = send_user_notification(
            user_id=self.user.id,
            title="Hello",
            body="World",
        )

        self.assertEqual(result["skipped"], "firebase_not_configured")
        mock_send.assert_not_called()

    @patch("apps.notifications.services.initialize_firebase", return_value=object())
    def test_send_skips_when_the_user_has_no_devices(self, _mock_init):
        result = send_user_notification(
            user_id=self.user.id,
            title="Hello",
            body="World",
        )

        self.assertEqual(result["skipped"], "no_devices")

    @patch("apps.notifications.services.initialize_firebase", return_value=object())
    @patch("apps.notifications.services.messaging.send_each_for_multicast")
    def test_send_reports_successful_delivery(self, mock_send, _mock_init):
        UserDevice.objects.create(
            user=self.user, token="valid-token", platform=UserDevice.Platform.WEB
        )
        mock_send.return_value = SimpleNamespace(
            success_count=1,
            failure_count=0,
            responses=[SimpleNamespace(success=True, exception=None)],
        )

        result = send_user_notification(
            user_id=self.user.id,
            title="Hello",
            body="World",
            data={"notification_id": 1},
        )

        self.assertEqual(result, {"sent": 1, "failed": 0})
        message = mock_send.call_args.args[0]
        self.assertEqual(message.tokens, ["valid-token"])
        self.assertEqual(message.data, {"notification_id": "1"})

    @patch("apps.notifications.services.initialize_firebase", return_value=object())
    @patch("apps.notifications.services.messaging.send_each_for_multicast")
    def test_send_deactivates_unregistered_tokens(self, mock_send, _mock_init):
        device = UserDevice.objects.create(
            user=self.user, token="stale-token", platform=UserDevice.Platform.WEB
        )
        mock_send.return_value = SimpleNamespace(
            success_count=0,
            failure_count=1,
            responses=[
                SimpleNamespace(
                    success=False,
                    exception=messaging.UnregisteredError("token is gone"),
                )
            ],
        )

        result = send_user_notification(
            user_id=self.user.id,
            title="Hello",
            body="World",
        )

        self.assertEqual(result, {"sent": 0, "failed": 1})
        device.refresh_from_db()
        self.assertFalse(device.is_active)

    @patch("apps.notifications.services.logger")
    @patch("apps.notifications.services.initialize_firebase", return_value=object())
    @patch("apps.notifications.services.messaging.send_each_for_multicast")
    def test_send_handles_transport_errors(
        self, mock_send, _mock_init, _mock_logger
    ):
        UserDevice.objects.create(
            user=self.user, token="valid-token", platform=UserDevice.Platform.WEB
        )
        mock_send.side_effect = RuntimeError("network down")

        result = send_user_notification(
            user_id=self.user.id,
            title="Hello",
            body="World",
        )

        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["error"], "send_failed")
