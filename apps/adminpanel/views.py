from datetime import timedelta

from django.conf import settings
from django.db.models import (
    BigIntegerField,
    Count,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.account.models import User
from apps.markets.models import Market
from apps.trading.models import Order, Position, Trade
from apps.trading.services import cancel_order
from apps.wallet.models import Wallet, WalletLedgerEntry

from .identity import house_filter
from .serializers import (
    AdminLedgerEntrySerializer,
    AdminOrderSerializer,
    AdminPositionSerializer,
    AdminTradeSerializer,
    AdminUserSerializer,
    AdminWalletSerializer,
)


class AdminPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


def _flag(value):
    if value is None:
        return None
    return value.lower() in {"1", "true", "yes"}


def _subquery(queryset, field, output_field):
    return Coalesce(
        Subquery(queryset.values(field), output_field=output_field),
        0,
    )


HOUSE_EMAIL = settings.MARKETX_HOUSE_EMAIL


def admin_user_queryset():
    """Users annotated with wallet + usage stats (no N+1, no join fan-out)."""
    order_count = (
        Order.objects.filter(user=OuterRef("pk"))
        .order_by()
        .values("user")
        .annotate(total=Count("pk"))
        .values("total")
    )
    position_stats = (
        Position.objects.filter(user=OuterRef("pk"))
        .order_by()
        .values("user")
        .annotate(
            open_count=Count("pk", filter=Q(quantity__gt=0)),
            realized=Sum("realized_pnl"),
            cost=Sum("cost_basis"),
        )
    )
    buy_count = (
        Trade.objects.filter(buyer=OuterRef("pk"))
        .order_by()
        .values("buyer")
        .annotate(total=Count("pk"))
        .values("total")
    )
    sell_count = (
        Trade.objects.filter(seller=OuterRef("pk"))
        .order_by()
        .values("seller")
        .annotate(total=Count("pk"))
        .values("total")
    )

    return User.objects.select_related("wallet").annotate(
        orders_count=_subquery(order_count, "total", IntegerField()),
        open_positions_count=_subquery(
            position_stats, "open_count", IntegerField()
        ),
        realized_pnl=_subquery(position_stats, "realized", BigIntegerField()),
        cost_basis=_subquery(position_stats, "cost", BigIntegerField()),
        buy_trades_count=_subquery(buy_count, "total", IntegerField()),
        sell_trades_count=_subquery(sell_count, "total", IntegerField()),
    )


class AdminOverviewView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        now = timezone.now()
        week_ago = now - timedelta(days=7)
        day_ago = now - timedelta(hours=24)
        customers = User.objects.exclude(house_filter())
        customer_wallets = Wallet.objects.exclude(house_filter("user__"))
        wallet_totals = customer_wallets.aggregate(
            available=Sum("available_balance"),
            reserved=Sum("reserved_balance"),
        )
        # Older databases can retain more than one liquidity account after the
        # house identity was renamed, so the market-maker block aggregates every
        # house account instead of only the configured one.
        house_users = User.objects.filter(house_filter())
        house_primary = (
            house_users.select_related("wallet").filter(email=HOUSE_EMAIL).first()
            or house_users.select_related("wallet").order_by("pk").first()
        )
        house_wallets = Wallet.objects.filter(house_filter("user__")).aggregate(
            available=Sum("available_balance"),
            reserved=Sum("reserved_balance"),
        )
        house_trade_filter = house_filter("buyer__") | house_filter("seller__")
        house_order_filter = Q(client_order_id__startswith="amm:") | house_filter(
            "user__"
        )

        return Response(
            {
                # Customer counts exclude the inactive liquidity account. The
                # market maker is reported separately below.
                "users": {
                    "total": customers.count(),
                    "staff": User.objects.filter(is_staff=True)
                    .exclude(house_filter())
                    .count(),
                    "active": customers.filter(is_active=True).count(),
                    "new_7d": customers.filter(
                        created_at__gte=week_ago
                    ).count(),
                },
                "wallets": {
                    "total": customer_wallets.count(),
                    "available": wallet_totals["available"] or 0,
                    "reserved": wallet_totals["reserved"] or 0,
                },
                "markets": {
                    "total": Market.objects.count(),
                    "open": Market.objects.filter(
                        status=Market.Status.OPEN
                    ).count(),
                    "draft": Market.objects.filter(
                        status=Market.Status.DRAFT
                    ).count(),
                    "closed": Market.objects.filter(
                        status=Market.Status.CLOSED
                    ).count(),
                    "resolved": Market.objects.filter(
                        status=Market.Status.RESOLVED
                    ).count(),
                    "void": Market.objects.filter(
                        status=Market.Status.VOID
                    ).count(),
                },
                "trading": {
                    # Order and trade totals include market-maker activity.
                    "orders": Order.objects.count(),
                    # Only legacy resting orders can still be open. A new
                    # partial fill cancels its remainder immediately.
                    "open_orders": Order.objects.filter(
                        status__in=[
                            Order.Status.OPEN,
                            Order.Status.PARTIALLY_FILLED,
                        ],
                        cancelled_at__isnull=True,
                    ).count(),
                    "trades": Trade.objects.count(),
                    "trades_24h": Trade.objects.filter(
                        created_at__gte=day_ago
                    ).count(),
                    # House inventory is AMM liquidity, not customer exposure.
                    "open_positions": Position.objects.filter(
                        quantity__gt=0
                    )
                    .exclude(house_filter("user__"))
                    .count(),
                },
                "market_maker": {
                    "user_id": house_primary.pk if house_primary else None,
                    "email": house_primary.email if house_primary else HOUSE_EMAIL,
                    "username": house_primary.username if house_primary else None,
                    "accounts": house_users.count(),
                    "available_balance": house_wallets["available"] or 0,
                    "reserved_balance": house_wallets["reserved"] or 0,
                    "orders": Order.objects.filter(house_order_filter).count(),
                    "trades": Trade.objects.filter(house_trade_filter).count(),
                    "open_positions": Position.objects.filter(
                        quantity__gt=0
                    ).filter(house_filter("user__")).count(),
                },
            }
        )


class AdminUserListView(generics.ListAPIView):
    serializer_class = AdminUserSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = AdminPagination

    def get_queryset(self):
        queryset = admin_user_queryset().order_by("-created_at")
        params = self.request.query_params

        search = params.get("search")
        if search:
            queryset = queryset.filter(
                Q(email__icontains=search)
                | Q(username__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
            )

        is_staff = _flag(params.get("is_staff"))
        if is_staff is not None:
            queryset = queryset.filter(is_staff=is_staff)

        is_active = _flag(params.get("is_active"))
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active)

        onboarded = _flag(params.get("onboarded"))
        if onboarded is not None:
            queryset = queryset.filter(is_onboarded=onboarded)

        market_maker = _flag(params.get("market_maker"))
        if market_maker is True:
            queryset = queryset.filter(house_filter())
        elif market_maker is False:
            queryset = queryset.exclude(house_filter())

        return queryset


class AdminUserDetailView(generics.RetrieveAPIView):
    serializer_class = AdminUserSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        return admin_user_queryset()


class AdminUserLedgerView(generics.ListAPIView):
    serializer_class = AdminLedgerEntrySerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = AdminPagination

    def get_queryset(self):
        return WalletLedgerEntry.objects.filter(
            wallet__user_id=self.kwargs["pk"]
        ).order_by("-created_at")


class AdminWalletListView(generics.ListAPIView):
    serializer_class = AdminWalletSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = AdminPagination

    def get_queryset(self):
        queryset = Wallet.objects.select_related("user").order_by(
            "-available_balance", "-reserved_balance"
        )
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search)
                | Q(user__username__icontains=search)
                | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )
        market_maker = _flag(self.request.query_params.get("market_maker"))
        if market_maker is True:
            queryset = queryset.filter(house_filter("user__"))
        elif market_maker is False:
            queryset = queryset.exclude(house_filter("user__"))
        return queryset


