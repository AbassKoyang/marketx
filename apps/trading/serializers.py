from rest_framework import serializers

from apps.markets.models import MarketOutcome
from .constants import CREDIT_SCALE, MAX_PRICE, MIN_PRICE
from .models import ActivityEvent, Order, Position, Trade


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


class SubmitOrderSerializer(serializers.Serializer):
    outcome = serializers.PrimaryKeyRelatedField(queryset=MarketOutcome.objects.all())
    side = serializers.ChoiceField(choices=Order.Side.choices)
    limit_price = serializers.IntegerField(min_value=MIN_PRICE, max_value=MAX_PRICE)
    quantity = serializers.IntegerField(min_value=1)
    client_order_id = serializers.CharField(max_length=100)


class TradeSerializer(OutcomeContextMixin, serializers.ModelSerializer):
    price_decimal = serializers.SerializerMethodField()

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
            "created_at",
        ]
        read_only_fields = fields

    def get_price_decimal(self, obj):
        return obj.price / CREDIT_SCALE


class PositionSerializer(OutcomeContextMixin, serializers.ModelSerializer):
    average_entry_price = serializers.SerializerMethodField()
    last_price = serializers.SerializerMethodField()
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
            "settled_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_average_entry_price(self, obj):
        if not obj.quantity:
            return None
        return (obj.cost_basis / obj.quantity) / CREDIT_SCALE

    def get_last_price(self, obj):
        annotated = getattr(obj, "latest_price", None)
        if annotated is not None:
            return annotated
        return (
            obj.outcome.price_points.order_by("-timestamp")
            .values_list("price", flat=True)
            .first()
        )

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
