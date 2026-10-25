from rest_framework import serializers

from apps.markets.models import MarketOutcome
from .constants import CREDIT_SCALE, MAX_PRICE, MIN_PRICE
from .models import ActivityEvent, Order, Position, Trade
from .pricing import lmsr_prices


class OutcomeContextMixin(serializers.Serializer):
    outcome_name = serializers.SerializerMethodField()
    market_id = serializers.SerializerMethodField()
    market_title = serializers.SerializerMethodField()
    market_slug = serializers.SerializerMethodField()

    def get_outcome_name(self, obj):
        return obj.outcome.name if obj.outcome_id else None

    def get_market_id(self, obj):
        return obj.outcome.market_id if obj.outcome_id else None

    def get_market_title(self, obj):
        return obj.outcome.market.title if obj.outcome_id else None

    def get_market_slug(self, obj):
        return obj.outcome.market.slug if obj.outcome_id else None


class OrderSerializer(OutcomeContextMixin, serializers.ModelSerializer):
    remaining_quantity = serializers.IntegerField(read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "outcome",
            "outcome_name",
            "market_id",
            "market_title",
            "market_slug",
            "side",
            "limit_price",
            "original_quantity",
            "filled_quantity",
            "remaining_quantity",
            "status",
            "client_order_id",
            "cancelled_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["filled_quantity", "status", "cancelled_at", "created_at", "updated_at"]


def _default_limit_price(attrs):
    if attrs.get("limit_price") is None:
        attrs["limit_price"] = (
            MAX_PRICE if attrs["side"] == Order.Side.BUY else MIN_PRICE
        )
    return attrs


class SubmitOrderSerializer(serializers.Serializer):
    outcome = serializers.PrimaryKeyRelatedField(queryset=MarketOutcome.objects.all())
    side = serializers.ChoiceField(choices=Order.Side.choices)
    # Optional slippage cap. Omit it for a market order: buys accept any price
    # up to MAX_PRICE and sells accept any price down to MIN_PRICE.
    limit_price = serializers.IntegerField(
        min_value=MIN_PRICE,
        max_value=MAX_PRICE,
        required=False,
        allow_null=True,
    )
    quantity = serializers.IntegerField(min_value=1)
    client_order_id = serializers.CharField(max_length=100)

    def validate(self, attrs):
        return _default_limit_price(attrs)


class QuoteRequestSerializer(serializers.Serializer):
    outcome = serializers.PrimaryKeyRelatedField(queryset=MarketOutcome.objects.all())
    side = serializers.ChoiceField(choices=Order.Side.choices)
    quantity = serializers.IntegerField(min_value=1)
    limit_price = serializers.IntegerField(
        min_value=MIN_PRICE,
        max_value=MAX_PRICE,
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        return _default_limit_price(attrs)


class TradeSerializer(OutcomeContextMixin, serializers.ModelSerializer):
    price_decimal = serializers.SerializerMethodField()
    notional_decimal = serializers.SerializerMethodField()

    class Meta:
        model = Trade
        fields = [
            "id",
            "execution_id",
            "outcome",
            "outcome_name",
            "market_id",
            "market_title",
            "market_slug",
            "price",
            "price_decimal",
            "quantity",
            "notional",
            "notional_decimal",
            "created_at",
        ]
        read_only_fields = fields

    def get_price_decimal(self, obj):
        return obj.price / CREDIT_SCALE

    def get_notional_decimal(self, obj):
        return obj.notional / CREDIT_SCALE


class PositionSerializer(OutcomeContextMixin, serializers.ModelSerializer):
    average_entry_price = serializers.SerializerMethodField()
    last_price = serializers.SerializerMethodField()
    current_price = serializers.SerializerMethodField()
    current_price_decimal = serializers.SerializerMethodField()
    market_value = serializers.SerializerMethodField()
    unrealized_pnl = serializers.SerializerMethodField()
    market_status = serializers.SerializerMethodField()
    market_closes_at = serializers.SerializerMethodField()

    class Meta:
        model = Position
        fields = [
            "id",
            "outcome",
            "outcome_name",
            "market_id",
            "market_title",
            "market_slug",
            "market_status",
            "market_closes_at",
            "quantity",
            "reserved_quantity",
            "cost_basis",
            "realized_pnl",
            "average_entry_price",
            "last_price",
            "current_price",
            "current_price_decimal",
            "market_value",
            "unrealized_pnl",
            "settled_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_average_entry_price(self, obj):
        if not obj.quantity:
            return None
        return (obj.cost_basis / obj.quantity) / CREDIT_SCALE

    def _current_price_tick(self, obj):
        if not obj.outcome_id:
            return None
        market = obj.outcome.market
        cache = self.context.setdefault("position_price_map", {})
        if market.pk not in cache:
            outcomes = [
                outcome for outcome in market.outcomes.all() if outcome.is_active
            ]
            resolution = getattr(market, "resolution", None)
            if (
                resolution is not None
                and not resolution.is_void
                and resolution.winning_outcome_id
            ):
                cache[market.pk] = {
                    outcome.id: (
                        CREDIT_SCALE
                        if outcome.id == resolution.winning_outcome_id
                        else 0
                    )
                    for outcome in outcomes
                }
            else:
                ticks = lmsr_prices(
                    [outcome.net_shares for outcome in outcomes],
                    market.liquidity_b,
                )
                cache[market.pk] = {
                    outcome.id: tick for outcome, tick in zip(outcomes, ticks)
                }
        return cache[market.pk].get(obj.outcome_id)

    def get_last_price(self, obj):
        # Backwards-compatible alias for the unified LMSR price.
        return self._current_price_tick(obj)

    def get_current_price(self, obj):
        return self._current_price_tick(obj)

    def get_current_price_decimal(self, obj):
        tick = self._current_price_tick(obj)
        return tick / CREDIT_SCALE if tick is not None else None

    def get_market_value(self, obj):
        tick = self._current_price_tick(obj)
        if tick is None:
            return None
        return obj.quantity * tick

    def get_unrealized_pnl(self, obj):
        market_value = self.get_market_value(obj)
        if market_value is None:
            return None
        return market_value - obj.cost_basis

    def get_market_status(self, obj):
        return obj.outcome.market.status if obj.outcome_id else None

    def get_market_closes_at(self, obj):
        return obj.outcome.market.closes_at if obj.outcome_id else None


class ActivityEventSerializer(OutcomeContextMixin, serializers.ModelSerializer):
    class Meta:
        model = ActivityEvent
        fields = [
            "id",
            "event_type",
            "visibility",
            "outcome",
            "outcome_name",
            "market_id",
            "market_title",
            "market_slug",
            "payload",
            "created_at",
        ]
        read_only_fields = fields
