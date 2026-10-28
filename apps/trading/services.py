import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.markets.models import Market, MarketOutcome, MarketPricePoint, MarketResolution
from apps.notifications.models import Notification
from apps.notifications.services import notify_user, notify_users
from apps.wallet.models import Wallet
from apps.wallet.services import (
    consume_reserved_credits,
    credit_available_credits,
    debit_available_credits,
    release_reserved_credits,
    reserve_credits,
)
from .constants import CREDIT_SCALE, MAX_PRICE, MIN_PRICE
from .house import get_house_user
from .models import ActivityEvent, Order, Position, Settlement, Trade
from .pricing import (
    average_tick,
    buy_cost,
    lmsr_prices,
    max_buy_quantity,
    max_sell_quantity,
    sell_refund,
)


class TradingError(ValidationError):
    """Base exception for trading operations."""


class MarketNotOpen(TradingError):
    pass


class InsufficientPosition(TradingError):
    pass


class InsufficientLiquidity(TradingError):
    pass


def _validate_order_input(*, price, quantity, client_order_id):
    if not client_order_id:
        raise ValidationError("A client_order_id is required.")
    if isinstance(price, bool) or not isinstance(price, int):
        raise ValidationError("Price must be an integer tick value.")
    if price < MIN_PRICE or price > MAX_PRICE:
        raise ValidationError(f"Price must be between {MIN_PRICE} and {MAX_PRICE}.")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValidationError("Quantity must be a positive integer.")


def _open_status_filter():
    return (
        Q(status=Order.Status.OPEN) | Q(status=Order.Status.PARTIALLY_FILLED)
    ) & Q(cancelled_at__isnull=True)


def _active_outcomes(market):
    return list(
        MarketOutcome.objects.filter(market=market, is_active=True).order_by(
            "display_order", "id"
        )
    )


def _outcome_index(outcomes, outcome_id):
    for index, outcome in enumerate(outcomes):
        if outcome.pk == outcome_id:
            return index
    raise ValidationError("Outcome is not active in this market.")


def _net_shares(outcomes):
    return [outcome.net_shares for outcome in outcomes]


def _readonly_house():
    house = (
        get_user_model()
        .objects.filter(email=settings.MARKETX_HOUSE_EMAIL)
        .first()
    )
    wallet = Wallet.objects.filter(user=house).first() if house else None
    # Before the first trade the liquidity account has not been created yet.
    # Quotes and depth previews should still reflect the configured funding.
    available = (
        wallet.available_balance
        if wallet is not None
        else settings.MARKETX_HOUSE_CREDITS
    )
    return house, wallet, available


def _house_inventory(house, outcomes):
    positions = {
        position.outcome_id: position
        for position in Position.objects.filter(user=house, outcome__in=outcomes)
    }
    return positions


def _inventory_available(position):
    if position is None:
        return 0
    return max(0, position.quantity - position.reserved_quantity)


def _max_amm_buy_quantity(*, market, house_available_credits, inventory_available):
    remaining_sets = max(0, market.issuance_limit - market.issued_quantity)
    affordable_sets = house_available_credits // CREDIT_SCALE
    return inventory_available + min(remaining_sets, affordable_sets)


