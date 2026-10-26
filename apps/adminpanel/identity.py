"""Identify the platform liquidity (market-maker) accounts for admin views."""

from django.conf import settings
from django.db.models import Q

# The configured house identities plus aliases created by earlier defaults.
# Local and long-lived databases can contain more than one liquidity account
# after the house email was renamed, and none of them are customers.
HOUSE_EMAILS = {
    settings.MARKETX_HOUSE_EMAIL,
    "liquidity@marketx.local",
}
HOUSE_USERNAMES = {
    settings.MARKETX_HOUSE_USERNAME,
    "marketx-liquidity",
}


def house_filter(prefix=""):
    """Q object matching every house account, optionally through a relation."""
    return Q(**{f"{prefix}email__in": HOUSE_EMAILS}) | Q(
        **{f"{prefix}username__in": HOUSE_USERNAMES}
    )


def is_house_account(user):
    """True for a house account instance, or for a bare email string."""
    if user is None:
        return False
    if isinstance(user, str):
        return user in HOUSE_EMAILS
    return user.email in HOUSE_EMAILS or user.username in HOUSE_USERNAMES
