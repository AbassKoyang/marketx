from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import UserDeviceViewSet

router = DefaultRouter()
router.register("devices", UserDeviceViewSet, basename="device")

urlpatterns = [
    path("", include(router.urls)),
]