@transaction.atomic
def submit_limit_order(*, user, outcome_id, side, limit_price, quantity, client_order_id):
    """
    Execute an order immediately against the LMSR market maker.

    ``limit_price`` is a slippage cap: the maximum average price for a BUY and
    the minimum average price for a SELL.  The largest quantity that respects
    the cap is filled.  Any unfilled remainder is cancelled immediately; the
    order never rests in an order book.
    """
    _validate_order_input(
        price=limit_price,
        quantity=quantity,
        client_order_id=client_order_id,
    )
    if side not in Order.Side.values:
        raise ValidationError("Side must be buy or sell.")

    existing = Order.objects.filter(user=user, client_order_id=client_order_id).first()
    if existing:
        expected = (
            existing.outcome_id == outcome_id
            and existing.side == side
            and existing.limit_price == limit_price
            and existing.original_quantity == quantity
        )
        if not expected:
            raise ValidationError("client_order_id was already used for a different order.")
        return existing

    outcome = MarketOutcome.objects.select_related("market").get(pk=outcome_id)
    market = Market.objects.select_for_update().get(pk=outcome.market_id)
    now = timezone.now()
    if market.status != Market.Status.OPEN:
        raise MarketNotOpen("Orders can only be placed on open markets.")
    if market.opens_at > now or market.closes_at <= now:
        raise MarketNotOpen("Market is not inside its trading window.")

    outcomes = _active_outcomes(market)
    if len(outcomes) < 2:
        raise TradingError("A market needs at least two active outcomes to trade.")
    index = _outcome_index(outcomes, outcome_id)
    outcome = outcomes[index]
    if not outcome.is_active:
        raise ValidationError("Outcome is not active.")

    net_shares = _net_shares(outcomes)
    house = get_house_user()
    house_wallet = Wallet.objects.select_for_update().get(user=house)

    if side == Order.Side.BUY:
        user_wallet = Wallet.objects.select_for_update().get(user=user)
        house_positions = _house_inventory(house, outcomes)
        inventory_available = _inventory_available(house_positions.get(outcome.id))
        max_quantity = min(
            quantity,
            _max_amm_buy_quantity(
                market=market,
                house_available_credits=house_wallet.available_balance,
                inventory_available=inventory_available,
            ),
        )
        fill_quantity = max_buy_quantity(
            net_shares,
            index,
            market.liquidity_b,
            limit_price=limit_price,
            max_quantity=max_quantity,
            max_cost=user_wallet.available_balance,
        )
        if fill_quantity <= 0:
            raise TradingError("No quantity can fill at this limit.")
        notional = buy_cost(net_shares, index, fill_quantity, market.liquidity_b)
    else:
        position = (
            Position.objects.select_for_update()
            .filter(user=user, outcome=outcome)
            .first()
        )
        available_shares = _inventory_available(position)
        max_quantity = min(quantity, available_shares, net_shares[index])
        fill_quantity = max_sell_quantity(
            net_shares,
            index,
            market.liquidity_b,
            limit_price=limit_price,
            max_quantity=max_quantity,
            max_refund=house_wallet.available_balance,
        )
        if fill_quantity <= 0:
            raise TradingError("No quantity can fill at this limit.")
        notional = sell_refund(net_shares, index, fill_quantity, market.liquidity_b)

    order = Order.objects.create(
        user=user,
        outcome=outcome,
        side=side,
        limit_price=limit_price,
        original_quantity=quantity,
        client_order_id=client_order_id,
    )

    if side == Order.Side.BUY:
        _execute_buy(
            order=order,
            market=market,
            outcomes=outcomes,
            outcome=outcome,
            index=index,
            fill_quantity=fill_quantity,
            notional=notional,
            house=house,
            house_wallet=house_wallet,
        )
    else:
        _execute_sell(
            order=order,
            market=market,
            outcomes=outcomes,
            outcome=outcome,
            index=index,
            fill_quantity=fill_quantity,
            notional=notional,
            house=house,
            house_wallet=house_wallet,
        )

    _finalize_order(order, fill_quantity=fill_quantity, requested_quantity=quantity)
    return Order.objects.get(pk=order.pk)


def _finalize_order(order, *, fill_quantity, requested_quantity):
    order.filled_quantity = fill_quantity
    if fill_quantity >= requested_quantity:
        order.status = Order.Status.FILLED
        order.cancelled_at = None
    else:
        # The order was marketable for only part of its size.  The rest is
        # cancelled immediately rather than left resting.
        order.status = Order.Status.PARTIALLY_FILLED
        order.cancelled_at = timezone.now()
    order.save(
        update_fields=["filled_quantity", "status", "cancelled_at", "updated_at"]
    )


