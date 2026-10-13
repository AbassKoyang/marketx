from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ExpressionWrapper, F, Q, Sum, BigIntegerField
from django.utils import timezone

from apps.markets.models import Market, MarketOutcome, MarketPricePoint, MarketResolution
from apps.wallet.services import (
    consume_reserved_credits,
    credit_available_credits,
    release_reserved_credits,
    reserve_credits,
)
from .constants import CREDIT_SCALE, MAX_PRICE, MIN_PRICE
from .models import ActivityEvent, Order, Position, Settlement, Trade


class TradingError(ValidationError):
    """Base exception for trading operations."""


class MarketNotOpen(TradingError):
    pass


class InsufficientPosition(TradingError):
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


def _set_order_fill_state(order):
    if order.filled_quantity >= order.original_quantity:
        order.status = Order.Status.FILLED
    elif order.filled_quantity > 0:
        order.status = Order.Status.PARTIALLY_FILLED
    else:
        order.status = Order.Status.OPEN


def _open_status_filter():
    return Q(status=Order.Status.OPEN) | Q(status=Order.Status.PARTIALLY_FILLED)


@transaction.atomic
def submit_limit_order(*, user, outcome_id, side, limit_price, quantity, client_order_id):
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

    outcome = (
        MarketOutcome.objects.select_related("market")
        .select_for_update()
        .get(pk=outcome_id)
    )
    now = timezone.now()
    if outcome.market.status != Market.Status.OPEN:
        raise MarketNotOpen("Orders can only be placed on open markets.")
    if outcome.market.opens_at > now or outcome.market.closes_at <= now:
        raise MarketNotOpen("Market is not inside its trading window.")
    if not outcome.is_active:
        raise ValidationError("Outcome is not active.")

    if side == Order.Side.BUY:
        reserve_credits(
            user_id=user.id,
            amount=limit_price * quantity,
            idempotency_key=f"order-reserve:{user.id}:{client_order_id}",
            reference_type="order",
            reference_id=client_order_id,
        )
    else:
        position, _ = Position.objects.select_for_update().get_or_create(
            user=user,
            outcome=outcome,
        )
        if position.quantity - position.reserved_quantity < quantity:
            raise InsufficientPosition("Not enough unreserved contracts to sell.")
        position.reserved_quantity += quantity
        position.save(update_fields=["reserved_quantity", "updated_at"])

    order = Order.objects.create(
        user=user,
        outcome=outcome,
        side=side,
        limit_price=limit_price,
        original_quantity=quantity,
        client_order_id=client_order_id,
    )
    _record_private_event(
        user_id=user.id,
        event_type="order_placed",
        outcome_id=outcome.id,
        payload={"order_id": order.id, "side": side, "quantity": quantity, "limit_price": limit_price},
    )
    _match_order(order.id)
    return Order.objects.get(pk=order.pk)


@transaction.atomic
def cancel_order(*, user, order_id):
    order = (
        Order.objects.select_for_update()
        .select_related("outcome")
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
        user_id=user.id,
        event_type="order_cancelled",
        outcome_id=order.outcome_id,
        payload={"order_id": order.id, "released_quantity": remaining},
    )

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
    for order in open_orders:
        _cancel_locked_order(order)
    market.status = Market.Status.CLOSED
    market.save(update_fields=["status", "updated_at"])
    return market


def _candidate_order(incoming):
    opposite = Order.Side.SELL if incoming.side == Order.Side.BUY else Order.Side.BUY
    crossing = (
        Q(limit_price__lte=incoming.limit_price)
        if incoming.side == Order.Side.BUY
        else Q(limit_price__gte=incoming.limit_price)
    )
    ordering = ["limit_price", "created_at", "id"]
    if incoming.side == Order.Side.SELL:
        ordering = ["-limit_price", "created_at", "id"]
    return (
        Order.objects.select_for_update()
        .filter(outcome=incoming.outcome, side=opposite)
        .filter(_open_status_filter())
        .filter(crossing)
        .exclude(user=incoming.user)
        .order_by(*ordering)
        .first()
    )


