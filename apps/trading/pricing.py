"""LMSR (Logarithmic Market Scoring Rule) pricing helpers.

The market maker keeps one net-share counter per outcome.  ``net_shares[i]``
is the number of shares of outcome ``i`` that the automated market maker has
sold to users, net of shares it has bought back.  Prices are the softmax of
those counters::

    p_i = exp(q_i / b) / sum_j exp(q_j / b)

and therefore always add up to one.  ``b`` is the market's liquidity
parameter: larger values make prices move more slowly.

All wallet amounts are integer credit units (``CREDIT_SCALE`` units per
virtual credit).  Buy costs are rounded up and sell refunds are rounded down
so integer rounding can never give a trader free credits.
"""

import math

from .constants import CREDIT_SCALE, MAX_PRICE, MIN_PRICE


def _validate_b(b):
    if isinstance(b, bool) or not isinstance(b, int) or b <= 0:
        raise ValueError("The liquidity parameter b must be a positive integer.")


def _logsumexp(values):
    if not values:
        raise ValueError("At least one value is required.")
    maximum = max(values)
    return maximum + math.log(math.fsum(math.exp(value - maximum) for value in values))


def lmsr_prices(net_shares, b):
    """
    Return the current price vector as integer ticks that sum to CREDIT_SCALE.

    Each tick is clamped to the tradable range ``MIN_PRICE..MAX_PRICE``.  With
    a single outcome the market is certain and the tick is CREDIT_SCALE.
    """
    _validate_b(b)
    shares = [int(value) for value in net_shares]
    if not shares:
        return []
    if len(shares) == 1:
        return [CREDIT_SCALE]

    scaled = [value / b for value in shares]
    maximum = max(scaled)
    weights = [math.exp(value - maximum) for value in scaled]
    total = math.fsum(weights)
    raw = [weight / total * CREDIT_SCALE for weight in weights]

    ticks = [int(math.floor(value)) for value in raw]
    remainder = CREDIT_SCALE - sum(ticks)
    if remainder:
        order = sorted(
            range(len(raw)),
            key=lambda index: raw[index] - ticks[index],
            reverse=True,
        )
        for index in order[:remainder]:
            ticks[index] += 1

    ticks = [min(MAX_PRICE, max(MIN_PRICE, value)) for value in ticks]

    # Clamping can break the sum.  Move the difference onto the largest
    # outcome(s) so the vector still adds up to exactly CREDIT_SCALE.
    difference = CREDIT_SCALE - sum(ticks)
    if difference > 0:
        index = max(range(len(raw)), key=lambda item: raw[item])
        ticks[index] += difference
    elif difference < 0:
        remaining = -difference
        for index in sorted(range(len(ticks)), key=lambda item: ticks[item], reverse=True):
            take = min(remaining, ticks[index] - MIN_PRICE)
            ticks[index] -= take
            remaining -= take
            if remaining == 0:
                break

    return ticks


def _cost_delta(net_shares, outcome_index, delta, b):
    """
    Exact LMSR cost change in credit units for changing one counter by delta.

    Positive ``delta`` is a buy, negative is a sell.  The log ratio is
    evaluated in a numerically stable form so very large or very small
    counters do not overflow ``exp``.
    """
    _validate_b(b)
    shares = [int(value) for value in net_shares]
    if delta == 0:
        return 0
    if outcome_index < 0 or outcome_index >= len(shares):
        raise IndexError("Outcome index is out of range.")

    others = [value / b for index, value in enumerate(shares) if index != outcome_index]
    current = shares[outcome_index] / b
    updated = (shares[outcome_index] + delta) / b
    maximum = max([current, updated] + others)
    other_sum = math.fsum(math.exp(value - maximum) for value in others)
    current_weight = math.exp(current - maximum)
    updated_weight = math.exp(updated - maximum)
    ratio = (other_sum + updated_weight) / (other_sum + current_weight)
    return CREDIT_SCALE * b * math.log(ratio)


def buy_cost(net_shares, outcome_index, quantity, b):
    """Cost in credit units to buy ``quantity`` shares, rounded up."""
    if quantity <= 0:
        return 0
    return max(1, math.ceil(_cost_delta(net_shares, outcome_index, quantity, b)))


def sell_refund(net_shares, outcome_index, quantity, b):
    """Refund in credit units for selling ``quantity`` shares, rounded down."""
    if quantity <= 0:
        return 0
    # _cost_delta returns C(q - quantity) - C(q), which is negative for a
    # buyback.  The refund is the positive difference C(q) - C(q - quantity).
    return max(0, math.floor(-_cost_delta(net_shares, outcome_index, -quantity, b)))


def _largest_satisfying(predicate, upper_bound):
    if upper_bound <= 0 or not predicate(1):
        return 0
    low, high = 1, int(upper_bound)
    while low < high:
        middle = (low + high + 1) // 2
        if predicate(middle):
            low = middle
        else:
            high = middle - 1
    return low


def max_buy_quantity(
    net_shares,
    outcome_index,
    b,
    *,
    limit_price,
    max_quantity,
    max_cost=None,
):
    """
    Largest quantity that respects the slippage cap and available credits.

    ``limit_price`` is the maximum average price per share the buyer accepts.
    ``max_quantity`` is the hard cap from market inventory and issuance.
    ``max_cost`` caps the total credits the buyer can spend.
    """
    if max_quantity <= 0:
        return 0

    def acceptable(quantity):
        cost = buy_cost(net_shares, outcome_index, quantity, b)
        if cost > limit_price * quantity:
            return False
        if max_cost is not None and cost > max_cost:
            return False
        return True

    return _largest_satisfying(acceptable, max_quantity)


def max_sell_quantity(
    net_shares,
    outcome_index,
    b,
    *,
    limit_price,
    max_quantity,
    max_refund=None,
):
    """
    Largest quantity the market maker will buy back.

    ``limit_price`` is the minimum average price per share the seller accepts.
    ``max_quantity`` is capped by the seller's position and by the shares the
    market maker actually sold for that outcome.
    """
    if max_quantity <= 0:
        return 0

    def acceptable(quantity):
        refund = sell_refund(net_shares, outcome_index, quantity, b)
        if refund < limit_price * quantity:
            return False
        if max_refund is not None and refund > max_refund:
            return False
        return True

    return _largest_satisfying(acceptable, max_quantity)


def average_tick(notional, quantity, *, side):
    """Average execution price in ticks, rounded in the trader's favour."""
    if quantity <= 0:
        return None
    if side == "buy":
        tick = notional // quantity
    else:
        tick = (notional + quantity - 1) // quantity
    return min(MAX_PRICE, max(MIN_PRICE, tick))
