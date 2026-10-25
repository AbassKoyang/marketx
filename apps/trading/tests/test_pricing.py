from django.test import SimpleTestCase

from apps.trading.constants import CREDIT_SCALE, MAX_PRICE, MIN_PRICE
from apps.trading.pricing import (
    average_tick,
    buy_cost,
    lmsr_prices,
    max_buy_quantity,
    max_sell_quantity,
    sell_refund,
)


class LmsrPricingTests(SimpleTestCase):
    def test_initial_binary_prices_are_uniform(self):
        self.assertEqual(lmsr_prices([0, 0], 1_000), [5_000, 5_000])

    def test_initial_multi_outcome_prices_sum_to_one(self):
        ticks = lmsr_prices([0, 0, 0], 1_000)
        self.assertEqual(sum(ticks), CREDIT_SCALE)
        self.assertLessEqual(max(ticks) - min(ticks), 1)

    def test_prices_sum_to_one_for_many_states(self):
        states = [
            [0, 0],
            [1, 0],
            [10, 0],
            [0, 10],
            [100, 50],
            [10_000, 0],
            [0, 10_000],
            [5_000, 5_000],
            [3, 7, 11],
            [0, 0, 10_000],
            [10_000, 10_000, 10_000],
        ]
        for state in states:
            with self.subTest(state=state):
                ticks = lmsr_prices(state, 1_000)
                self.assertEqual(sum(ticks), CREDIT_SCALE)
                self.assertTrue(all(MIN_PRICE <= tick <= MAX_PRICE for tick in ticks))

    def test_buying_moves_price_up_and_selling_moves_it_down(self):
        initial = lmsr_prices([0, 0], 1_000)
        after_buy = lmsr_prices([20, 0], 1_000)
        after_sell = lmsr_prices([5, 0], 1_000)
        self.assertGreater(after_buy[0], initial[0])
        self.assertLess(after_buy[1], initial[1])
        self.assertGreater(after_sell[0], initial[0])
        self.assertLess(after_sell[0], after_buy[0])

    def test_buy_cost_and_refund_are_inverse_with_a_one_unit_spread(self):
        state = [10, 0]
        cost = buy_cost([0, 0], 0, 10, 1_000)
        refund = sell_refund(state, 0, 10, 1_000)
        self.assertGreater(cost, 0)
        self.assertGreater(refund, 0)
        self.assertLessEqual(refund, cost)
        self.assertLessEqual(cost - refund, 1)

    def test_larger_trades_cost_more_per_share(self):
        one = buy_cost([0, 0], 0, 1, 1_000)
        ten = buy_cost([0, 0], 0, 10, 1_000)
        hundred = buy_cost([0, 0], 0, 100, 1_000)
        self.assertGreater(ten / 10, one)
        self.assertGreater(hundred / 100, ten / 10)

    def test_max_buy_quantity_respects_slippage_and_balance(self):
        self.assertEqual(
            max_buy_quantity(
                [0, 0], 0, 1_000, limit_price=5_000, max_quantity=100
            ),
            0,
        )
        fill = max_buy_quantity(
            [0, 0], 0, 1_000, limit_price=5_100, max_quantity=100
        )
        self.assertGreater(fill, 0)
        self.assertLess(fill, 100)
        self.assertLessEqual(buy_cost([0, 0], 0, fill, 1_000), 5_100 * fill)

        affordable = max_buy_quantity(
            [0, 0],
            0,
            1_000,
            limit_price=MAX_PRICE,
            max_quantity=100,
            max_cost=20_000,
        )
        self.assertGreater(affordable, 0)
        self.assertLessEqual(
            buy_cost([0, 0], 0, affordable, 1_000), 20_000
        )

    def test_max_sell_quantity_respects_slippage_and_refund_cap(self):
        state = [20, 0]
        fill = max_sell_quantity(
            state, 0, 1_000, limit_price=4_900, max_quantity=20
        )
        self.assertGreater(fill, 0)
        self.assertLessEqual(fill, 20)
        self.assertGreaterEqual(
            sell_refund(state, 0, fill, 1_000), 4_900 * fill
        )

        capped = max_sell_quantity(
            state,
            0,
            1_000,
            limit_price=MIN_PRICE,
            max_quantity=20,
            max_refund=10_000,
        )
        self.assertGreaterEqual(capped, 0)
        self.assertLessEqual(sell_refund(state, 0, capped, 1_000), 10_000)

    def test_extreme_probabilities_are_clamped_and_still_sum(self):
        ticks = lmsr_prices([10_000, 0], 1_000)
        self.assertEqual(ticks, [MAX_PRICE, MIN_PRICE])
        self.assertEqual(sum(ticks), CREDIT_SCALE)

    def test_average_tick_rounds_in_the_traders_favour(self):
        self.assertEqual(average_tick(10_001, 2, side="buy"), 5_000)
        self.assertEqual(average_tick(10_001, 2, side="sell"), 5_001)
        self.assertEqual(average_tick(1, 1, side="buy"), MIN_PRICE)
        self.assertEqual(average_tick(CREDIT_SCALE, 1, side="sell"), MAX_PRICE)

    def test_invalid_liquidity_parameter_is_rejected(self):
        with self.assertRaises(ValueError):
            lmsr_prices([0, 0], 0)
