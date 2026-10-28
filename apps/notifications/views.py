from django.utils import timezone
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Notification, UserDevice
from .serializers import NotificationSerializer, UserDeviceSerializer
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


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        queryset = Notification.objects.filter(user=self.request.user).order_by(
            "-created_at"
        )
        unread = self.request.query_params.get("unread")
        if unread in {"true", "1"}:
            queryset = queryset.filter(read_at__isnull=True)
        elif unread in {"false", "0"}:
            queryset = queryset.filter(read_at__isnull=False)

        event_type = self.request.query_params.get("event_type")
        if event_type:
            queryset = queryset.filter(event_type=event_type)
        return queryset

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = self.get_queryset().filter(read_at__isnull=True).count()
        return Response({"unread_count": count})

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        updated = self.get_queryset().filter(read_at__isnull=True).update(
            read_at=timezone.now()
        )
        return Response({"updated": updated})