def _execute_buy(
    *,
    order,
    market,
    outcomes,
    outcome,
    index,
    fill_quantity,
    notional,
    house,
    house_wallet,
):
    house_positions = {
        position.outcome_id: position
        for position in Position.objects.select_for_update().filter(
            user=house, outcome__in=outcomes
        )
    }
    inventory_available = _inventory_available(house_positions.get(outcome.id))
    mint_quantity = max(0, fill_quantity - inventory_available)

    remaining_sets = max(0, market.issuance_limit - market.issued_quantity)
    affordable_sets = house_wallet.available_balance // CREDIT_SCALE
    if mint_quantity > remaining_sets or mint_quantity > affordable_sets:
        raise InsufficientLiquidity("The market maker cannot mint enough shares.")
    if fill_quantity <= 0:
        raise InsufficientLiquidity("The market maker has no shares available.")

    if mint_quantity:
        reserve_credits(
            user_id=house.id,
            amount=CREDIT_SCALE * mint_quantity,
            idempotency_key=f"issuance-collateral:{market.id}:{uuid.uuid4().hex}",
            reference_type="market",
            reference_id=market.id,
        )
        sold_basis = notional * mint_quantity // fill_quantity
        _mint_house_positions(
            house=house,
            house_positions=house_positions,
            outcomes=outcomes,
            sold_outcome=outcome,
            mint_quantity=mint_quantity,
            sold_basis=sold_basis,
        )
        market.issued_quantity += mint_quantity
        market.save(update_fields=["issued_quantity", "updated_at"])

    price = average_tick(notional, fill_quantity, side="buy")
    house_order = Order.objects.create(
        user=house,
        outcome=outcome,
        side=Order.Side.SELL,
        limit_price=price,
        original_quantity=fill_quantity,
        filled_quantity=fill_quantity,
        status=Order.Status.FILLED,
        client_order_id=f"amm:{uuid.uuid4().hex}",
    )
    trade = Trade.objects.create(
        outcome=outcome,
        buy_order=order,
        sell_order=house_order,
        buyer_id=order.user_id,
        seller=house,
        price=price,
        quantity=fill_quantity,
        notional=notional,
    )

    debit_available_credits(
        user_id=order.user_id,
        amount=notional,
        idempotency_key=f"trade-spend:{trade.id}:buyer",
        reference_type="trade",
        reference_id=trade.id,
        description="Credits spent buying outcome shares",
    )
    credit_available_credits(
        user_id=house.id,
        amount=notional,
        idempotency_key=f"trade-proceeds:{trade.id}:seller",
        reference_type="trade",
        reference_id=trade.id,
        description="Market maker share sale proceeds",
    )

    buyer_position, _ = Position.objects.select_for_update().get_or_create(
        user=order.user,
        outcome=outcome,
    )
    buyer_position.quantity += fill_quantity
    buyer_position.cost_basis += notional
    buyer_position.save(update_fields=["quantity", "cost_basis", "updated_at"])

    sold_position = house_positions.get(outcome.id)
    if sold_position is None or sold_position.quantity < fill_quantity:
        raise InsufficientLiquidity("The market maker inventory is inconsistent.")
    released_basis = sold_position.cost_basis * fill_quantity // sold_position.quantity
    sold_position.quantity -= fill_quantity
    sold_position.cost_basis -= released_basis
    sold_position.realized_pnl += notional - released_basis
    sold_position.save(
        update_fields=["quantity", "cost_basis", "realized_pnl", "updated_at"]
    )

    outcome.net_shares += fill_quantity
    outcome.save(update_fields=["net_shares", "updated_at"])

    _record_price_vector(market=market, outcomes=outcomes, trade=trade)
    _record_trade_events(
        trade=trade,
        market=market,
        outcomes=outcomes,
        user_order=order,
        user_id=order.user_id,
        user_is_buyer=True,
    )


def _execute_sell(
    *,
    order,
    market,
    outcomes,
    outcome,
    index,
    fill_quantity,
    notional,
    house,
    house_wallet,
):
    seller_position = Position.objects.select_for_update().get(
        user=order.user,
        outcome=outcome,
    )
    if seller_position.quantity - seller_position.reserved_quantity < fill_quantity:
        raise InsufficientPosition("Not enough unreserved contracts to sell.")
    if outcome.net_shares < fill_quantity:
        raise InsufficientLiquidity("The market maker cannot buy back that many shares.")

    house_position, _ = Position.objects.select_for_update().get_or_create(
        user=house,
        outcome=outcome,
    )

    price = average_tick(notional, fill_quantity, side="sell")
    house_order = Order.objects.create(
        user=house,
        outcome=outcome,
        side=Order.Side.BUY,
        limit_price=price,
        original_quantity=fill_quantity,
        filled_quantity=fill_quantity,
        status=Order.Status.FILLED,
        client_order_id=f"amm:{uuid.uuid4().hex}",
    )
    trade = Trade.objects.create(
        outcome=outcome,
        buy_order=house_order,
        sell_order=order,
        buyer=house,
        seller_id=order.user_id,
        price=price,
        quantity=fill_quantity,
        notional=notional,
    )

    debit_available_credits(
        user_id=house.id,
        amount=notional,
        idempotency_key=f"trade-spend:{trade.id}:buyer",
        reference_type="trade",
        reference_id=trade.id,
        description="Market maker share buyback",
    )
    credit_available_credits(
        user_id=order.user_id,
        amount=notional,
        idempotency_key=f"trade-proceeds:{trade.id}:seller",
        reference_type="trade",
        reference_id=trade.id,
        description="Proceeds from selling outcome shares",
    )

    released_basis = (
        seller_position.cost_basis * fill_quantity // seller_position.quantity
    )
    seller_position.quantity -= fill_quantity
    seller_position.cost_basis -= released_basis
    seller_position.realized_pnl += notional - released_basis
    seller_position.save(
        update_fields=["quantity", "cost_basis", "realized_pnl", "updated_at"]
    )

    house_position.quantity += fill_quantity
    house_position.cost_basis += notional
    house_position.save(update_fields=["quantity", "cost_basis", "updated_at"])

    outcome.net_shares -= fill_quantity
    outcome.save(update_fields=["net_shares", "updated_at"])

    _record_price_vector(market=market, outcomes=outcomes, trade=trade)
    _record_trade_events(
        trade=trade,
        market=market,
        outcomes=outcomes,
        user_order=order,
        user_id=order.user_id,
        user_is_buyer=False,
    )


