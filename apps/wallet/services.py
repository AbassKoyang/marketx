
from django.db import transaction
from django.core.exceptions import ValidationError

from .models import Wallet, WalletLedgerEntry


class WalletServiceError(Exception):
    """Base exception for wallet service operations."""


class InsufficientAvailableCredits(WalletServiceError):
    """Raised when a wallet cannot reserve the requested credits."""


class InsufficientReservedCredits(WalletServiceError):
    """Raised when a wallet does not have enough reserved credits."""


class IdempotencyConflict(WalletServiceError):
    """Raised when an idempotency key is reused for a different operation."""


def _validate_amount(amount):
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise ValidationError("Amount must be a positive integer.")


def _get_existing_entry(wallet, *, entry_type, available_delta,
                        reserved_delta, idempotency_key):
    """
    Return an existing matching ledger entry, or None.

    Reusing a key for a different operation is an error.
    """
    if not idempotency_key:
        raise ValidationError("An idempotency key is required.")

    entry = WalletLedgerEntry.objects.filter(
        idempotency_key=idempotency_key
    ).first()

    if entry is None:
        return None

    matches = (
        entry.wallet_id == wallet.pk
        and entry.entry_type == entry_type
        and entry.available_delta == available_delta
        and entry.reserved_delta == reserved_delta
    )

    if not matches:
        raise IdempotencyConflict(
            "This idempotency key has already been used "
            "for a different wallet operation."
        )

    return entry


def _record_entry(
    *,
    wallet,
    entry_type,
    available_delta,
    reserved_delta,
    idempotency_key,
    reference_type="",
    reference_id="",
    description="",
):
    """Create an immutable ledger record for a wallet operation."""
    return WalletLedgerEntry.objects.create(
        wallet=wallet,
        entry_type=entry_type,
        available_delta=available_delta,
        reserved_delta=reserved_delta,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=str(reference_id) if reference_id else "",
        description=description,
    )


def _mutate_wallet(
    *,
    user_id,
    entry_type,
    available_delta,
    reserved_delta,
    idempotency_key,
    reference_type="",
    reference_id="",
    description="",
):
    if not idempotency_key:
        raise ValidationError("An idempotency key is required.")

    wallet = Wallet.objects.select_for_update().get(user_id=user_id)

    existing = _get_existing_entry(
        wallet,
        entry_type=entry_type,
        available_delta=available_delta,
        reserved_delta=reserved_delta,
        idempotency_key=idempotency_key,
    )
    if existing:
        return wallet

    next_available = wallet.available_balance + available_delta
    next_reserved = wallet.reserved_balance + reserved_delta

    if next_available < 0:
        raise InsufficientAvailableCredits("Not enough available credits.")
    if next_reserved < 0:
        raise InsufficientReservedCredits("Not enough reserved credits.")

    wallet.available_balance = next_available
    wallet.reserved_balance = next_reserved
    wallet.save(update_fields=["available_balance", "reserved_balance", "updated_at"])

    _record_entry(
        wallet=wallet,
        entry_type=entry_type,
        available_delta=available_delta,
        reserved_delta=reserved_delta,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
    )
    return wallet


@transaction.atomic
def get_or_create_wallet(user, initial_credits=1_000_000):
    """
    Get a user's wallet, creating it and allocating starter credits
    only if the initial-credit operation has not already occurred.

    Change initial_credits to match your chosen MarketX policy.
    """
    if (
        isinstance(initial_credits, bool)
        or not isinstance(initial_credits, int)
        or initial_credits < 0
    ):
        raise ValidationError(
            "Initial credits must be a non-negative integer."
        )

    wallet, _ = Wallet.objects.get_or_create(user=user)

    # Lock the wallet before checking or changing its balance.
    wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)

    key = f"initial-credit:{user.pk}"

    existing = _get_existing_entry(
        wallet,
        entry_type="initial_credit",
        available_delta=initial_credits,
        reserved_delta=0,
        idempotency_key=key,
    )

    if existing:
        return wallet

    wallet.available_balance += initial_credits
    wallet.save(
        update_fields=["available_balance", "updated_at"]
    )

    _record_entry(
        wallet=wallet,
        entry_type="initial_credit",
        available_delta=initial_credits,
        reserved_delta=0,
        idempotency_key=key,
        description="Initial credit allocation",
    )

    return wallet


