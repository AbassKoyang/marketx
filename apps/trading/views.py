from django.db.models import Q, Sum
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import ActivityEvent, Order, Position, Trade
from .serializers import (
    ActivityEventSerializer,
    OrderSerializer,
    PositionSerializer,
    SubmitOrderSerializer,
    TradeSerializer,
)
from .services import cancel_order, get_order_book_depth, submit_limit_order


class OrderViewSet(viewsets.ModelViewSet):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related("outcome", "outcome__market")
            .order_by("-created_at")
        )

    def create(self, request, *args, **kwargs):
        serializer = SubmitOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = submit_limit_order(
            user=request.user,
            outcome_id=serializer.validated_data["outcome"].id,
            side=serializer.validated_data["side"],
            limit_price=serializer.validated_data["limit_price"],
            quantity=serializer.validated_data["quantity"],
            client_order_id=serializer.validated_data["client_order_id"],
        )
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        order = cancel_order(user=request.user, order_id=kwargs["pk"])
        return Response(OrderSerializer(order).data)


class TradeViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TradeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Trade.objects.select_related("outcome").order_by("-created_at")
        outcome_id = self.request.query_params.get("outcome")
        mine = self.request.query_params.get("mine")
        if outcome_id:
            queryset = queryset.filter(outcome_id=outcome_id)
        if mine in {"true", "1"}:
            queryset = queryset.filter(Q(buyer=self.request.user) | Q(seller=self.request.user))
        return queryset


class PositionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PositionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Position.objects.filter(user=self.request.user)
            .select_related("outcome", "outcome__market")
            .order_by("outcome__market__title", "outcome__display_order")
        )

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
        return Response(get_order_book_depth(outcome_id=pk, limit=limit))


class ActivityEventViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ActivityEventSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = ActivityEvent.objects.filter(
            Q(visibility=ActivityEvent.Visibility.PUBLIC)
            | Q(visibility=ActivityEvent.Visibility.PRIVATE, user=self.request.user)
        ).select_related("outcome")
        outcome_id = self.request.query_params.get("outcome")
        if outcome_id:
            queryset = queryset.filter(outcome_id=outcome_id)
        return queryset.order_by("-created_at")