def _mint_house_positions(
    *,
    house,
    house_positions,
    outcomes,
    sold_outcome,
    mint_quantity,
    sold_basis,
):
    """Increase the house inventory by one share of every outcome per set."""
    complement_basis = CREDIT_SCALE * mint_quantity - sold_basis
    complements = [item for item in outcomes if item.pk != sold_outcome.pk]
    if not complements:
        raise ValidationError("A complete set needs at least two outcomes.")
    share, remainder = divmod(complement_basis, len(complements))

    def position_for(item):
        position = house_positions.get(item.id)
        if position is None:
            position = Position.objects.create(user=house, outcome=item)
            house_positions[item.id] = position
        return position

    for index, item in enumerate(complements):
        position = position_for(item)
        position.quantity += mint_quantity
        position.cost_basis += share + (1 if index < remainder else 0)
        position.save(update_fields=["quantity", "cost_basis", "updated_at"])

    sold_position = position_for(sold_outcome)
    sold_position.quantity += mint_quantity
    sold_position.cost_basis += sold_basis
    sold_position.save(update_fields=["quantity", "cost_basis", "updated_at"])


def _record_price_vector(*, market, outcomes, trade):
    """Persist the full post-trade price vector, not just the traded outcome."""
    ticks = lmsr_prices(_net_shares(outcomes), market.liquidity_b)
    bucket = trade.created_at.replace(second=0, microsecond=0)
    resolution_seconds = max(
        0,
        int((market.resolution_deadline - trade.created_at).total_seconds()),
    )
    for outcome, tick in zip(outcomes, ticks):
        defaults = {
            "price": tick,
            "resolution_seconds": resolution_seconds,
            "source_trade_id": trade.id,
        }
        if outcome.pk == trade.outcome_id:
            defaults["volume"] = (
                Trade.objects.filter(
                    outcome=outcome,
                    created_at__gte=bucket,
                    created_at__lt=bucket + timedelta(minutes=1),
                ).aggregate(total=Sum("quantity"))["total"]
                or 0
            )
        MarketPricePoint.objects.update_or_create(
            outcome=outcome,
            timestamp=bucket,
            defaults=defaults,
        )


def _format_credits(amount):
    return f"{amount / CREDIT_SCALE:,.2f}"


def _format_price(tick):
    return f"{tick / CREDIT_SCALE:.2f}"


def _notify_order_filled(*, user, trade, market, outcome, user_order):
    is_partial = trade.quantity < user_order.original_quantity
    side_label = "Buy" if user_order.side == Order.Side.BUY else "Sell"
    title = "Order partially filled" if is_partial else "Order filled"
    body = (
        f"{side_label} {trade.quantity:,} {outcome.name} shares at "
        f"{_format_price(trade.price)} for {_format_credits(trade.notional)} credits."
    )
    if is_partial:
        cancelled = user_order.original_quantity - trade.quantity
        body = (
            f"{side_label} order partially filled: {trade.quantity:,} of "
            f"{user_order.original_quantity:,} {outcome.name} shares at "
            f"{_format_price(trade.price)} for {_format_credits(trade.notional)} "
            f"credits. {cancelled:,} unfilled shares were cancelled."
        )
    notify_user(
        user=user,
        event_type=Notification.EventType.ORDER_FILLED,
        title=title,
        body=body,
        data={
            "trade_id": trade.id,
            "order_id": user_order.id,
            "side": user_order.side,
            "quantity": trade.quantity,
            "original_quantity": user_order.original_quantity,
            "partial": is_partial,
            "price": trade.price,
            "price_decimal": trade.price / CREDIT_SCALE,
            "notional": trade.notional,
            "notional_decimal": trade.notional / CREDIT_SCALE,
            "market_id": market.id,
            "market_slug": market.slug,
            "market_title": market.title,
            "outcome_id": outcome.id,
            "outcome_name": outcome.name,
        },
    )


