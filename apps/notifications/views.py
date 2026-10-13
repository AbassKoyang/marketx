from rest_framework import permissions, viewsets

from .models import UserDevice
from .serializers import UserDeviceSerializer
from .services import register_device


class UserDeviceViewSet(viewsets.ModelViewSet):
    serializer_class = UserDeviceSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return UserDevice.objects.filter(user=self.request.user).order_by("-last_seen_at")

    def perform_create(self, serializer):
        device = register_device(
            user=self.request.user,
            token=serializer.validated_data["token"],
            platform=serializer.validated_data["platform"],
        )
        serializer.instance = device

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active", "last_seen_at"])