class AdminOrderListView(generics.ListAPIView):
    serializer_class = AdminOrderSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = AdminPagination

    def get_queryset(self):
        queryset = Order.objects.select_related(
            "user", "outcome", "outcome__market"
        ).order_by("-created_at")
        params = self.request.query_params

        status_param = params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)

        side = params.get("side")
        if side:
            queryset = queryset.filter(side=side)

        user_id = params.get("user")
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        outcome_id = params.get("outcome")
        if outcome_id:
            queryset = queryset.filter(outcome_id=outcome_id)

        market_id = params.get("market")
        if market_id:
            queryset = queryset.filter(outcome__market_id=market_id)

        search = params.get("search")
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search)
                | Q(user__username__icontains=search)
                | Q(client_order_id__icontains=search)
                | Q(outcome__name__icontains=search)
                | Q(outcome__market__title__icontains=search)
            )

        market_maker = _flag(params.get("market_maker"))
        house_order_filter = Q(client_order_id__startswith="amm:") | house_filter(
            "user__"
        )
        if market_maker is True:
            queryset = queryset.filter(house_order_filter)
        elif market_maker is False:
            queryset = queryset.exclude(house_order_filter)

        return queryset


class AdminOrderCancelView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk)
        order = cancel_order(user=order.user, order_id=order.pk)
        return Response(AdminOrderSerializer(order).data)


