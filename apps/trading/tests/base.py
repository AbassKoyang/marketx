from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.markets.models import Market, MarketCategory, MarketOutcome
from apps.trading.constants import CREDIT_SCALE
from apps.trading.house import get_house_user
from apps.trading.models import Order, Position
from apps.trading.pricing import lmsr_prices
from apps.trading.services import submit_limit_order
from apps.wallet.models import Wallet
from apps.wallet.services import get_or_create_wallet


class AmmTestBase(TestCase):
    """Shared funded users plus binary and three-outcome markets."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(
            email="amm-admin@example.com",
            password="password",
            first_name="Amm",
            last_name="Admin",
            username="amm-admin",
            is_staff=True,
        )
        cls.buyer = User.objects.create_user(
            email="amm-buyer@example.com",
            password="password",
            first_name="First",
            last_name="Buyer",
            username="amm-buyer",
        )
        cls.buyer2 = User.objects.create_user(
            email="amm-buyer2@example.com",
            password="password",
            first_name="Second",
            last_name="Buyer",
            username="amm-buyer2",
        )
        cls.seller = User.objects.create_user(
            email="amm-seller@example.com",
            password="password",
            first_name="Share",
            last_name="Seller",
            username="amm-seller",
        )
        for user in (cls.buyer, cls.buyer2, cls.seller):
            get_or_create_wallet(user, 100_000)

        cls.category = MarketCategory.objects.create(name="Amm", slug="amm")
        cls.market = cls._create_market(
            title="Will the market maker work?",
            market_type=Market.MarketType.BINARY,
            names=["Yes", "No"],
        )
        cls.yes, cls.no = list(
            MarketOutcome.objects.filter(market=cls.market).order_by(
                "display_order", "id"
            )
        )

        cls.multi_market = cls._create_market(
            title="Which outcome wins?",
            market_type=Market.MarketType.MULTI_OUTCOME,
            names=["Alpha", "Beta", "Gamma"],
        )
        cls.alpha, cls.beta, cls.gamma = list(
            MarketOutcome.objects.filter(market=cls.multi_market).order_by(
                "display_order", "id"
            )
        )

    @classmethod
    def _create_market(cls, *, title, market_type, names=None, issuance_limit=10_000):
        now = timezone.now()
        market = Market.objects.create(
            title=title,
            question=title,
            market_type=market_type,
            category=cls.category,
            created_by=cls.admin,
            resolution_criteria="Test resolution criteria.",
            resolution_source_description="Test source",
            status=Market.Status.OPEN,
            opens_at=now - timedelta(hours=1),
            closes_at=now + timedelta(hours=1),
            resolution_deadline=now + timedelta(days=1),
            issuance_limit=issuance_limit,
        )
        for index, name in enumerate(names or [], start=1):
            MarketOutcome.objects.create(
                market=market,
                name=name,
                slug=name.lower().replace(" ", "-"),
                display_order=index,
            )
        return market

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def buy(self, user, outcome, price, quantity, client_order_id):
        return submit_limit_order(
            user=user,
            outcome_id=outcome.id,
            side=Order.Side.BUY,
            limit_price=price,
            quantity=quantity,
            client_order_id=client_order_id,
        )

    def sell(self, user, outcome, price, quantity, client_order_id):
        return submit_limit_order(
            user=user,
            outcome_id=outcome.id,
            side=Order.Side.SELL,
            limit_price=price,
            quantity=quantity,
            client_order_id=client_order_id,
        )

    def wallet(self, user):
        wallet = Wallet.objects.get(user=user)
        wallet.refresh_from_db()
        return wallet

    def house(self):
        return get_house_user()

    def house_wallet(self):
        wallet = Wallet.objects.get(user=self.house())
        wallet.refresh_from_db()
        return wallet

    def position(self, user, outcome):
        return Position.objects.get(user=user, outcome=outcome)

    def prices(self, market=None):
        market = market or self.market
        outcomes = list(
            MarketOutcome.objects.filter(market=market, is_active=True).order_by(
                "display_order", "id"
            )
        )
        return lmsr_prices(
            [outcome.net_shares for outcome in outcomes],
            market.liquidity_b,
        )

    def assert_prices_sum(self, market=None):
        ticks = self.prices(market)
        self.assertEqual(sum(ticks), CREDIT_SCALE)
        return ticks
