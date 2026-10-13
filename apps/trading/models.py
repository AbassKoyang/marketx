import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.markets.models import MarketOutcome
from .constants import MAX_PRICE, MIN_PRICE


class Order(models.Model):
    class Side(models.TextChoices):
        BUY = "buy", "Buy"
        SELL = "sell", "Sell"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PARTIALLY_FILLED = "partially_filled", "Partially filled"
        FILLED = "filled", "Filled"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    outcome = models.ForeignKey(
        MarketOutcome,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    side = models.CharField(max_length=4, choices=Side.choices)
    limit_price = models.PositiveIntegerField()
    original_quantity = models.PositiveBigIntegerField()
    filled_quantity = models.PositiveBigIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    client_order_id = models.CharField(max_length=100)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "client_order_id"],
                name="order_unique_user_client_id",
            ),
            models.CheckConstraint(
                condition=Q(limit_price__gte=MIN_PRICE) & Q(limit_price__lte=MAX_PRICE),
                name="order_valid_limit_price",
            ),
            models.CheckConstraint(
                condition=Q(original_quantity__gt=0),
                name="order_positive_original_quantity",
            ),
            models.CheckConstraint(
                condition=Q(filled_quantity__gte=0)
                & Q(filled_quantity__lte=models.F("original_quantity")),
                name="order_valid_filled_quantity",
            ),
        ]
        indexes = [
            models.Index(
                fields=["outcome", "side", "status", "limit_price", "created_at"],
                name="order_book_lookup_idx",
            ),
            models.Index(fields=["user", "status"], name="order_user_status_idx"),
        ]

    @property
    def remaining_quantity(self):
        return self.original_quantity - self.filled_quantity

    @property
    def is_open_for_matching(self):
        return self.status in {self.Status.OPEN, self.Status.PARTIALLY_FILLED}


class Trade(models.Model):
    execution_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    outcome = models.ForeignKey(
        MarketOutcome,
        on_delete=models.PROTECT,
        related_name="trades",
    )
    buy_order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="buy_trades",
    )
    sell_order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="sell_trades",
    )
    buyer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="bought_trades",
    )
    seller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sold_trades",
    )
    price = models.PositiveIntegerField()
    quantity = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=MIN_PRICE) & Q(price__lte=MAX_PRICE),
                name="trade_valid_price",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=0),
                name="trade_positive_quantity",
            ),
            models.CheckConstraint(
                condition=~Q(buyer=models.F("seller")),
                name="trade_distinct_parties",
            ),
        ]
        indexes = [
            models.Index(fields=["outcome", "-created_at"], name="trade_outcome_time_idx"),
            models.Index(fields=["buyer", "-created_at"], name="trade_buyer_time_idx"),
            models.Index(fields=["seller", "-created_at"], name="trade_seller_time_idx"),
        ]


class Position(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="positions",
    )
    outcome = models.ForeignKey(
        MarketOutcome,
        on_delete=models.PROTECT,
        related_name="positions",
    )
    quantity = models.PositiveBigIntegerField(default=0)
    reserved_quantity = models.PositiveBigIntegerField(default=0)
    cost_basis = models.BigIntegerField(default=0)
    realized_pnl = models.BigIntegerField(default=0)
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "outcome"],
                name="position_unique_user_outcome",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=0),
                name="position_quantity_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(reserved_quantity__gte=0)
                & Q(reserved_quantity__lte=models.F("quantity")),
                name="position_reserved_lte_quantity",
            ),
            models.CheckConstraint(
                condition=Q(cost_basis__gte=0),
                name="position_cost_basis_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "outcome"], name="position_user_outcome_idx"),
        ]


class Settlement(models.Model):
    position = models.OneToOneField(
        Position,
        on_delete=models.PROTECT,
        related_name="settlement",
    )
    payout = models.BigIntegerField(default=0)
    idempotency_key = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(payout__gte=0),
                name="settlement_payout_nonnegative",
            ),
        ]


class ActivityEvent(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    event_type = models.CharField(max_length=50)
    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="activity_events",
        null=True,
        blank=True,
    )
    outcome = models.ForeignKey(
        MarketOutcome,
        on_delete=models.CASCADE,
        related_name="activity_events",
        null=True,
        blank=True,
    )
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["visibility", "outcome", "-created_at"], name="activity_public_outcome_idx"),
            models.Index(fields=["user", "-created_at"], name="activity_user_time_idx"),
        ]