class AdminPositionListView(generics.ListAPIView):
    serializer_class = AdminPositionSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = AdminPagination

    def get_queryset(self):
        queryset = Position.objects.select_related(
            "user", "outcome", "outcome__market"
        ).order_by("-updated_at")
        params = self.request.query_params

        user_id = params.get("user")
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        outcome_id = params.get("outcome")
        if outcome_id:
            queryset = queryset.filter(outcome_id=outcome_id)

        market_id = params.get("market")
        if market_id:
            queryset = queryset.filter(outcome__market_id=market_id)

        settled = _flag(params.get("settled"))
        if settled is True:
            queryset = queryset.filter(settled_at__isnull=False)
        elif settled is False:
            queryset = queryset.filter(settled_at__isnull=True)

        search = params.get("search")
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search)
                | Q(user__username__icontains=search)
                | Q(outcome__name__icontains=search)
                | Q(outcome__market__title__icontains=search)
            )

        market_maker = _flag(params.get("market_maker"))
        if market_maker is True:
            queryset = queryset.filter(house_filter("user__"))
        elif market_maker is False:
            queryset = queryset.exclude(house_filter("user__"))

        return queryset


class AdminTradeListView(generics.ListAPIView):
    serializer_class = AdminTradeSerializer
    permission_classes = [permissions.IsAdminUser]
    pagination_class = AdminPagination

    def get_queryset(self):
        queryset = Trade.objects.select_related(
            "buyer", "seller", "outcome", "outcome__market"
        ).order_by("-created_at")
        params = self.request.query_params

        user_id = params.get("user")
        if user_id:
            queryset = queryset.filter(
                Q(buyer_id=user_id) | Q(seller_id=user_id)
            )

        outcome_id = params.get("outcome")
        if outcome_id:
            queryset = queryset.filter(outcome_id=outcome_id)

        market_id = params.get("market")
        if market_id:
            queryset = queryset.filter(outcome__market_id=market_id)

        search = params.get("search")
        if search:
            queryset = queryset.filter(
                Q(buyer__email__icontains=search)
                | Q(buyer__username__icontains=search)
                | Q(seller__email__icontains=search)
                | Q(seller__username__icontains=search)
                | Q(outcome__name__icontains=search)
                | Q(outcome__market__title__icontains=search)
            )

        market_maker = _flag(params.get("market_maker"))
        house_trade_filter = house_filter("buyer__") | house_filter("seller__")
        if market_maker is True:
            queryset = queryset.filter(house_trade_filter)
        elif market_maker is False:
            queryset = queryset.exclude(house_trade_filter)

        return queryset
