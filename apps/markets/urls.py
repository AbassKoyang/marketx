from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import MarketCategoryViewSet, MarketOutcomeViewSet, MarketViewSet

router = DefaultRouter()
router.register("categories", MarketCategoryViewSet, basename="market-category")
router.register("markets", MarketViewSet, basename="market")
router.register("outcomes", MarketOutcomeViewSet, basename="market-outcome")

urlpatterns = [
    path("", include(router.urls)),
]
