from django.contrib import admin

from .models import Wallet, WalletLedgerEntry


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "available_balance", "reserved_balance", "updated_at")
    search_fields = ("user__email", "user__username")


@admin.register(WalletLedgerEntry)
class WalletLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("wallet", "entry_type", "available_delta", "reserved_delta", "created_at")
    list_filter = ("entry_type",)
    search_fields = ("wallet__user__email", "idempotency_key", "reference_id")
    readonly_fields = (
        "wallet",
        "entry_type",
        "available_delta",
        "reserved_delta",
        "reference_type",
        "reference_id",
        "idempotency_key",
        "description",
        "created_at",
    )
