from rest_framework import serializers

from apps.markets.models import MarketOutcome
from .constants import CREDIT_SCALE, MAX_PRICE, MIN_PRICE
from .models import ActivityEvent, Order, Position, Trade


class OrderSerializer(serializers.ModelSerializer):
    remaining_quantity = serializers.IntegerField(read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "outcome",
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


class TradeSerializer(serializers.ModelSerializer):
    price_decimal = serializers.SerializerMethodField()

    class Meta:
        model = Trade
        fields = [
            "id",
            "execution_id",
            "outcome",
            "price",
            "price_decimal",
            "quantity",
            "created_at",
        ]
        read_only_fields = fields

    def get_price_decimal(self, obj):
        return obj.price / CREDIT_SCALE


class PositionSerializer(serializers.ModelSerializer):
    average_entry_price = serializers.SerializerMethodField()

    class Meta:
        model = Position
        fields = [
            "id",
            "outcome",
            "quantity",
            "reserved_quantity",
            "cost_basis",
            "realized_pnl",
            "average_entry_price",
            "settled_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_average_entry_price(self, obj):
        if not obj.quantity:
            return None
        return (obj.cost_basis / obj.quantity) / CREDIT_SCALE


class ActivityEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivityEvent
        fields = ["id", "event_type", "visibility", "outcome", "payload", "created_at"]
        read_only_fields = fields