def _notify_order_cancelled(*, order, remaining):
    side_label = "buy" if order.side == Order.Side.BUY else "sell"
    body = (
        f"Your {side_label} order for {order.original_quantity:,} "
        f"{order.outcome.name} shares was cancelled."
    )
    if remaining:
        if order.side == Order.Side.BUY:
            body += (
                f" {_format_credits(order.limit_price * remaining)} reserved "
                "credits were released."
            )
        else:
            body += f" {remaining:,} shares were released."
    notify_user(
        user=order.user,
        event_type=Notification.EventType.ORDER_CANCELLED,
        title="Order cancelled",
        body=body,
        data={
            "order_id": order.id,
            "side": order.side,
            "original_quantity": order.original_quantity,
            "filled_quantity": order.filled_quantity,
            "released_quantity": remaining,
            "market_id": order.outcome.market_id,
            "outcome_id": order.outcome_id,
            "outcome_name": order.outcome.name,
        },
    )


def _notify_market_closed(*, market, extra_user_ids=()):
    user_ids = set(extra_user_ids)
    user_ids.update(
        Position.objects.filter(outcome__market=market, quantity__gt=0)
        .exclude(user__email=settings.MARKETX_HOUSE_EMAIL)
        .values_list("user_id", flat=True)
    )
    house_user_id = (
        get_user_model()
        .objects.filter(email=settings.MARKETX_HOUSE_EMAIL)
        .values_list("id", flat=True)
        .first()
    )
    user_ids.discard(house_user_id)
    if not user_ids:
        return
    notify_users(
        user_ids=list(user_ids),
        event_type=Notification.EventType.MARKET_CLOSED,
        title=f"Market closed: {market.title}"[:180],
        body=(
            "Trading has ended. Your positions will be settled when the "
            "market is resolved."
        ),
        data={
            "market_id": market.id,
            "market_slug": market.slug,
            "market_title": market.title,
        },
    )


def _notify_position_settled(*, position, market, resolution, payout):
    outcome = position.outcome
    won = not resolution.is_void and position.outcome_id == resolution.winning_outcome_id
    if resolution.is_void:
        title = "Market voided"
        body = (
            f"{market.title} was voided. Your {outcome.name} position was "
            f"refunded {_format_credits(payout)} credits."
        )
    elif won:
        title = "Winning position settled"
        body = (
            f"Your {outcome.name} position in {market.title} won and paid "
            f"{_format_credits(payout)} credits."
        )
    else:
        title = "Position settled"
        body = (
            f"Your {outcome.name} position in {market.title} did not win. "
            f"Payout: {_format_credits(payout)} credits."
        )
    notify_user(
        user=position.user,
        event_type=Notification.EventType.POSITION_SETTLED,
        title=title,
        body=body,
        data={
            "position_id": position.id,
            "market_id": market.id,
            "market_slug": market.slug,
            "market_title": market.title,
            "outcome_id": outcome.id,
            "outcome_name": outcome.name,
            "payout": payout,
            "payout_decimal": payout / CREDIT_SCALE,
            "is_void": resolution.is_void,
            "winning_outcome_id": resolution.winning_outcome_id,
            "won": won,
        },
    )


def _record_trade_events(*, trade, market, outcomes, user_order, user_id, user_is_buyer):
    ticks = lmsr_prices(_net_shares(outcomes), market.liquidity_b)
    price_vector = {
        str(outcome.id): tick for outcome, tick in zip(outcomes, ticks)
    }
    _record_public_event(
        event_type="trade_executed",
        outcome_id=trade.outcome_id,
        payload={
            "trade_id": trade.id,
            "price": trade.price,
            "quantity": trade.quantity,
            "notional": trade.notional,
            "market_id": market.id,
            "prices": price_vector,
        },
    )
    _record_private_event(
        user_id=user_id,
        event_type="order_filled",
        outcome_id=trade.outcome_id,
        payload={
            "trade_id": trade.id,
            "order_id": user_order.id,
            "side": user_order.side,
            "quantity": trade.quantity,
            "price": trade.price,
            "notional": trade.notional,
        },
    )
    _notify_order_filled(
        user=user_order.user,
        trade=trade,
        market=market,
        outcome=trade.outcome,
        user_order=user_order,
    )


@transaction.atomic
def cancel_order(*, user, order_id):
    order = (
        Order.objects.select_for_update()
        .select_related("outcome", "user")
        .get(pk=order_id, user=user)
    )
    return _cancel_locked_order(order)


