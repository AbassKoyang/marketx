from rest_framework import serializers

from .models import Notification, UserDevice


class UserDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = ["id", "token", "platform", "is_active", "last_seen_at", "created_at"]
        read_only_fields = ["id", "is_active", "last_seen_at", "created_at"]


class NotificationSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "event_type",
            "title",
            "body",
            "data",
            "is_read",
            "read_at",
            "created_at",
        ]
        read_only_fields = fields
