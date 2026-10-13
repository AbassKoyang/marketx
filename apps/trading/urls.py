from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ActivityEventViewSet, OrderBookViewSet, OrderViewSet, PositionViewSet, TradeViewSet

router = DefaultRouter()
router.register("orders", OrderViewSet, basename="order")
router.register("trades", TradeViewSet, basename="trade")
router.register("positions", PositionViewSet, basename="position")
router.register("order-books", OrderBookViewSet, basename="order-book")
router.register("activity", ActivityEventViewSet, basename="activity")

urlpatterns = [
    path("", include(router.urls)),
]
