from datetime import timedelta

from django.db.models import Max, Min, OuterRef, Prefetch, Subquery, Sum
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.trading.constants import CREDIT_SCALE
from apps.trading.models import Trade
from apps.trading.services import close_market, get_order_book_depth, resolve_market
from .media import upload_market_image
from .models import Market, MarketCategory, MarketOutcome, MarketPricePoint
from .serializers import (
    MarketCategorySerializer,
    MarketOutcomeSerializer,
    MarketPricePointSerializer,
    MarketSerializer,
)


RANGES = {
    "1H": timedelta(hours=1),
    "6H": timedelta(hours=6),
    "1D": timedelta(days=1),
    "1W": timedelta(days=7),
    "1M": timedelta(days=30),
}


def _truthy(value):
    return value in {True, "true", "True", "1", 1}


class IsStaffOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_staff)


class MarketCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = MarketCategorySerializer
    permission_classes = [IsStaffOrReadOnly]
    queryset = MarketCategory.objects.filter(is_active=True)
    lookup_field = "slug"


class MarketViewSet(viewsets.ModelViewSet):
    serializer_class = MarketSerializer
    permission_classes = [IsStaffOrReadOnly]
    lookup_field = "slug"

    def get_queryset(self):
        latest_price = (
            MarketPricePoint.objects.filter(outcome=OuterRef("pk"))
            .order_by("-timestamp")
            .values("price")[:1]
        )
        outcomes = MarketOutcome.objects.annotate(
            latest_price_value=Subquery(latest_price)
        ).order_by("display_order", "id")
        queryset = (
            Market.objects.select_related("category", "created_by", "resolution")
            .prefetch_related(Prefetch("outcomes", queryset=outcomes))
            .all()
        )
        if not (self.request.user and self.request.user.is_staff):
            queryset = queryset.exclude(status=Market.Status.DRAFT)
        status_value = self.request.query_params.get("status")
        category = self.request.query_params.get("category")
        market_type = self.request.query_params.get("market_type")
        search = self.request.query_params.get("search")
        featured = self.request.query_params.get("featured")
        if status_value:
            queryset = queryset.filter(status=status_value)
        if category:
            queryset = queryset.filter(category__slug=category)
        if market_type:
            queryset = queryset.filter(market_type=market_type)
        if featured in {"true", "1"}:
            queryset = queryset.filter(is_featured=True)
        if search:
            queryset = queryset.filter(question__icontains=search)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["get"])
    def stats(self, request, slug=None):
        market = self.get_object()
        outcome_ids = market.outcomes.values_list("id", flat=True)
        trades = Trade.objects.filter(outcome_id__in=outcome_ids)
        latest = trades.order_by("-created_at").first()
        depth = {}
        for outcome in market.outcomes.all():
            depth[outcome.slug] = get_order_book_depth(outcome_id=outcome.id, limit=5)
        return Response({
            "latest_price": latest.price / CREDIT_SCALE if latest else None,
            "total_volume": trades.aggregate(total=Sum("quantity"))["total"] or 0,
            "trade_count": trades.count(),
            "first_trade_at": trades.aggregate(value=Min("created_at"))["value"],
            "last_trade_at": trades.aggregate(value=Max("created_at"))["value"],
            "depth": depth,
        })

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def publish(self, request, slug=None):
        market = self.get_object()
        if market.status != Market.Status.DRAFT:
            return Response({"detail": "Only draft markets can be published."}, status=status.HTTP_400_BAD_REQUEST)
        if market.outcomes.filter(is_active=True).count() < 2:
            return Response({"detail": "A market needs at least two active outcomes."}, status=status.HTTP_400_BAD_REQUEST)
        market.status = Market.Status.OPEN
        market.save(update_fields=["status", "updated_at"])
        return Response(MarketSerializer(market, context={"request": request}).data)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def close(self, request, slug=None):
        market = self.get_object()
        if market.status != Market.Status.OPEN:
            return Response({"detail": "Only open markets can be closed."}, status=status.HTTP_400_BAD_REQUEST)
        market = close_market(market_id=market.id)
        return Response(MarketSerializer(market, context={"request": request}).data)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def resolve(self, request, slug=None):
        market = self.get_object()
        evidence_summary = request.data.get("evidence_summary", "")
        if not evidence_summary:
            return Response(
                {"evidence_summary": ["This field is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        is_void = _truthy(request.data.get("is_void", False))
        if not is_void and not request.data.get("winning_outcome"):
            return Response(
                {"winning_outcome": ["This field is required unless the market is void."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resolution = resolve_market(
            market_id=market.id,
            winning_outcome_id=request.data.get("winning_outcome"),
            is_void=is_void,
            resolved_by=request.user,
            evidence_summary=evidence_summary,
            evidence_url=request.data.get("evidence_url", ""),
        )
        return Response({
            "id": resolution.id,
            "market": market.id,
            "winning_outcome": resolution.winning_outcome_id,
            "is_void": resolution.is_void,
            "resolved_at": resolution.resolved_at,
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[permissions.IsAdminUser])
    def upload_image(self, request, slug=None):
        market = self.get_object()
        uploaded_file = request.FILES.get("image")
        if uploaded_file is None:
            return Response({"image": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        market = upload_market_image(market=market, uploaded_file=uploaded_file)
        return Response(MarketSerializer(market, context={"request": request}).data)


class MarketOutcomeViewSet(viewsets.ModelViewSet):
    serializer_class = MarketOutcomeSerializer
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        queryset = MarketOutcome.objects.select_related("market").filter(is_active=True)
        if not (self.request.user and self.request.user.is_staff):
            queryset = queryset.exclude(market__status=Market.Status.DRAFT)
        return queryset

    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        outcome = self.get_object()
        range_key = request.query_params.get("range", "1D").upper()
        try:
            limit = min(int(request.query_params.get("limit", 1000)), 1000)
            offset = int(request.query_params.get("offset", 0))
        except ValueError:
            return Response(
                {"detail": "limit and offset must be integers."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if limit < 1 or offset < 0:
            return Response(
                {"detail": "limit must be positive and offset cannot be negative."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        points = MarketPricePoint.objects.filter(outcome=outcome)
        if range_key != "ALL":
            points = points.filter(timestamp__gte=timezone.now() - RANGES.get(range_key, RANGES["1D"]))
        total = points.count()
        rows = points.order_by("timestamp")[offset:offset + limit]
        return Response({
            "data": MarketPricePointSerializer(rows, many=True).data,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
                "next_cursor": offset + limit if offset + limit < total else None,
            },
        })