def _cancel_locked_order(order):
    if not order.is_open_for_matching:
        return order

    remaining = order.remaining_quantity
    order.status = Order.Status.CANCELLED
    order.cancelled_at = timezone.now()
    order.save(update_fields=["status", "cancelled_at", "updated_at"])
    _record_private_event(
        user_id=order.user_id,
        event_type="order_cancelled",
        outcome_id=order.outcome_id,
        payload={"order_id": order.id, "released_quantity": remaining},
    )
    _notify_order_cancelled(order=order, remaining=remaining)

    if remaining:
        if order.side == Order.Side.BUY:
            release_reserved_credits(
                user_id=order.user_id,
                amount=order.limit_price * remaining,
                idempotency_key=f"order-cancel-release:{order.id}",
                reference_type="order",
                reference_id=order.id,
            )
        else:
            position = Position.objects.select_for_update().get(
                user=order.user,
                outcome=order.outcome,
            )
            position.reserved_quantity -= remaining
            position.save(update_fields=["reserved_quantity", "updated_at"])
    return order


@transaction.atomic
def close_market(*, market_id):
    market = Market.objects.select_for_update().get(pk=market_id)
    if market.status != Market.Status.OPEN:
        raise ValidationError("Only open markets can be closed.")
    open_orders = (
        Order.objects.select_for_update()
        .filter(outcome__market=market)
        .filter(_open_status_filter())
        .select_related("outcome", "user")
        .order_by("id")
    )
    open_order_user_ids = set()
    for order in open_orders:
        open_order_user_ids.add(order.user_id)
        _cancel_locked_order(order)
    market.status = Market.Status.CLOSED
    market.save(update_fields=["status", "updated_at"])
    _notify_market_closed(market=market, extra_user_ids=open_order_user_ids)
    return market


def quote_order(*, user, outcome_id, side, quantity, limit_price):
    """
    Preview an AMM order without mutating any state.

    Returns the fillable quantity, exact cash amount, average price, and the
    price vector that would result from the fill.
    """
    if side not in Order.Side.values:
        raise ValidationError("Side must be buy or sell.")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValidationError("Quantity must be a positive integer.")
    _validate_order_input(
        price=limit_price,
        quantity=quantity,
        client_order_id="quote",
    )

    outcome = MarketOutcome.objects.select_related("market").get(pk=outcome_id)
    market = outcome.market
    if market.status != Market.Status.OPEN:
        raise MarketNotOpen("Quotes are only available for open markets.")
    outcomes = _active_outcomes(market)
    if len(outcomes) < 2:
        raise TradingError("A market needs at least two active outcomes to trade.")
    index = _outcome_index(outcomes, outcome_id)
    outcome = outcomes[index]
    net_shares = _net_shares(outcomes)
    current_ticks = lmsr_prices(net_shares, market.liquidity_b)
    house, house_wallet, house_available = _readonly_house()

    if side == Order.Side.BUY:
        house_positions = _house_inventory(house, outcomes) if house else {}
        inventory_available = _inventory_available(house_positions.get(outcome.id))
        max_quantity = min(
            quantity,
            _max_amm_buy_quantity(
                market=market,
                house_available_credits=house_available,
                inventory_available=inventory_available,
            ),
        )
        user_wallet = Wallet.objects.filter(user=user).first()
        max_cost = user_wallet.available_balance if user_wallet else 0
        fill_quantity = max_buy_quantity(
            net_shares,
            index,
            market.liquidity_b,
            limit_price=limit_price,
            max_quantity=max_quantity,
            max_cost=max_cost,
        )
        notional = buy_cost(net_shares, index, fill_quantity, market.liquidity_b)
        limit_capacity = min(quantity, max_quantity)
        limit_fill = max_buy_quantity(
            net_shares,
            index,
            market.liquidity_b,
            limit_price=limit_price,
            max_quantity=limit_capacity,
        )
        if fill_quantity < quantity and limit_fill < limit_capacity:
            limited_by = "limit"
        elif fill_quantity < quantity and max_quantity < quantity:
            limited_by = "liquidity"
        elif fill_quantity < quantity:
            limited_by = "balance"
        else:
            limited_by = None
        average = average_tick(notional, fill_quantity, side="buy") if fill_quantity else None
    else:
        position = Position.objects.filter(user=user, outcome=outcome).first()
        available_shares = _inventory_available(position)
        max_quantity = min(quantity, available_shares, net_shares[index])
        max_refund = house_available
        fill_quantity = max_sell_quantity(
            net_shares,
            index,
            market.liquidity_b,
            limit_price=limit_price,
            max_quantity=max_quantity,
            max_refund=max_refund,
        )
        notional = sell_refund(net_shares, index, fill_quantity, market.liquidity_b)
        limit_capacity = min(quantity, net_shares[index])
        limit_fill = max_sell_quantity(
            net_shares,
            index,
            market.liquidity_b,
            limit_price=limit_price,
            max_quantity=limit_capacity,
        )
        if fill_quantity < quantity and limit_fill < limit_capacity:
            limited_by = "limit"
        elif fill_quantity < quantity and available_shares < quantity:
            limited_by = "position"
        elif fill_quantity < quantity:
            limited_by = "liquidity"
        else:
            limited_by = None
        average = average_tick(notional, fill_quantity, side="sell") if fill_quantity else None

    after = list(net_shares)
    if fill_quantity:
        after[index] += fill_quantity if side == Order.Side.BUY else -fill_quantity
    after_ticks = lmsr_prices(after, market.liquidity_b)

    return {
        "outcome": outcome.id,
        "outcome_name": outcome.name,
        "market_id": market.id,
        "market_slug": market.slug,
        "side": side,
        "requested_quantity": quantity,
        "fillable_quantity": fill_quantity,
        "notional": notional,
        "notional_decimal": notional / CREDIT_SCALE,
        "average_price": average,
        "average_price_decimal": average / CREDIT_SCALE if average is not None else None,
        "current_price": current_ticks[index],
        "current_price_decimal": current_ticks[index] / CREDIT_SCALE,
        "prices_after": {
            str(item.id): tick for item, tick in zip(outcomes, after_ticks)
        },
        "prices_after_decimal": {
            str(item.id): tick / CREDIT_SCALE
            for item, tick in zip(outcomes, after_ticks)
        },
        "limited_by": limited_by,
    }


