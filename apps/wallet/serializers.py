from rest_framework import serializers

from .models import Wallet, WalletLedgerEntry


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ["available_balance", "reserved_balance", "created_at", "updated_at"]
        read_only_fields = fields


class WalletLedgerEntrySerializer(serializers.ModelSerializer):
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
