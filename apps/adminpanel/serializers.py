from rest_framework import serializers

from apps.account.models import User
from apps.trading.constants import CREDIT_SCALE
from apps.trading.models import Order, Position, Trade
from apps.wallet.models import Wallet, WalletLedgerEntry


class AdminUserCompactSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "username", "first_name", "last_name", "avatar"]
        read_only_fields = fields


class AdminUserSerializer(serializers.ModelSerializer):
    wallet = serializers.SerializerMethodField()
    trades_count = serializers.SerializerMethodField()
    orders_count = serializers.IntegerField(read_only=True)
    open_positions_count = serializers.IntegerField(read_only=True)
    realized_pnl = serializers.IntegerField(read_only=True)
    cost_basis = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "avatar",
            "is_active",
            "is_staff",
            "is_onboarded",
            "is_verified",
            "auth_provider",
            "created_at",
            "last_login",
            "wallet",
            "orders_count",
            "open_positions_count",
            "trades_count",
            "realized_pnl",
            "cost_basis",
        ]
        read_only_fields = fields

    def get_wallet(self, obj):
        wallet = getattr(obj, "wallet", None)
        if wallet is None:
            return {
                "available_balance": 0,
                "reserved_balance": 0,
                "total_balance": 0,
            }
        return {
            "available_balance": wallet.available_balance,
            "reserved_balance": wallet.reserved_balance,
            "total_balance": wallet.available_balance + wallet.reserved_balance,
        }

    def get_trades_count(self, obj):
        return (getattr(obj, "buy_trades_count", 0) or 0) + (
            getattr(obj, "sell_trades_count", 0) or 0
        )


class AdminWalletSerializer(serializers.ModelSerializer):
    user = AdminUserCompactSerializer(read_only=True)
    total_balance = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = [
            "id",
            "user",
            "available_balance",
            "reserved_balance",
            "total_balance",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_total_balance(self, obj):
        return obj.available_balance + obj.reserved_balance


class AdminLedgerEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletLedgerEntry
        fields = [
            "id",
            "entry_type",
            "available_delta",
            "reserved_delta",
            "reference_type",
            "reference_id",
            "description",
            "created_at",
        ]
        read_only_fields = fields


class AdminOrderSerializer(serializers.ModelSerializer):
    user = AdminUserCompactSerializer(read_only=True)
    remaining_quantity = serializers.IntegerField(read_only=True)
    outcome_name = serializers.CharField(source="outcome.name", read_only=True)
    market_id = serializers.IntegerField(source="outcome.market.id", read_only=True)
    market_title = serializers.CharField(
        source="outcome.market.title", read_only=True
    )
    market_slug = serializers.CharField(
        source="outcome.market.slug", read_only=True
    )

    class Meta:
        model = Order
        fields = [
            "id",
            "user",
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
        read_only_fields = fields


class AdminPositionSerializer(serializers.ModelSerializer):
    user = AdminUserCompactSerializer(read_only=True)
    average_entry_price = serializers.SerializerMethodField()
    outcome_name = serializers.CharField(source="outcome.name", read_only=True)
    market_id = serializers.IntegerField(source="outcome.market.id", read_only=True)
    market_title = serializers.CharField(
        source="outcome.market.title", read_only=True
    )
    market_slug = serializers.CharField(
        source="outcome.market.slug", read_only=True
    )

    class Meta:
        model = Position
        fields = [
            "id",
            "user",
            "outcome",
            "outcome_name",
            "market_id",
            "market_title",
            "market_slug",
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


class AdminTradeSerializer(serializers.ModelSerializer):
    buyer = AdminUserCompactSerializer(read_only=True)
    seller = AdminUserCompactSerializer(read_only=True)
    price_decimal = serializers.SerializerMethodField()
    outcome_name = serializers.CharField(source="outcome.name", read_only=True)
    market_id = serializers.IntegerField(source="outcome.market.id", read_only=True)
    market_title = serializers.CharField(
        source="outcome.market.title", read_only=True
    )
    market_slug = serializers.CharField(
        source="outcome.market.slug", read_only=True
    )

    class Meta:
        model = Trade
        fields = [
            "id",
            "execution_id",
            "buyer",
            "seller",
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
