from django.contrib import admin

from .models import Order, Position, Settlement, Trade


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "outcome", "side", "limit_price", "original_quantity", "filled_quantity", "status")
    list_filter = ("side", "status")
    search_fields = ("user__email", "client_order_id", "outcome__name")


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "outcome",
        "buyer",
        "seller",
        "price",
        "quantity",
        "notional",
        "created_at",
    )
    search_fields = ("buyer__email", "seller__email", "execution_id")


@admin.register(Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("user", "outcome", "quantity", "reserved_quantity", "cost_basis", "realized_pnl")
    search_fields = ("user__email", "outcome__name")


admin.site.register(Settlement)