def _match_order(order_id):
    incoming = (
        Order.objects.select_for_update()
        .select_related("outcome")
        .get(pk=order_id)
    )

    while incoming.remaining_quantity > 0 and incoming.is_open_for_matching:
        resting = _candidate_order(incoming)
        if resting is None:
            break

        quantity = min(incoming.remaining_quantity, resting.remaining_quantity)
        price = resting.limit_price
        buy_order = incoming if incoming.side == Order.Side.BUY else resting
        sell_order = resting if incoming.side == Order.Side.BUY else incoming

        trade = Trade.objects.create(
            outcome=incoming.outcome,
            buy_order=buy_order,
            sell_order=sell_order,
            buyer=buy_order.user,
            seller=sell_order.user,
            price=price,
            quantity=quantity,
        )
        _record_public_event(
            event_type="trade_executed",
            outcome_id=trade.outcome_id,
            payload={
                "trade_id": trade.id,
                "price": trade.price,
                "quantity": trade.quantity,
            },
        )
        _record_private_event(
            user_id=trade.buyer_id,
            event_type="order_filled",
            outcome_id=trade.outcome_id,
            payload={"trade_id": trade.id, "order_id": buy_order.id, "quantity": quantity, "price": price},
        )
        _record_private_event(
            user_id=trade.seller_id,
            event_type="order_filled",
            outcome_id=trade.outcome_id,
            payload={"trade_id": trade.id, "order_id": sell_order.id, "quantity": quantity, "price": price},
        )
        _apply_trade(trade)

        for order in (incoming, resting):
            order.filled_quantity = F("filled_quantity") + quantity
            order.save(update_fields=["filled_quantity", "updated_at"])
            order.refresh_from_db()
            _set_order_fill_state(order)
            order.save(update_fields=["status", "updated_at"])


def _apply_trade(trade):
    buyer_position, _ = Position.objects.select_for_update().get_or_create(
        user=trade.buyer,
        outcome=trade.outcome,
    )
    seller_position = Position.objects.select_for_update().get(
        user=trade.seller,
        outcome=trade.outcome,
    )

    buyer_cost = trade.price * trade.quantity
    buyer_limit = trade.buy_order.limit_price * trade.quantity
    credit_spent_key = f"trade-spend:{trade.id}:buyer"
    consume_reserved_credits(
        user_id=trade.buyer_id,
        amount=buyer_cost,
        idempotency_key=credit_spent_key,
        reference_type="trade",
        reference_id=trade.id,
    )
    excess = buyer_limit - buyer_cost
    if excess > 0:
        release_reserved_credits(
            user_id=trade.buyer_id,
            amount=excess,
            idempotency_key=f"trade-price-improvement:{trade.id}:buyer",
            reference_type="trade",
            reference_id=trade.id,
        )

    credit_available_credits(
        user_id=trade.seller_id,
        amount=buyer_cost,
        idempotency_key=f"trade-proceeds:{trade.id}:seller",
        reference_type="trade",
        reference_id=trade.id,
        description="Trade proceeds credited to seller",
    )

    released_cost_basis = 0
    if seller_position.quantity:
        released_cost_basis = (
            seller_position.cost_basis * trade.quantity // seller_position.quantity
        )

    buyer_position.quantity += trade.quantity
    buyer_position.cost_basis += buyer_cost
    buyer_position.save(update_fields=["quantity", "cost_basis", "updated_at"])

    seller_position.quantity -= trade.quantity
    seller_position.reserved_quantity -= trade.quantity
    seller_position.cost_basis -= released_cost_basis
    seller_position.realized_pnl += buyer_cost - released_cost_basis
    seller_position.save(
        update_fields=[
            "quantity",
            "reserved_quantity",
            "cost_basis",
            "realized_pnl",
            "updated_at",
        ]
    )

    _record_trade_price_point(trade)


def _record_trade_price_point(trade):
    MarketPricePoint.objects.update_or_create(
        outcome=trade.outcome,
        timestamp=trade.created_at.replace(second=0, microsecond=0),
        defaults={
            "price": trade.price,
            "volume": Trade.objects.filter(
                outcome=trade.outcome,
                created_at__gte=trade.created_at.replace(second=0, microsecond=0),
                created_at__lt=trade.created_at.replace(second=0, microsecond=0)
                + timedelta(minutes=1),
            ).aggregate(total=Sum("quantity"))["total"]
            or 0,
            "resolution_seconds": max(
                0,
                int((trade.outcome.market.resolution_deadline - trade.created_at).total_seconds()),
            ),
            "source_trade_id": trade.id,
        },
    )


@transaction.atomic
def resolve_market(*, market_id, winning_outcome_id=None, is_void=False, resolved_by, evidence_summary, evidence_url=""):
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


def get_order_book_depth(*, outcome_id, limit=20):
    open_orders = Order.objects.filter(outcome_id=outcome_id).filter(_open_status_filter())
    remaining = ExpressionWrapper(
        F("original_quantity") - F("filled_quantity"),
        output_field=BigIntegerField(),
    )
    bids = (
        open_orders.filter(side=Order.Side.BUY)
        .values("limit_price")
        .annotate(quantity=Sum(remaining))
        .order_by("-limit_price")[:limit]
    )
    asks = (
        open_orders.filter(side=Order.Side.SELL)
        .values("limit_price")
        .annotate(quantity=Sum(remaining))
        .order_by("limit_price")[:limit]
    )
    return {
        "bids": [{"price": row["limit_price"], "quantity": row["quantity"]} for row in bids],
        "asks": [{"price": row["limit_price"], "quantity": row["quantity"]} for row in asks],
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
