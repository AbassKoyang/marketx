from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from apps.markets.models import Market, MarketOutcome, MarketPricePoint
from apps.trading.constants import CREDIT_SCALE
from apps.trading.models import Order, Trade
from apps.trading.pricing import average_tick, buy_cost
from apps.trading.services import TradingError
from .base import AmmTestBase


class IssuanceTests(AmmTestBase):
    def test_first_buy_mints_complete_sets_and_moves_prices(self):
        self.assertEqual(Trade.objects.count(), 0)
        self.assertEqual(self.prices(), [5_000, 5_000])

        order = self.buy(self.buyer, self.yes, 6_000, 10, "first-buy")

        self.assertEqual(order.status, Order.Status.FILLED)
        self.assertEqual(order.filled_quantity, 10)

        trade = Trade.objects.get()
        expected_notional = buy_cost([0, 0], 0, 10, 1_000)
        self.assertEqual(trade.buyer_id, self.buyer.id)
        self.assertEqual(trade.seller_id, self.house().id)
        self.assertEqual(trade.notional, expected_notional)
        self.assertEqual(trade.price, average_tick(expected_notional, 10, side="buy"))

        buyer_wallet = self.wallet(self.buyer)
        self.assertEqual(buyer_wallet.available_balance, 100_000 - expected_notional)
        self.assertEqual(buyer_wallet.reserved_balance, 0)

        buyer_position = self.position(self.buyer, self.yes)
        self.assertEqual(buyer_position.quantity, 10)
        self.assertEqual(buyer_position.cost_basis, expected_notional)

        market = Market.objects.get(pk=self.market.pk)
        self.assertEqual(market.issued_quantity, 10)

        # The market maker sold YES and keeps the complementary NO shares.
        self.assertEqual(self.position(self.house(), self.yes).quantity, 0)
        self.assertEqual(self.position(self.house(), self.no).quantity, 10)
        self.assertEqual(
            MarketOutcome.objects.get(pk=self.yes.pk).net_shares, 10
        )
        self.assertEqual(
            MarketOutcome.objects.get(pk=self.no.pk).net_shares, 0
        )

        house_wallet = self.house_wallet()
        self.assertEqual(house_wallet.reserved_balance, CREDIT_SCALE * 10)
        self.assertEqual(
            house_wallet.available_balance,
            settings.MARKETX_HOUSE_CREDITS - CREDIT_SCALE * 10 + expected_notional,
        )

        ticks = self.assert_prices_sum()
        self.assertGreater(ticks[0], 5_000)
        self.assertLess(ticks[1], 5_000)

    def test_first_buy_on_non_primary_outcome(self):
        order = self.buy(self.buyer, self.no, 6_000, 3, "first-no-buy")

        self.assertEqual(order.status, Order.Status.FILLED)
        house = self.house()
        self.assertEqual(self.position(house, self.no).quantity, 0)
        self.assertEqual(self.position(house, self.yes).quantity, 3)
        self.assertEqual(
            Market.objects.get(pk=self.market.pk).issued_quantity, 3
        )
        self.assertEqual(self.house_wallet().reserved_balance, CREDIT_SCALE * 3)
        ticks = self.assert_prices_sum()
        self.assertGreater(ticks[1], 5_000)

    def test_second_outcome_buy_uses_house_inventory_before_minting(self):
        self.buy(self.buyer, self.yes, 6_000, 5, "first-buy")
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 5)

        second = self.buy(self.buyer2, self.no, 6_000, 5, "second-outcome-buy")

        # No new sets were minted: the market maker already held five NO shares.
        market = Market.objects.get(pk=self.market.pk)
        self.assertEqual(market.issued_quantity, 5)
        self.assertEqual(second.status, Order.Status.FILLED)
        self.assertEqual(self.position(self.house(), self.no).quantity, 0)
        self.assertEqual(self.position(self.buyer2, self.no).quantity, 5)

        expected_notional = buy_cost([5, 0], 1, 5, 1_000)
        trade = Trade.objects.order_by("-id").first()
        self.assertEqual(trade.notional, expected_notional)
        self.assertEqual(
            self.wallet(self.buyer2).available_balance,
            100_000 - expected_notional,
        )

        ticks = self.assert_prices_sum()
        self.assertEqual(ticks, self.prices())

    def test_multi_outcome_issuance_mints_one_share_of_every_outcome(self):
        self.buy(self.buyer, self.alpha, 6_000, 10, "multi-buy")

        market = Market.objects.get(pk=self.multi_market.pk)
        self.assertEqual(market.issued_quantity, 10)
        house = self.house()
        self.assertEqual(self.position(house, self.alpha).quantity, 0)
        self.assertEqual(self.position(house, self.beta).quantity, 10)
        self.assertEqual(self.position(house, self.gamma).quantity, 10)
        self.assertEqual(
            self.house_wallet().reserved_balance, CREDIT_SCALE * 10
        )

        ticks = self.assert_prices_sum(self.multi_market)
        self.assertGreater(ticks[0], ticks[1])
        self.assertGreater(ticks[0], ticks[2])

    def test_issuance_cap_partially_fills_and_cancels_remainder(self):
        market = Market.objects.get(pk=self.market.pk)
        market.issuance_limit = 3
        market.save(update_fields=["issuance_limit"])

        order = self.buy(self.buyer, self.yes, 6_000, 5, "capped-buy")

        self.assertEqual(order.status, Order.Status.PARTIALLY_FILLED)
        self.assertEqual(order.filled_quantity, 3)
        self.assertEqual(order.remaining_quantity, 2)
        self.assertIsNotNone(order.cancelled_at)
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 3)
        self.assertEqual(
            self.wallet(self.buyer).available_balance,
            100_000 - buy_cost([0, 0], 0, 3, 1_000),
        )

        # No YES inventory and no issuance capacity remain.
        with self.assertRaises(TradingError):
            self.buy(self.buyer2, self.yes, 6_000, 1, "beyond-cap-buy")

        # The market maker does hold NO inventory, so a NO buy still fills.
        no_order = self.buy(self.buyer2, self.no, 6_000, 1, "inventory-buy")
        self.assertEqual(no_order.status, Order.Status.FILLED)
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 3)

    @override_settings(MARKETX_HOUSE_CREDITS=20_000)
    def test_issuance_is_limited_by_house_collateral(self):
        order = self.buy(self.buyer, self.yes, 6_000, 5, "underfunded-buy")

        self.assertEqual(order.status, Order.Status.PARTIALLY_FILLED)
        self.assertEqual(order.filled_quantity, 2)
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 2)
        house_wallet = self.house_wallet()
        self.assertEqual(house_wallet.reserved_balance, 20_000)
        self.assertEqual(
            house_wallet.available_balance,
            buy_cost([0, 0], 0, 2, 1_000),
        )

    def test_outcome_cannot_be_deactivated_after_issuance(self):
        from apps.markets.serializers import MarketOutcomeSerializer

        self.buy(self.buyer, self.yes, 6_000, 1, "issued-buy")

        serializer = MarketOutcomeSerializer(
            self.no, data={"is_active": False}, partial=True
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

        outcome = MarketOutcome.objects.get(pk=self.no.pk)
        outcome.is_active = False
        with self.assertRaises(ValidationError):
            outcome.full_clean()

    def test_naked_selling_is_rejected_and_oversell_is_capped(self):
        with self.assertRaises(TradingError):
            self.sell(self.buyer, self.yes, 1, 1, "naked-sell")

        self.assertEqual(Trade.objects.count(), 0)
        self.assertFalse(
            Market.objects.get(pk=self.market.pk).issued_quantity
        )
        self.assertFalse(
            get_user_model()
            .objects.filter(email=settings.MARKETX_HOUSE_EMAIL)
            .exists()
        )

        self.buy(self.buyer, self.yes, 6_000, 1, "buy-one")
        order = self.sell(self.buyer, self.yes, 4_000, 2, "oversell")

        self.assertEqual(order.status, Order.Status.PARTIALLY_FILLED)
        self.assertEqual(order.filled_quantity, 1)
        self.assertIsNotNone(order.cancelled_at)
        self.assertEqual(self.position(self.buyer, self.yes).quantity, 0)
        self.assertEqual(self.position(self.buyer, self.yes).reserved_quantity, 0)

    def test_price_history_records_the_full_vector(self):
        fixed_now = timezone.now()
        with patch("django.utils.timezone.now", return_value=fixed_now):
            self.buy(self.buyer, self.yes, 6_000, 10, "history-buy-yes")
            self.buy(self.buyer2, self.no, 6_000, 5, "history-buy-no")

        bucket = fixed_now.replace(second=0, microsecond=0)
        points = MarketPricePoint.objects.filter(timestamp=bucket).order_by(
            "outcome__display_order"
        )
        self.assertEqual(points.count(), 2)
        self.assertEqual(sum(point.price for point in points), CREDIT_SCALE)
        by_outcome = {point.outcome_id: point for point in points}
        self.assertEqual(by_outcome[self.yes.id].volume, 10)
        self.assertEqual(by_outcome[self.no.id].volume, 5)
        self.assertGreater(by_outcome[self.yes.id].price, 5_000)