@transaction.atomic
def reserve_credits(
    *,
    user_id,
    amount,
    idempotency_key,
    reference_type="order",
    reference_id="",
):
    """
    Move credits from available to reserved.

    Use for BUY orders that need credits to be held.
    This does not apply to reserving YES/NO contracts for SELL orders.
    """
    _validate_amount(amount)

    if not idempotency_key:
        raise ValidationError("An idempotency key is required.")

    wallet = Wallet.objects.select_for_update().get(
        user_id=user_id
    )

    existing = _get_existing_entry(
        wallet,
        entry_type="order_reserve",
        available_delta=-amount,
        reserved_delta=amount,
        idempotency_key=idempotency_key,
    )

    if existing:
        return wallet

    if wallet.available_balance < amount:
        raise InsufficientAvailableCredits(
            "Not enough available credits."
        )

    wallet.available_balance -= amount
    wallet.reserved_balance += amount
    wallet.save(
        update_fields=[
            "available_balance",
            "reserved_balance",
            "updated_at",
        ]
    )

    _record_entry(
        wallet=wallet,
        entry_type="order_reserve",
        available_delta=-amount,
        reserved_delta=amount,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description="Credits reserved for an order",
    )

    return wallet


@transaction.atomic
def release_reserved_credits(
    *,
    user_id,
    amount,
    idempotency_key,
    reference_type="order",
    reference_id="",
):
    """
    Move credits from reserved back to available.

    Use when an order is cancelled or its unfilled quantity is released.
    """
    _validate_amount(amount)

    if not idempotency_key:
        raise ValidationError("An idempotency key is required.")

    wallet = Wallet.objects.select_for_update().get(
        user_id=user_id
    )

    existing = _get_existing_entry(
        wallet,
        entry_type="order_release",
        available_delta=amount,
        reserved_delta=-amount,
        idempotency_key=idempotency_key,
    )

    if existing:
        return wallet

    if wallet.reserved_balance < amount:
        raise InsufficientReservedCredits(
            "Not enough reserved credits to release."
        )

    wallet.reserved_balance -= amount
    wallet.available_balance += amount
    wallet.save(
        update_fields=[
            "available_balance",
            "reserved_balance",
            "updated_at",
        ]
    )

    _record_entry(
        wallet=wallet,
        entry_type="order_release",
        available_delta=amount,
        reserved_delta=-amount,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description="Reserved credits released",
    )

    return wallet


@transaction.atomic
def consume_reserved_credits(
    *,
    user_id,
    amount,
    idempotency_key,
    reference_type="trade",
    reference_id="",
):
    """Remove reserved credits after a BUY order execution spends them."""
    _validate_amount(amount)
    return _mutate_wallet(
        user_id=user_id,
        entry_type="reserved_spend",
        available_delta=0,
        reserved_delta=-amount,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description="Reserved credits spent on a trade",
    )


@transaction.atomic
def debit_available_credits(
    *,
    user_id,
    amount,
    idempotency_key,
    reference_type="trade",
    reference_id="",
    description="Credits spent",
):
    """Remove available credits immediately, for trades that settle at once."""
    _validate_amount(amount)
    return _mutate_wallet(
        user_id=user_id,
        entry_type="available_debit",
        available_delta=-amount,
        reserved_delta=0,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
    )


@transaction.atomic
def credit_available_credits(
    *,
    user_id,
    amount,
    idempotency_key,
    reference_type="trade",
    reference_id="",
    description="Credits added",
):
    """Credit available credits for proceeds, payouts, or adjustments."""
    _validate_amount(amount)
    return _mutate_wallet(
        user_id=user_id,
        entry_type="available_credit",
        available_delta=amount,
        reserved_delta=0,
        idempotency_key=idempotency_key,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
    )
