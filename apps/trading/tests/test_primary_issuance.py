from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.markets.models import Market, MarketCategory, MarketOutcome
from apps.trading.constants import CREDIT_SCALE
from apps.trading.house import get_house_user
from apps.trading.models import Order, Position, Settlement, Trade
from apps.trading.services import (
    InsufficientPosition,
    resolve_market,
    submit_limit_order,
)
from apps.wallet.models import Wallet
from apps.wallet.services import get_or_create_wallet


class IssuanceTestBase(TestCase):
    """Shared binary market with a funded house and two funded buyers."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(
            email="issuance-admin@example.com",
            password="password",
            first_name="Issuance",
            last_name="Admin",
            username="issuance-admin",
            is_staff=True,
        )
        cls.buyer = User.objects.create_user(
            email="issuance-buyer@example.com",
            password="password",
            first_name="First",
            last_name="Buyer",
            username="issuance-buyer",
        )
        cls.buyer2 = User.objects.create_user(
            email="issuance-buyer2@example.com",
            password="password",
            first_name="Second",
            last_name="Buyer",
            username="issuance-buyer2",
        )
        cls.seller = User.objects.create_user(
            email="issuance-seller@example.com",
            password="password",
            first_name="Share",
            last_name="Seller",
            username="issuance-seller",
        )
        for user in (cls.buyer, cls.buyer2, cls.seller):
            get_or_create_wallet(user, 100_000)

        cls.category = MarketCategory.objects.create(name="Issuance", slug="issuance")
        cls.market = cls._create_market(
            title="Will the first buyer be filled?",
            market_type=Market.MarketType.BINARY,
        )
        cls.yes = MarketOutcome.objects.create(
            market=cls.market,
            name="Yes",
            slug="yes",
            display_order=1,
        )
        cls.no = MarketOutcome.objects.create(
            market=cls.market,
            name="No",
            slug="no",
            display_order=2,
        )

    @classmethod
    def _create_market(cls, *, title, market_type, issuance_limit=10_000, names=None):
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
        outcomes = []
        for index, name in enumerate(names or [], start=1):
            outcomes.append(
                MarketOutcome.objects.create(
                    market=market,
                    name=name,
                    slug=name.lower().replace(" ", "-"),
                    display_order=index,
                )
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


class FirstBuyerIssuanceTests(IssuanceTestBase):
    def test_first_buy_fills_without_any_seller(self):
        self.assertEqual(Trade.objects.count(), 0)

        order = self.buy(self.buyer, self.yes, 4_000, 5, "first-buy")

        self.assertEqual(order.status, Order.Status.FILLED)
        self.assertEqual(order.filled_quantity, 5)
        self.assertEqual(Trade.objects.count(), 1)

        trade = Trade.objects.get()
        self.assertEqual(trade.buyer_id, self.buyer.id)
        self.assertEqual(trade.seller_id, self.house().id)
        self.assertEqual(trade.price, 4_000)
        self.assertEqual(trade.quantity, 5)
        self.assertTrue(trade.sell_order.client_order_id.startswith("issuance:"))

        buyer_wallet = self.wallet(self.buyer)
        self.assertEqual(buyer_wallet.available_balance, 100_000 - 20_000)
        self.assertEqual(buyer_wallet.reserved_balance, 0)

        buyer_position = self.position(self.buyer, self.yes)
        self.assertEqual(buyer_position.quantity, 5)
        self.assertEqual(buyer_position.cost_basis, 20_000)

        market = Market.objects.get(pk=self.market.pk)
        self.assertEqual(market.issued_quantity, 5)

        # Every minted set creates one share of each outcome. The house sold
        # the requested outcome and keeps the complementary shares.
        self.assertEqual(self.position(self.house(), self.yes).quantity, 0)
        complementary = self.position(self.house(), self.no)
        self.assertEqual(complementary.quantity, 5)
        self.assertEqual(complementary.cost_basis, 6_000 * 5)

        house_wallet = self.house_wallet()
        self.assertEqual(
            house_wallet.reserved_balance, CREDIT_SCALE * 5
        )
        self.assertEqual(
            house_wallet.available_balance,
            settings.MARKETX_HOUSE_CREDITS - CREDIT_SCALE * 5 + 4_000 * 5,
        )

    def test_second_outcome_buy_uses_house_inventory_before_minting(self):
        self.buy(self.buyer, self.yes, 4_000, 5, "first-buy")
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 5)

        self.buy(self.buyer2, self.no, 6_000, 5, "second-outcome-buy")

        # No new sets were minted: the house already held five NO shares.
        market = Market.objects.get(pk=self.market.pk)
        self.assertEqual(market.issued_quantity, 5)
        self.assertEqual(self.position(self.house(), self.no).quantity, 0)
        self.assertEqual(self.position(self.house(), self.no).cost_basis, 0)
        self.assertEqual(
            self.house_wallet().reserved_balance, CREDIT_SCALE * 5
        )

        self.assertEqual(self.position(self.buyer2, self.no).quantity, 5)
        self.assertEqual(self.wallet(self.buyer2).available_balance, 70_000)

        resolve_market(
            market_id=self.market.id,
            winning_outcome_id=self.yes.id,
            resolved_by=self.admin,
            evidence_summary="Yes won.",
        )

        # Winners are paid from the reserved collateral and the house ends
        # exactly where it started.
        self.assertEqual(self.wallet(self.buyer).available_balance, 130_000)
        self.assertEqual(self.wallet(self.buyer2).available_balance, 70_000)
        self.assertEqual(self.house_wallet().available_balance, settings.MARKETX_HOUSE_CREDITS)
        self.assertEqual(self.house_wallet().reserved_balance, 0)

    def test_first_buy_on_non_primary_outcome(self):
        self.buy(self.buyer, self.no, 6_000, 3, "first-no-buy")

        house = self.house()
        self.assertEqual(self.position(house, self.no).quantity, 0)
        self.assertEqual(self.position(house, self.no).cost_basis, 0)
        self.assertEqual(self.position(house, self.yes).quantity, 3)
        self.assertEqual(self.position(house, self.yes).cost_basis, 4_000 * 3)
        self.assertEqual(self.house_wallet().reserved_balance, CREDIT_SCALE * 3)
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 3)

        resolve_market(
            market_id=self.market.id,
            winning_outcome_id=self.yes.id,
            resolved_by=self.admin,
            evidence_summary="Yes won.",
        )
        self.assertEqual(self.wallet(self.buyer).available_balance, 82_000)
        self.assertEqual(
            self.house_wallet().available_balance,
            settings.MARKETX_HOUSE_CREDITS + 18_000,
        )
        self.assertEqual(self.house_wallet().reserved_balance, 0)

    def test_multi_outcome_issuance_mints_one_share_of_every_outcome(self):
        market = self._create_market(
            title="Which outcome wins?",
            market_type=Market.MarketType.MULTI_OUTCOME,
            names=["Alpha", "Beta", "Gamma"],
        )
        alpha, beta, gamma = list(
            MarketOutcome.objects.filter(market=market).order_by(
                "display_order", "id"
            )
        )

        self.buy(self.buyer, alpha, 3_000, 10, "multi-buy")

        market.refresh_from_db()
        self.assertEqual(market.issued_quantity, 10)
        house = self.house()
        self.assertEqual(self.position(house, alpha).quantity, 0)
        self.assertEqual(self.position(house, beta).quantity, 10)
        self.assertEqual(self.position(house, gamma).quantity, 10)
        # Complement cost basis is split evenly across the unsold outcomes.
        self.assertEqual(self.position(house, beta).cost_basis, 35_000)
        self.assertEqual(self.position(house, gamma).cost_basis, 35_000)
        self.assertEqual(self.house_wallet().reserved_balance, CREDIT_SCALE * 10)

        resolve_market(
            market_id=market.id,
            winning_outcome_id=beta.id,
            resolved_by=self.admin,
            evidence_summary="Beta won.",
        )
        self.assertEqual(self.wallet(self.buyer).available_balance, 70_000)
        self.assertEqual(
            self.house_wallet().available_balance,
            settings.MARKETX_HOUSE_CREDITS + 30_000,
        )
        self.assertEqual(self.house_wallet().reserved_balance, 0)

    def test_partial_seller_liquidity_then_issuance_for_remainder(self):
        Position.objects.create(
            user=self.seller,
            outcome=self.yes,
            quantity=2,
            cost_basis=4_000,
        )
        self.sell(self.seller, self.yes, 3_000, 2, "seller-ask")

        order = self.buy(self.buyer, self.yes, 4_000, 5, "split-buy")

        self.assertEqual(order.status, Order.Status.FILLED)
        trades = list(Trade.objects.order_by("id"))
        self.assertEqual([trade.price for trade in trades], [3_000, 4_000])
        self.assertEqual([trade.quantity for trade in trades], [2, 3])
        self.assertEqual(trades[0].seller_id, self.seller.id)
        self.assertEqual(trades[1].seller_id, self.house().id)

        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 3)
        self.assertEqual(self.position(self.buyer, self.yes).quantity, 5)
        self.assertEqual(self.position(self.buyer, self.yes).cost_basis, 6_000 + 12_000)
        # The buyer reserved 20,000 and paid 18,000; the 2,000 price
        # improvement on the seller fill was released immediately.
        buyer_wallet = self.wallet(self.buyer)
        self.assertEqual(buyer_wallet.available_balance, 82_000)
        self.assertEqual(buyer_wallet.reserved_balance, 0)

    def test_issuance_cap_partially_fills_and_leaves_remainder_open(self):
        market = Market.objects.get(pk=self.market.pk)
        market.issuance_limit = 3
        market.save(update_fields=["issuance_limit"])

        order = self.buy(self.buyer, self.yes, 4_000, 5, "capped-buy")

        self.assertEqual(order.status, Order.Status.PARTIALLY_FILLED)
        self.assertEqual(order.filled_quantity, 3)
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 3)
        buyer_wallet = self.wallet(self.buyer)
        self.assertEqual(buyer_wallet.available_balance, 80_000)
        self.assertEqual(buyer_wallet.reserved_balance, 8_000)

        second = self.buy(self.buyer2, self.yes, 4_000, 1, "beyond-cap-buy")
        self.assertEqual(second.status, Order.Status.OPEN)
        self.assertEqual(second.filled_quantity, 0)
        self.assertEqual(Trade.objects.count(), 1)
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 3)

    @override_settings(MARKETX_HOUSE_CREDITS=20_000)
    def test_issuance_is_limited_by_house_collateral(self):
        order = self.buy(self.buyer, self.yes, 4_000, 5, "underfunded-buy")

        self.assertEqual(order.status, Order.Status.PARTIALLY_FILLED)
        self.assertEqual(order.filled_quantity, 2)
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 2)
        house_wallet = self.house_wallet()
        self.assertEqual(house_wallet.available_balance, 8_000)
        self.assertEqual(house_wallet.reserved_balance, 20_000)

    def test_outcome_cannot_be_deactivated_after_issuance(self):
        from apps.markets.serializers import MarketOutcomeSerializer

        self.buy(self.buyer, self.yes, 4_000, 1, "issued-buy")

        serializer = MarketOutcomeSerializer(
            self.no, data={"is_active": False}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

        outcome = MarketOutcome.objects.get(pk=self.no.pk)
        outcome.is_active = False
        with self.assertRaises(ValidationError):
            outcome.full_clean()

    def test_naked_selling_is_still_rejected(self):
        with self.assertRaises(InsufficientPosition):
            self.sell(self.buyer, self.yes, 4_000, 1, "naked-sell")

        self.assertEqual(Trade.objects.count(), 0)
        self.assertFalse(
            Market.objects.get(pk=self.market.pk).issued_quantity
        )
        self.assertFalse(
            get_user_model()
            .objects.filter(email=settings.MARKETX_HOUSE_EMAIL)
            .exists()
        )

        self.buy(self.buyer, self.yes, 4_000, 1, "buy-one")
        with self.assertRaises(InsufficientPosition):
            self.sell(self.buyer, self.yes, 5_000, 2, "oversell")

    def test_user_to_user_trading_after_issuance(self):
        self.buy(self.buyer, self.yes, 4_000, 5, "seed-buy")
        house_wallet_before = self.house_wallet()

        self.sell(self.buyer, self.yes, 6_000, 5, "user-ask")
        self.buy(self.buyer2, self.yes, 6_000, 5, "user-bid")

        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 5)
        trade = Trade.objects.order_by("-id").first()
        self.assertEqual(trade.buyer_id, self.buyer2.id)
        self.assertEqual(trade.seller_id, self.buyer.id)
        self.assertEqual(self.wallet(self.buyer).available_balance, 110_000)
        self.assertEqual(self.wallet(self.buyer2).available_balance, 70_000)

        house_wallet_after = self.house_wallet()
        self.assertEqual(
            house_wallet_after.available_balance,
            house_wallet_before.available_balance,
        )
        self.assertEqual(
            house_wallet_after.reserved_balance,
            house_wallet_before.reserved_balance,
        )

    def test_void_market_refunds_cost_basis_from_collateral(self):
        self.buy(self.buyer, self.yes, 2_500, 4, "void-buy")

        resolve_market(
            market_id=self.market.id,
            is_void=True,
            resolved_by=self.admin,
            evidence_summary="Event cancelled.",
        )

        self.assertEqual(self.wallet(self.buyer).available_balance, 100_000)
        self.assertEqual(
            self.house_wallet().available_balance, settings.MARKETX_HOUSE_CREDITS
        )
        self.assertEqual(self.house_wallet().reserved_balance, 0)
        self.assertEqual(
            Settlement.objects.aggregate(total=Sum("payout"))["total"],
            CREDIT_SCALE * 4,
        )

    def test_reconciliation_passes_after_issuance_and_settlement(self):
        self.buy(self.buyer, self.yes, 4_000, 2, "reconcile-buy-yes")
        self.buy(self.buyer2, self.no, 5_000, 1, "reconcile-buy-no")
        resolve_market(
            market_id=self.market.id,
            winning_outcome_id=self.no.id,
            resolved_by=self.admin,
            evidence_summary="No won.",
        )

        output = StringIO()
        call_command("reconcile_marketx", stdout=output)
        self.assertIn("No discrepancies found.", output.getvalue())
