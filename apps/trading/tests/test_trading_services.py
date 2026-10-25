from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import Sum

from apps.markets.models import Market, MarketOutcome
from apps.trading.constants import CREDIT_SCALE
from apps.trading.models import Order, Position, Settlement, Trade
from apps.trading.pricing import buy_cost, lmsr_prices, sell_refund
from apps.trading.services import (
    TradingError,
    cancel_order,
    close_market,
    get_order_book_depth,
    quote_order,
    resolve_market,
)
from apps.wallet.models import Wallet
from apps.wallet.services import get_or_create_wallet, reserve_credits
from .base import AmmTestBase


class AmmTradingTests(AmmTestBase):
    def test_sell_back_to_market_maker_moves_price_down(self):
        buy_order = self.buy(self.buyer, self.yes, 6_000, 10, "buy-ten")
        buy_notional = Trade.objects.get().notional
        price_after_buy = self.prices()[0]

        sell_order = self.sell(self.buyer, self.yes, 4_000, 4, "sell-four")

        expected_refund = sell_refund([10, 0], 0, 4, 1_000)
        trade = Trade.objects.order_by("-id").first()
        self.assertEqual(sell_order.status, Order.Status.FILLED)
        self.assertEqual(sell_order.filled_quantity, 4)
        self.assertEqual(trade.buyer_id, self.house().id)
        self.assertEqual(trade.seller_id, self.buyer.id)
        self.assertEqual(trade.notional, expected_refund)

        position = self.position(self.buyer, self.yes)
        self.assertEqual(position.quantity, 6)
        self.assertEqual(
            position.cost_basis,
            buy_notional - (buy_notional * 4 // 10),
        )
        self.assertEqual(
            self.wallet(self.buyer).available_balance,
            100_000 - buy_notional + expected_refund,
        )

        # The market maker holds the repurchased shares and no new sets were
        # minted, so issued quantity is unchanged.
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 10)
        self.assertEqual(self.position(self.house(), self.yes).quantity, 4)
        self.assertEqual(self.position(self.house(), self.no).quantity, 10)
        self.assertEqual(MarketOutcome.objects.get(pk=self.yes.pk).net_shares, 6)

        ticks = self.assert_prices_sum()
        self.assertGreater(ticks[0], 5_000)
        self.assertLess(ticks[0], price_after_buy)

    def test_sell_rejects_a_limit_above_the_market_price(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "buy-ten")

        with self.assertRaises(TradingError):
            self.sell(self.buyer, self.yes, 6_000, 1, "greedy-sell")

        self.assertEqual(self.position(self.buyer, self.yes).quantity, 10)
        self.assertEqual(Trade.objects.count(), 1)

    def test_winning_settlement_pays_contract_value_once(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "settle-yes")
        self.buy(self.buyer2, self.no, 6_000, 5, "settle-no")
        yes_notional = Trade.objects.get(outcome=self.yes).notional
        no_notional = Trade.objects.get(outcome=self.no).notional

        resolve_market(
            market_id=self.market.id,
            winning_outcome_id=self.yes.id,
            resolved_by=self.admin,
            evidence_summary="Yes won.",
        )

        self.assertEqual(
            self.wallet(self.buyer).available_balance,
            100_000 - yes_notional + 10 * CREDIT_SCALE,
        )
        self.assertEqual(
            self.wallet(self.buyer2).available_balance,
            100_000 - no_notional,
        )
        self.assertEqual(self.house_wallet().reserved_balance, 0)
        self.assertEqual(
            Settlement.objects.get(position__user=self.buyer, position__outcome=self.yes).payout,
            10 * CREDIT_SCALE,
        )
        self.assertEqual(
            Settlement.objects.get(position__user=self.buyer2, position__outcome=self.no).payout,
            0,
        )
        self.assertEqual(Market.objects.get(pk=self.market.pk).issued_quantity, 10)

    def test_void_market_refunds_cost_basis(self):
        self.buy(self.buyer, self.yes, 6_000, 4, "void-yes")
        self.buy(self.buyer2, self.no, 6_000, 2, "void-no")
        expected_refund = (
            Position.objects.aggregate(total=Sum("cost_basis"))["total"] or 0
        )

        resolve_market(
            market_id=self.market.id,
            is_void=True,
            resolved_by=self.admin,
            evidence_summary="Event cancelled.",
        )

        self.assertEqual(self.wallet(self.buyer).available_balance, 100_000)
        self.assertEqual(self.wallet(self.buyer2).available_balance, 100_000)
        self.assertEqual(self.house_wallet().reserved_balance, 0)
        self.assertEqual(
            Settlement.objects.aggregate(total=Sum("payout"))["total"],
            expected_refund,
        )

    def test_close_market_cancels_legacy_open_orders_and_releases_reservations(self):
        reserve_credits(
            user_id=self.buyer.id,
            amount=3_000 * 2,
            idempotency_key="legacy-close-reserve",
            reference_type="order",
            reference_id="legacy-close",
        )
        order = Order.objects.create(
            user=self.buyer,
            outcome=self.yes,
            side=Order.Side.BUY,
            limit_price=3_000,
            original_quantity=2,
            client_order_id="legacy-close",
        )

        close_market(market_id=self.market.id)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        wallet = self.wallet(self.buyer)
        self.assertEqual(wallet.reserved_balance, 0)
        self.assertEqual(wallet.available_balance, 100_000)

    def test_cancel_order_releases_legacy_reservation(self):
        reserve_credits(
            user_id=self.buyer.id,
            amount=3_000 * 2,
            idempotency_key="legacy-cancel-reserve",
            reference_type="order",
            reference_id="legacy-cancel",
        )
        order = Order.objects.create(
            user=self.buyer,
            outcome=self.yes,
            side=Order.Side.BUY,
            limit_price=3_000,
            original_quantity=2,
            client_order_id="legacy-cancel",
        )

        cancel_order(user=self.buyer, order_id=order.id)

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        wallet = self.wallet(self.buyer)
        self.assertEqual(wallet.reserved_balance, 0)
        self.assertEqual(wallet.available_balance, 100_000)

    def test_quote_matches_execution(self):
        quote = quote_order(
            user=self.buyer,
            outcome_id=self.yes.id,
            side=Order.Side.BUY,
            quantity=10,
            limit_price=6_000,
        )
        self.assertEqual(quote["fillable_quantity"], 10)
        self.assertEqual(quote["notional"], buy_cost([0, 0], 0, 10, 1_000))
        self.assertEqual(
            sum(quote["prices_after"].values()), CREDIT_SCALE
        )

        order = self.buy(self.buyer, self.yes, 6_000, 10, "quoted-buy")
        trade = Trade.objects.get()
        self.assertEqual(order.filled_quantity, quote["fillable_quantity"])
        self.assertEqual(trade.notional, quote["notional"])

        sell_quote = quote_order(
            user=self.buyer,
            outcome_id=self.yes.id,
            side=Order.Side.SELL,
            quantity=4,
            limit_price=4_000,
        )
        self.assertEqual(sell_quote["fillable_quantity"], 4)
        self.assertEqual(
            sell_quote["notional"], sell_refund([10, 0], 0, 4, 1_000)
        )

    def test_quote_reports_a_limit_rejection(self):
        quote = quote_order(
            user=self.buyer,
            outcome_id=self.yes.id,
            side=Order.Side.BUY,
            quantity=1,
            limit_price=4_000,
        )
        self.assertEqual(quote["fillable_quantity"], 0)
        self.assertEqual(quote["limited_by"], "limit")
        self.assertIsNone(quote["average_price"])

    def test_order_book_depth_uses_amm_quotes(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "depth-buy")

        depth = get_order_book_depth(outcome_id=self.yes.id, limit=5)
        self.assertEqual(
            depth["current_price"],
            lmsr_prices([10, 0], 1_000)[0],
        )
        self.assertTrue(depth["asks"])
        self.assertTrue(depth["bids"])
        for row in depth["asks"] + depth["bids"]:
            self.assertGreaterEqual(row["price"], 1)
            self.assertLessEqual(row["price"], CREDIT_SCALE - 1)
            self.assertGreater(row["quantity"], 0)

        close_market(market_id=self.market.id)
        closed_depth = get_order_book_depth(outcome_id=self.yes.id, limit=5)
        self.assertEqual(closed_depth["asks"], [])
        self.assertEqual(closed_depth["bids"], [])

    def test_insufficient_balance_caps_the_buy(self):
        User = get_user_model()
        poor = User.objects.create_user(
            email="poor@example.com",
            password="password",
            first_name="Poor",
            last_name="Buyer",
            username="poor-buyer",
        )
        get_or_create_wallet(poor, 10_000)

        order = self.buy(poor, self.yes, 6_000, 10, "poor-buy")

        expected_fill = 1
        expected_cost = buy_cost([0, 0], 0, expected_fill, 1_000)
        self.assertEqual(order.status, Order.Status.PARTIALLY_FILLED)
        self.assertEqual(order.filled_quantity, expected_fill)
        self.assertIsNotNone(order.cancelled_at)
        self.assertEqual(
            self.wallet(poor).available_balance,
            10_000 - expected_cost,
        )
        self.assertEqual(self.position(poor, self.yes).quantity, expected_fill)

    def test_market_maker_will_not_buy_back_shares_it_did_not_sell(self):
        Position.objects.create(
            user=self.seller,
            outcome=self.yes,
            quantity=5,
            cost_basis=25_000,
        )

        with self.assertRaises(TradingError):
            self.sell(self.seller, self.yes, 1, 1, "legacy-sell")

        self.assertEqual(Trade.objects.count(), 0)
        self.assertEqual(self.position(self.seller, self.yes).quantity, 5)

    def test_reconciliation_passes_after_amm_activity(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "reconcile-buy")
        self.sell(self.buyer, self.yes, 4_000, 4, "reconcile-sell")
        self.buy(self.buyer2, self.no, 6_000, 3, "reconcile-buy-no")

        output = StringIO()
        call_command("reconcile_marketx", stdout=output)
        self.assertIn("No discrepancies found.", output.getvalue())
