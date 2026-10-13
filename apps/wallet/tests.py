from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import WalletLedgerEntry
from .services import (
    IdempotencyConflict,
    InsufficientAvailableCredits,
    consume_reserved_credits,
    credit_available_credits,
    get_or_create_wallet,
    release_reserved_credits,
    reserve_credits,
)


class WalletServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            email="wallet@example.com",
            password="password",
            first_name="Wallet",
            last_name="User",
            username="wallet-user",
        )

    def test_initial_credit_allocation_is_idempotent(self):
        wallet = get_or_create_wallet(self.user, initial_credits=10_000)
        again = get_or_create_wallet(self.user, initial_credits=10_000)

        self.assertEqual(wallet.pk, again.pk)
        again.refresh_from_db()
        self.assertEqual(again.available_balance, 10_000)
        self.assertEqual(WalletLedgerEntry.objects.filter(entry_type="initial_credit").count(), 1)

    def test_reserve_release_and_consume_reserved_credits(self):
        get_or_create_wallet(self.user, initial_credits=10_000)

        reserve_credits(user_id=self.user.id, amount=6_000, idempotency_key="reserve:1")
        wallet = self.user.wallet
        wallet.refresh_from_db()
        self.assertEqual(wallet.available_balance, 4_000)
        self.assertEqual(wallet.reserved_balance, 6_000)

        consume_reserved_credits(user_id=self.user.id, amount=4_000, idempotency_key="spend:1")
        release_reserved_credits(user_id=self.user.id, amount=2_000, idempotency_key="release:1")
        wallet.refresh_from_db()
        self.assertEqual(wallet.available_balance, 6_000)
        self.assertEqual(wallet.reserved_balance, 0)

    def test_insufficient_available_credits_raises(self):
        get_or_create_wallet(self.user, initial_credits=100)
        with self.assertRaises(InsufficientAvailableCredits):
            reserve_credits(user_id=self.user.id, amount=101, idempotency_key="reserve:too-much")

    def test_idempotency_key_conflict_raises(self):
        get_or_create_wallet(self.user, initial_credits=1_000)
        reserve_credits(user_id=self.user.id, amount=100, idempotency_key="same-key")
        with self.assertRaises(IdempotencyConflict):
            release_reserved_credits(user_id=self.user.id, amount=100, idempotency_key="same-key")

    def test_positive_amount_validation(self):
        get_or_create_wallet(self.user, initial_credits=1_000)
        with self.assertRaises(ValidationError):
            credit_available_credits(user_id=self.user.id, amount=0, idempotency_key="zero")
