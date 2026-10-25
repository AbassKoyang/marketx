from django.urls import reverse
from rest_framework.test import APIClient

from apps.markets.models import Market, MarketOutcome
from apps.trading.constants import CREDIT_SCALE, MAX_PRICE, MIN_PRICE
from apps.trading.models import Order, Trade
from apps.trading.pricing import buy_cost, lmsr_prices
from .base import AmmTestBase


class TradingApiTests(AmmTestBase):
    def setUp(self):
        self.api = APIClient()

    # ------------------------------------------------------------------
    # market pricing
    # ------------------------------------------------------------------
    def test_market_detail_exposes_a_unified_price_vector(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "api-detail-buy")

        response = self.api.get(f"/api/markets/{self.market.slug}/")

        self.assertEqual(response.status_code, 200)
        outcomes = response.data["outcomes"]
        self.assertEqual(len(outcomes), 2)
        ticks = [outcome["current_price"] for outcome in outcomes]
        self.assertEqual(sum(ticks), CREDIT_SCALE)
        net_shares = [
            outcome.net_shares
            for outcome in MarketOutcome.objects.filter(
                market=self.market, is_active=True
            ).order_by("display_order", "id")
        ]
        self.assertEqual(
            ticks,
            lmsr_prices(net_shares, self.market.liquidity_b),
        )
        current = response.data["current_prices"]
        self.assertEqual(len(current), 2)
        self.assertEqual(sum(row["price"] for row in current), CREDIT_SCALE)
        self.assertEqual(
            sum(row["price_decimal"] for row in current),
            1.0,
        )

    def test_market_stats_prices_sum_to_one(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "api-stats-buy")

        response = self.api.get(f"/api/markets/{self.market.slug}/stats/")

        self.assertEqual(response.status_code, 200)
        prices = response.data["prices"]
        self.assertEqual(set(prices), {"yes", "no"})
        self.assertAlmostEqual(sum(prices.values()), 1.0, places=6)
        self.assertEqual(response.data["liquidity_b"], self.market.liquidity_b)
        self.assertEqual(response.data["total_volume"], 10)

    def test_resolved_market_prices_are_certain(self):
        from apps.trading.services import resolve_market

        self.buy(self.buyer, self.yes, 6_000, 10, "api-resolve-buy")
        resolve_market(
            market_id=self.market.id,
            winning_outcome_id=self.yes.id,
            resolved_by=self.admin,
            evidence_summary="Yes won.",
        )

        detail = self.api.get(f"/api/markets/{self.market.slug}/")
        self.assertEqual(detail.status_code, 200)
        ticks = [outcome["current_price"] for outcome in detail.data["outcomes"]]
        self.assertEqual(ticks, [CREDIT_SCALE, 0])
        self.assertEqual(sum(ticks), CREDIT_SCALE)

        stats = self.api.get(f"/api/markets/{self.market.slug}/stats/")
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.data["prices"], {"yes": 1.0, "no": 0.0})

    # ------------------------------------------------------------------
    # order submission
    # ------------------------------------------------------------------
    def test_market_order_without_limit_price_fills(self):
        self.api.force_authenticate(self.buyer)

        response = self.api.post(
            "/api/trading/orders/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.BUY,
                "quantity": 5,
                "client_order_id": "api-market-buy",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Order.Status.FILLED)
        self.assertEqual(response.data["filled_quantity"], 5)
        self.assertEqual(response.data["limit_price"], MAX_PRICE)
        trade = Trade.objects.get()
        self.assertEqual(trade.notional, buy_cost([0, 0], 0, 5, 1_000))

    def test_sell_without_limit_price_defaults_to_min_price(self):
        self.buy(self.buyer, self.yes, 6_000, 5, "api-sell-seed")
        self.api.force_authenticate(self.buyer)

        response = self.api.post(
            "/api/trading/orders/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.SELL,
                "quantity": 2,
                "client_order_id": "api-market-sell",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Order.Status.FILLED)
        self.assertEqual(response.data["limit_price"], MIN_PRICE)

    def test_below_market_limit_is_rejected_with_400(self):
        self.api.force_authenticate(self.buyer)

        response = self.api.post(
            "/api/trading/orders/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.BUY,
                "limit_price": 4_000,
                "quantity": 1,
                "client_order_id": "api-below-market",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.data)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(Trade.objects.exists())

    def test_duplicate_client_order_id_returns_the_original_order(self):
        self.api.force_authenticate(self.buyer)
        payload = {
            "outcome": self.yes.id,
            "side": Order.Side.BUY,
            "quantity": 3,
            "client_order_id": "api-idempotent",
        }

        first = self.api.post("/api/trading/orders/", payload, format="json")
        second = self.api.post("/api/trading/orders/", payload, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(Order.objects.filter(user=self.buyer).count(), 1)
        self.assertEqual(Trade.objects.count(), 1)

    def test_reused_client_order_id_with_different_terms_is_rejected(self):
        self.api.force_authenticate(self.buyer)
        self.api.post(
            "/api/trading/orders/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.BUY,
                "quantity": 3,
                "client_order_id": "api-reused",
            },
            format="json",
        )

        response = self.api.post(
            "/api/trading/orders/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.BUY,
                "quantity": 4,
                "client_order_id": "api-reused",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.data)

    def test_closed_market_rejects_orders(self):
        self.api.force_authenticate(self.buyer)
        market = Market.objects.get(pk=self.market.pk)
        market.status = Market.Status.CLOSED
        market.save(update_fields=["status"])

        response = self.api.post(
            "/api/trading/orders/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.BUY,
                "quantity": 1,
                "client_order_id": "api-closed-market",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("detail", response.data)

    def test_cancel_unknown_order_returns_404(self):
        self.api.force_authenticate(self.buyer)

        response = self.api.delete("/api/trading/orders/999999/")

        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.data)

    # ------------------------------------------------------------------
    # quotes
    # ------------------------------------------------------------------
    def test_quote_endpoint_matches_execution(self):
        self.api.force_authenticate(self.buyer)

        response = self.api.get(
            "/api/trading/quote/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.BUY,
                "quantity": 10,
            },
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["fillable_quantity"], 10)
        self.assertEqual(
            response.data["notional"], buy_cost([0, 0], 0, 10, 1_000)
        )
        self.assertEqual(sum(response.data["prices_after"].values()), CREDIT_SCALE)
        self.assertIsNone(response.data["limited_by"])

        order = self.api.post(
            "/api/trading/orders/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.BUY,
                "quantity": 10,
                "client_order_id": "api-quoted-buy",
            },
            format="json",
        )
        self.assertEqual(order.status_code, 201)
        self.assertEqual(order.data["filled_quantity"], response.data["fillable_quantity"])
        self.assertEqual(Trade.objects.get().notional, response.data["notional"])

    def test_quote_reports_limit_and_position_constraints(self):
        self.buy(self.buyer, self.yes, 6_000, 4, "api-quote-seed")
        self.api.force_authenticate(self.buyer)

        limited = self.api.get(
            "/api/trading/quote/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.BUY,
                "quantity": 1,
                "limit_price": 4_000,
            },
        )
        self.assertEqual(limited.status_code, 200)
        self.assertEqual(limited.data["fillable_quantity"], 0)
        self.assertEqual(limited.data["limited_by"], "limit")
        self.assertIsNone(limited.data["average_price"])

        over_position = self.api.get(
            "/api/trading/quote/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.SELL,
                "quantity": 10,
            },
        )
        self.assertEqual(over_position.status_code, 200)
        self.assertEqual(over_position.data["fillable_quantity"], 4)
        self.assertEqual(over_position.data["limited_by"], "position")

    def test_quote_requires_authentication(self):
        response = self.api.get(
            "/api/trading/quote/",
            {
                "outcome": self.yes.id,
                "side": Order.Side.BUY,
                "quantity": 1,
            },
        )

        self.assertEqual(response.status_code, 401)

    # ------------------------------------------------------------------
    # positions
    # ------------------------------------------------------------------
    def test_positions_are_valued_at_the_current_amm_price(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "api-position-buy")
        self.api.force_authenticate(self.buyer)

        response = self.api.get("/api/trading/positions/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        position = response.data["results"][0]
        expected_tick = lmsr_prices([10, 0], 1_000)[0]
        self.assertEqual(position["quantity"], 10)
        self.assertEqual(position["current_price"], expected_tick)
        self.assertEqual(position["last_price"], expected_tick)
        self.assertAlmostEqual(position["current_price_decimal"], expected_tick / CREDIT_SCALE)
        self.assertEqual(position["market_value"], 10 * expected_tick)
        cost_basis = Trade.objects.get().notional
        self.assertEqual(position["unrealized_pnl"], 10 * expected_tick - cost_basis)

    def test_position_summary_aggregates_open_positions(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "api-summary-buy")
        self.api.force_authenticate(self.buyer)

        response = self.api.get("/api/trading/positions/summary/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["open_positions"], 1)
        self.assertEqual(response.data["total_cost_basis"], Trade.objects.get().notional)
        self.assertEqual(response.data["realized_pnl"], 0)

    # ------------------------------------------------------------------
    # order book depth
    # ------------------------------------------------------------------
    def test_order_book_depth_reflects_the_amm(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "api-depth-buy")

        response = self.api.get(f"/api/trading/order-books/{self.yes.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["current_price"],
            lmsr_prices([10, 0], 1_000)[0],
        )
        self.assertTrue(response.data["asks"])
        self.assertTrue(response.data["bids"])

    def test_order_book_rejects_a_bad_limit(self):
        response = self.api.get(
            f"/api/trading/order-books/{self.yes.id}/",
            {"limit": "not-a-number"},
        )

        self.assertEqual(response.status_code, 400)

    def test_order_book_returns_404_for_unknown_outcome(self):
        response = self.api.get("/api/trading/order-books/999999/")

        self.assertEqual(response.status_code, 404)
        self.assertIn("detail", response.data)
