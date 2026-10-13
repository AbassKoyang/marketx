from rest_framework import serializers

from .models import UserDevice


class UserDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = ["id", "token", "platform", "is_active", "last_seen_at", "created_at"]
        read_only_fields = ["id", "is_active", "last_seen_at", "created_at"]
