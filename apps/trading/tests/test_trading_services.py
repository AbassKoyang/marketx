from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.markets.models import Market, MarketCategory, MarketOutcome
from apps.wallet.services import get_or_create_wallet
from apps.trading.constants import CREDIT_SCALE
from apps.trading.models import Order, Position, Trade
from apps.trading.services import cancel_order, close_market, resolve_market, submit_limit_order


class TradingServiceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(
            email="admin@example.com",
            password="password",
            first_name="Admin",
            last_name="User",
            username="admin-user",
            is_staff=True,
        )
        self.buyer = User.objects.create_user(
            email="buyer@example.com",
            password="password",
            first_name="Buyer",
            last_name="User",
            username="buyer-user",
        )
        self.seller = User.objects.create_user(
            email="seller@example.com",
            password="password",
            first_name="Seller",
            last_name="User",
            username="seller-user",
        )
        get_or_create_wallet(self.buyer, 100_000)
        get_or_create_wallet(self.seller, 100_000)

        category = MarketCategory.objects.create(name="Sports", slug="sports")
        now = timezone.now()
        self.market = Market.objects.create(
            title="BUK final",
            question="Will Team A win?",
            market_type=Market.MarketType.BINARY,
            category=category,
            created_by=self.admin,
            resolution_criteria="Official final score decides the market.",
            resolution_source_description="BUK sports board",
            status=Market.Status.OPEN,
            opens_at=now - timedelta(hours=1),
            closes_at=now + timedelta(hours=1),
            resolution_deadline=now + timedelta(days=1),
        )
        self.outcome = MarketOutcome.objects.create(
            market=self.market,
            name="Team A",
            slug="team-a",
            display_order=1,
        )
        Position.objects.create(
            user=self.seller,
            outcome=self.outcome,
            quantity=10,
            cost_basis=2_000,
        )

    def test_price_time_priority_and_resting_price_execution(self):
        first = submit_limit_order(
            user=self.seller,
            outcome_id=self.outcome.id,
            side=Order.Side.SELL,
            limit_price=4_000,
            quantity=3,
            client_order_id="sell-1",
        )
        second = submit_limit_order(
            user=self.seller,
            outcome_id=self.outcome.id,
            side=Order.Side.SELL,
            limit_price=4_000,
            quantity=2,
            client_order_id="sell-2",
        )

        buy = submit_limit_order(
            user=self.buyer,
            outcome_id=self.outcome.id,
            side=Order.Side.BUY,
            limit_price=5_000,
            quantity=4,
            client_order_id="buy-1",
        )

        self.assertEqual(Trade.objects.count(), 2)
        trades = list(Trade.objects.order_by("created_at", "id"))
        self.assertEqual(trades[0].sell_order_id, first.id)
        self.assertEqual(trades[0].price, 4_000)
        self.assertEqual(trades[0].quantity, 3)
        self.assertEqual(trades[1].sell_order_id, second.id)
        self.assertEqual(trades[1].quantity, 1)
        buy.refresh_from_db()
        self.assertEqual(buy.status, Order.Status.FILLED)

    def test_no_match_when_prices_do_not_cross_and_cancel_releases(self):
        order = submit_limit_order(
            user=self.buyer,
            outcome_id=self.outcome.id,
            side=Order.Side.BUY,
            limit_price=3_000,
            quantity=2,
            client_order_id="buy-low",
        )
        self.assertEqual(Trade.objects.count(), 0)
        wallet = self.buyer.wallet
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, 6_000)

        cancel_order(user=self.buyer, order_id=order.id)
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, 0)
        self.assertEqual(wallet.available_balance, 100_000)

    def test_winning_settlement_pays_contract_value_once(self):
        submit_limit_order(
            user=self.seller,
            outcome_id=self.outcome.id,
            side=Order.Side.SELL,
            limit_price=4_000,
            quantity=1,
            client_order_id="sell-settle",
        )
        submit_limit_order(
            user=self.buyer,
            outcome_id=self.outcome.id,
            side=Order.Side.BUY,
            limit_price=4_000,
            quantity=1,
            client_order_id="buy-settle",
        )

        resolve_market(
            market_id=self.market.id,
            winning_outcome_id=self.outcome.id,
            resolved_by=self.admin,
            evidence_summary="Official result confirmed Team A.",
        )
        wallet = self.buyer.wallet
        wallet.refresh_from_db()
        self.assertEqual(wallet.available_balance, 100_000 - 4_000 + CREDIT_SCALE)

        resolve_count = Position.objects.get(user=self.buyer, outcome=self.outcome).settlement
        self.assertEqual(resolve_count.payout, CREDIT_SCALE)

    def test_close_market_cancels_open_orders_and_releases_reservations(self):
        submit_limit_order(
            user=self.buyer,
            outcome_id=self.outcome.id,
            side=Order.Side.BUY,
            limit_price=3_000,
            quantity=2,
            client_order_id="buy-before-close",
        )
        close_market(market_id=self.market.id)

        self.market.refresh_from_db()
        self.assertEqual(self.market.status, Market.Status.CLOSED)
        wallet = self.buyer.wallet
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_balance, 0)
        self.assertEqual(wallet.available_balance, 100_000)