@transaction.atomic
def resolve_market(
    *,
    market_id,
    winning_outcome_id=None,
    is_void=False,
    resolved_by,
    evidence_summary,
    evidence_url="",
):
    market = Market.objects.select_for_update().get(pk=market_id)
    existing = MarketResolution.objects.filter(market=market).first()
    if existing:
        settle_market(market_id=market.id)
        return existing
    if market.status not in {Market.Status.CLOSED, Market.Status.OPEN}:
        raise ValidationError("Only open or closed markets can be resolved.")
    winning_outcome = None
    if is_void:
        winning_outcome_id = None
    else:
        winning_outcome = MarketOutcome.objects.get(pk=winning_outcome_id, market=market)

    resolution = MarketResolution.objects.create(
        market=market,
        winning_outcome=winning_outcome,
        is_void=is_void,
        evidence_summary=evidence_summary,
        evidence_url=evidence_url,
        resolved_by=resolved_by,
    )
    market.status = Market.Status.VOID if is_void else Market.Status.RESOLVED
    market.save(update_fields=["status", "updated_at"])
    settle_market(market_id=market.id)
    return resolution


@transaction.atomic
def settle_market(*, market_id):
    market = Market.objects.select_for_update().get(pk=market_id)
    resolution = market.resolution

    if market.issued_quantity:
        house = get_house_user()
        collateral = CREDIT_SCALE * market.issued_quantity
        # Each minted set is backed by CREDIT_SCALE of reserved collateral and
        # pays exactly CREDIT_SCALE to the winning outcome. Consuming it funds
        # resolved payouts in full and covers the base cost basis on a void
        # market; refunds above that remain platform-funded, as before.
        consume_reserved_credits(
            user_id=house.id,
            amount=collateral,
            idempotency_key=f"issuance-collateral-settle:{market.id}",
            reference_type="market",
            reference_id=market.id,
        )

    positions = (
        Position.objects.select_for_update()
        .filter(outcome__market=market, settled_at__isnull=True)
        .select_related("outcome", "user")
        .order_by("id")
    )
    for position in positions:
        if Settlement.objects.filter(position=position).exists():
            continue
        if resolution.is_void:
            payout = position.cost_basis
        elif position.outcome_id == resolution.winning_outcome_id:
            payout = position.quantity * CREDIT_SCALE
        else:
            payout = 0
        if payout > 0:
            credit_available_credits(
                user_id=position.user_id,
                amount=payout,
                idempotency_key=f"settlement-payout:{position.id}",
                reference_type="settlement",
                reference_id=position.id,
                description="Market settlement payout",
            )
        position.realized_pnl += payout - position.cost_basis
        position.quantity = 0
        position.reserved_quantity = 0
        position.cost_basis = 0
        position.settled_at = timezone.now()
        position.save(
            update_fields=[
                "quantity",
                "reserved_quantity",
                "cost_basis",
                "realized_pnl",
                "settled_at",
                "updated_at",
            ]
        )
        Settlement.objects.create(
            position=position,
            payout=payout,
            idempotency_key=f"settlement:{position.id}",
        )
        _record_private_event(
            user_id=position.user_id,
            event_type="position_settled",
            outcome_id=position.outcome_id,
            payload={"position_id": position.id, "payout": payout, "market_id": market.id},
        )
        if position.user.email != settings.MARKETX_HOUSE_EMAIL:
            _notify_position_settled(
                position=position,
                market=market,
                resolution=resolution,
                payout=payout,
            )

    if not resolution.is_void:
        _record_resolution_price_points(market=market, resolution=resolution)


