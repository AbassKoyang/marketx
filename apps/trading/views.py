from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q, Sum
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.markets.models import MarketOutcome
from .models import ActivityEvent, Order, Position, Trade
from .serializers import (
    ActivityEventSerializer,
    OrderSerializer,
    PositionSerializer,
    QuoteRequestSerializer,
    SubmitOrderSerializer,
    TradeSerializer,
)
from .services import (
    cancel_order,
    get_order_book_depth,
    quote_order,
    submit_limit_order,
)


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        queryset = (
            Order.objects.filter(user=self.request.user)
            .select_related("outcome", "outcome__market")
            .order_by("-created_at")
        )
        outcome_id = self.request.query_params.get("outcome")
        if outcome_id:
            queryset = queryset.filter(outcome_id=outcome_id)
        if self.request.query_params.get("active") in {"true", "1"}:
            queryset = queryset.filter(
                status__in=[Order.Status.OPEN, Order.Status.PARTIALLY_FILLED],
                cancelled_at__isnull=True,
            )
        status_value = self.request.query_params.get("status")
        if status_value in Order.Status.values:
            queryset = queryset.filter(status=status_value)
        return queryset

    def create(self, request, *args, **kwargs):
        serializer = SubmitOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            order = submit_limit_order(
                user=request.user,
                outcome_id=serializer.validated_data["outcome"].id,
                side=serializer.validated_data["side"],
                limit_price=serializer.validated_data["limit_price"],
                quantity=serializer.validated_data["quantity"],
                client_order_id=serializer.validated_data["client_order_id"],
            )
        except DjangoValidationError as exc:
            return Response(
                {"detail": exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        try:
            order = cancel_order(user=request.user, order_id=kwargs["pk"])
        except Order.DoesNotExist:
            return Response(
                {"detail": "Order not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(OrderSerializer(order).data)


class TradeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TradeSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        queryset = Trade.objects.select_related("outcome", "outcome__market").order_by("-created_at")
        outcome_id = self.request.query_params.get("outcome")
        mine = self.request.query_params.get("mine")
        if outcome_id:
            queryset = queryset.filter(outcome_id=outcome_id)
        if mine in {"true", "1"}:
            if not self.request.user.is_authenticated:
                return queryset.none()
            queryset = queryset.filter(Q(buyer=self.request.user) | Q(seller=self.request.user))
        return queryset


class PositionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PositionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = (
            Position.objects.filter(user=self.request.user)
            .select_related("outcome", "outcome__market")
            .prefetch_related("outcome__market__outcomes")
            .order_by("outcome__market__title", "outcome__display_order")
        )
        outcome_id = self.request.query_params.get("outcome")
        if outcome_id:
            queryset = queryset.filter(outcome_id=outcome_id)
        return queryset

    @action(detail=False, methods=["get"])
    def summary(self, request):
        positions = self.get_queryset()
        return Response({
            "open_positions": positions.filter(quantity__gt=0).count(),
            "total_cost_basis": positions.aggregate(total=Sum("cost_basis"))["total"] or 0,
            "realized_pnl": positions.aggregate(total=Sum("realized_pnl"))["total"] or 0,
        })


class OrderBookViewSet(viewsets.ViewSet):
    permission_classes = [permissions.AllowAny]

    def retrieve(self, request, pk=None):
        try:
            limit = min(int(request.query_params.get("limit", 20)), 100)
        except ValueError:
            return Response({"detail": "limit must be an integer."}, status=status.HTTP_400_BAD_REQUEST)
        if limit < 1:
            return Response({"detail": "limit must be positive."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            depth = get_order_book_depth(outcome_id=pk, limit=limit)
        except MarketOutcome.DoesNotExist:
            return Response(
                {"detail": "Outcome not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(depth)


class QuoteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = QuoteRequestSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            quote = quote_order(
                user=request.user,
                outcome_id=data["outcome"].id,
                side=data["side"],
                quantity=data["quantity"],
                limit_price=data["limit_price"],
            )
        except DjangoValidationError as exc:
            return Response(
                {"detail": exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(quote)


class ActivityEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ActivityEventSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        if self.request.user.is_authenticated:
            visibility = Q(visibility=ActivityEvent.Visibility.PUBLIC) | Q(
                visibility=ActivityEvent.Visibility.PRIVATE,
                user=self.request.user,
            )
        else:
            visibility = Q(visibility=ActivityEvent.Visibility.PUBLIC)
        queryset = ActivityEvent.objects.filter(visibility).select_related(
            "outcome", "outcome__market"
        )
        outcome_id = self.request.query_params.get("outcome")
        if outcome_id:
            queryset = queryset.filter(outcome_id=outcome_id)
        return queryset.order_by("-created_at")
