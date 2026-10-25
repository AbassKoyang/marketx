from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    ActivityEventViewSet,
    OrderBookViewSet,
    OrderViewSet,
    PositionViewSet,
    QuoteView,
    TradeViewSet,
)

router = DefaultRouter()
router.register("orders", OrderViewSet, basename="order")
router.register("trades", TradeViewSet, basename="trade")
router.register("positions", PositionViewSet, basename="position")
router.register("order-books", OrderBookViewSet, basename="order-book")
router.register("activity", ActivityEventViewSet, basename="activity")

urlpatterns = [
    path("quote/", QuoteView.as_view(), name="quote"),
    path("", include(router.urls)),
]
