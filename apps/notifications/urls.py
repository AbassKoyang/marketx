from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import NotificationViewSet, UserDeviceViewSet

router = DefaultRouter()
router.register("devices", UserDeviceViewSet, basename="device")
router.register("", NotificationViewSet, basename="notification")

urlpatterns = [
    path("", include(router.urls)),
]
