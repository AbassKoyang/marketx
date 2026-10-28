from django.conf import settings
from django.db import models


class UserDevice(models.Model):
    class Platform(models.TextChoices):
        WEB = "web", "Web"
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="devices",
    )
    token = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=20, choices=Platform.choices, default=Platform.WEB)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_active"], name="device_user_active_idx"),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.platform}"


class Notification(models.Model):
    """A durable in-app notification for a single user.

    Push delivery is best effort and is scheduled after the surrounding
    database transaction commits, so a notification is never pushed before
    the event that produced it is durable.
    """

    class EventType(models.TextChoices):
        ORDER_FILLED = "order_filled", "Order filled"
        ORDER_CANCELLED = "order_cancelled", "Order cancelled"
        POSITION_SETTLED = "position_settled", "Position settled"
        MARKET_CLOSED = "market_closed", "Market closed"
        SYSTEM = "system", "System"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    event_type = models.CharField(
        max_length=50,
        choices=EventType.choices,
        db_index=True,
    )
    title = models.CharField(max_length=180)
    body = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "read_at", "-created_at"],
                name="notif_user_read_idx",
            ),
            models.Index(
                fields=["user", "-created_at"],
                name="notif_user_time_idx",
            ),
        ]

    @property
    def is_read(self):
        return self.read_at is not None

    def __str__(self):
        return f"{self.user_id}:{self.event_type}:{self.title}"