def _record_resolution_price_points(*, market, resolution):
    bucket = timezone.now().replace(second=0, microsecond=0)
    for outcome in market.outcomes.filter(is_active=True):
        price = CREDIT_SCALE if outcome.pk == resolution.winning_outcome_id else 0
        MarketPricePoint.objects.update_or_create(
            outcome=outcome,
            timestamp=bucket,
            defaults={
                "price": price,
                "volume": 0,
                "resolution_seconds": 0,
                "source_trade_id": None,
            },
        )


def get_order_book_depth(*, outcome_id, limit=20):
    """
    Return synthetic AMM depth for an outcome.

    The LMSR market maker has no resting user orders, so the book is derived
    from the cost of buying or selling geometrically increasing quantities.
    """
    outcome = MarketOutcome.objects.select_related("market").get(pk=outcome_id)
    market = outcome.market
    outcomes = _active_outcomes(market)
    if len(outcomes) < 2:
        return {
            "current_price": None,
            "current_price_decimal": None,
            "bids": [],
            "asks": [],
        }
    index = _outcome_index(outcomes, outcome.id)
    net_shares = _net_shares(outcomes)
    resolution = getattr(market, "resolution", None)
    if (
        resolution is not None
        and not resolution.is_void
        and resolution.winning_outcome_id
    ):
        ticks = [
            CREDIT_SCALE if item.pk == resolution.winning_outcome_id else 0
            for item in outcomes
        ]
    else:
        ticks = lmsr_prices(net_shares, market.liquidity_b)
    house, house_wallet, house_available = _readonly_house()
    if market.status != Market.Status.OPEN:
        return {
            "current_price": ticks[index],
            "current_price_decimal": ticks[index] / CREDIT_SCALE,
            "bids": [],
            "asks": [],
        }

    house_positions = _house_inventory(house, outcomes) if house else {}
    inventory_available = _inventory_available(house_positions.get(outcome.id))
    max_ask = _max_amm_buy_quantity(
        market=market,
        house_available_credits=house_available,
        inventory_available=inventory_available,
    )
    max_bid = max_sell_quantity(
        net_shares,
        index,
        market.liquidity_b,
        limit_price=MIN_PRICE,
        max_quantity=net_shares[index],
        max_refund=house_available,
    )

    def levels(max_quantity, *, side):
        rows = []
        cumulative = 0
        step = 1
        previous = 0
        while len(rows) < limit and cumulative < max_quantity:
            quantity = min(step, max_quantity - cumulative)
            new_cumulative = cumulative + quantity
            if side == Order.Side.BUY:
                total = buy_cost(net_shares, index, new_cumulative, market.liquidity_b)
                incremental = total - previous
                price = average_tick(incremental, quantity, side="buy")
            else:
                total = sell_refund(net_shares, index, new_cumulative, market.liquidity_b)
                incremental = total - previous
                price = average_tick(incremental, quantity, side="sell")
            rows.append({"price": price, "quantity": quantity})
            previous = total
            cumulative = new_cumulative
            step *= 2
        return rows

    return {
        "current_price": ticks[index],
        "current_price_decimal": ticks[index] / CREDIT_SCALE,
        "bids": levels(max_bid, side=Order.Side.SELL),
        "asks": levels(max_ask, side=Order.Side.BUY),
    }


def _record_public_event(*, event_type, outcome_id, payload):
    ActivityEvent.objects.create(
        event_type=event_type,
        visibility=ActivityEvent.Visibility.PUBLIC,
        outcome_id=outcome_id,
        payload=payload,
    )


def _record_private_event(*, user_id, event_type, outcome_id, payload):
    ActivityEvent.objects.create(
        event_type=event_type,
        visibility=ActivityEvent.Visibility.PRIVATE,
        user_id=user_id,
        outcome_id=outcome_id,
        payload=payload,
    )
