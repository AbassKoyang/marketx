from django.conf import settings
from django.db import models


class Wallet(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="wallet",
    )
    available_balance = models.BigIntegerField(default=0)
    reserved_balance = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet for {self.user.username}"


class WalletLedgerEntry(models.Model):
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )

    entry_type = models.CharField(max_length=30)
    available_delta = models.BigIntegerField(default=0)
    reserved_delta = models.BigIntegerField(default=0)

    reference_type = models.CharField(max_length=30, blank=True)
    reference_id = models.CharField(max_length=100, blank=True)
    idempotency_key = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.entry_type} - {self.wallet_id}"