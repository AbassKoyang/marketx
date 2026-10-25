"""Platform liquidity account used for primary share issuance."""

from django.conf import settings
from django.db import transaction

from apps.account.models import User
from apps.wallet.models import Wallet
from apps.wallet.services import get_or_create_wallet


@transaction.atomic
def get_house_user():
    """
    Return the system liquidity account, creating it on first use.

    The account is inactive (it can never log in) and holds the collateral
    that backs shares minted by the matching engine.
    """
    user, created = User.objects.get_or_create(
        email=settings.MARKETX_HOUSE_EMAIL,
        defaults={
            "username": settings.MARKETX_HOUSE_USERNAME,
            "first_name": "MarketX",
            "last_name": "Liquidity",
            "is_active": False,
        },
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    # Only allocate starting collateral once. Re-running this after the
    # configured amount changes must not raise an idempotency conflict.
    if not Wallet.objects.filter(user=user).exists():
        get_or_create_wallet(user, settings.MARKETX_HOUSE_CREDITS)
    return user
