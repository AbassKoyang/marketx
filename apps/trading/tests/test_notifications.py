from unittest.mock import patch

from apps.markets.models import Market, MarketOutcome
from apps.notifications.models import Notification, UserDevice
from apps.trading.constants import CREDIT_SCALE
from apps.trading.models import Order, Position
from apps.trading.services import cancel_order, close_market, resolve_market
from .base import AmmTestBase


class TradingNotificationTests(AmmTestBase):
    def test_buy_creates_an_order_filled_notification(self):
        order = self.buy(self.buyer, self.yes, 6_000, 10, "notify-buy")

        notification = Notification.objects.get(
            user=self.buyer,
            event_type=Notification.EventType.ORDER_FILLED,
        )
        self.assertEqual(notification.data["order_id"], order.id)
        self.assertEqual(notification.data["trade_id"], order.buy_trades.get().id)
        self.assertEqual(notification.data["side"], Order.Side.BUY)
        self.assertEqual(notification.data["quantity"], 10)
        self.assertEqual(notification.data["original_quantity"], 10)
        self.assertFalse(notification.data["partial"])
        self.assertEqual(notification.data["market_id"], self.market.id)
        self.assertEqual(notification.data["outcome_id"], self.yes.id)
        self.assertIn("Order filled", notification.title)

    def test_sell_creates_an_order_filled_notification(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "notify-sell-seed")
        Notification.objects.all().delete()

        order = self.sell(self.buyer, self.yes, 4_000, 5, "notify-sell")

        notification = Notification.objects.get(
            user=self.buyer,
            event_type=Notification.EventType.ORDER_FILLED,
        )
        self.assertEqual(notification.data["order_id"], order.id)
        self.assertEqual(notification.data["side"], Order.Side.SELL)
        self.assertEqual(notification.data["quantity"], 5)
        self.assertFalse(notification.data["partial"])

    def test_partially_filled_order_notification_reports_the_cancelled_remainder(self):
        market = self._create_market(
            title="Tiny issuance market",
            market_type=Market.MarketType.BINARY,
            names=["Tiny Yes", "Tiny No"],
            issuance_limit=5,
        )
        outcome = MarketOutcome.objects.filter(market=market).order_by(
            "display_order", "id"
        ).first()

        order = self.buy(self.buyer, outcome, 9_000, 10, "notify-partial")

        self.assertEqual(order.status, Order.Status.PARTIALLY_FILLED)
        notification = Notification.objects.get(
            user=self.buyer,
            event_type=Notification.EventType.ORDER_FILLED,
        )
        self.assertTrue(notification.data["partial"])
        self.assertEqual(notification.data["quantity"], 5)
        self.assertEqual(notification.data["original_quantity"], 10)
        self.assertEqual(notification.title, "Order partially filled")
        self.assertIn("5 unfilled shares were cancelled", notification.body)

    def test_cancelling_an_order_creates_a_notification(self):
        Position.objects.create(
            user=self.seller,
            outcome=self.yes,
            quantity=10,
            reserved_quantity=5,
        )
        order = Order.objects.create(
            user=self.seller,
            outcome=self.yes,
            side=Order.Side.SELL,
            limit_price=5_000,
            original_quantity=5,
            client_order_id="notify-open-sell",
        )

        cancel_order(user=self.seller, order_id=order.id)

        notification = Notification.objects.get(
            user=self.seller,
            event_type=Notification.EventType.ORDER_CANCELLED,
        )
        self.assertEqual(notification.data["order_id"], order.id)
        self.assertEqual(notification.data["released_quantity"], 5)
        self.assertIn("5 shares were released", notification.body)

    def test_close_market_notifies_position_holders_and_cancels_open_orders(self):
        self.buy(self.buyer, self.alpha, 6_000, 10, "notify-close-buy")
        Position.objects.create(
            user=self.seller,
            outcome=self.beta,
            quantity=10,
            reserved_quantity=5,
        )
        Order.objects.create(
            user=self.seller,
            outcome=self.beta,
            side=Order.Side.SELL,
            limit_price=5_000,
            original_quantity=5,
            client_order_id="notify-close-open-sell",
        )

        close_market(market_id=self.multi_market.id)

        self.assertTrue(
            Notification.objects.filter(
                user=self.buyer,
                event_type=Notification.EventType.MARKET_CLOSED,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.seller,
                event_type=Notification.EventType.MARKET_CLOSED,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.seller,
                event_type=Notification.EventType.ORDER_CANCELLED,
            ).exists()
        )
        self.assertFalse(
            Notification.objects.filter(user=self.house()).exists()
        )

    def test_resolution_notifies_winners_and_losers_but_not_the_house(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "notify-resolve-yes")
        self.buy(self.buyer2, self.no, 6_000, 10, "notify-resolve-no")

        resolve_market(
            market_id=self.market.id,
            winning_outcome_id=self.yes.id,
            resolved_by=self.admin,
            evidence_summary="Yes won.",
        )

        winner = Notification.objects.get(
            user=self.buyer,
            event_type=Notification.EventType.POSITION_SETTLED,
        )
        loser = Notification.objects.get(
            user=self.buyer2,
            event_type=Notification.EventType.POSITION_SETTLED,
        )
        self.assertTrue(winner.data["won"])
        self.assertEqual(winner.data["payout"], 10 * CREDIT_SCALE)
        self.assertFalse(loser.data["won"])
        self.assertEqual(loser.data["payout"], 0)
        self.assertFalse(
            Notification.objects.filter(user=self.house()).exists()
        )

    def test_void_resolution_refunds_and_notifies_position_holders(self):
        self.buy(self.buyer, self.yes, 6_000, 10, "notify-void-buy")
        position = Position.objects.get(user=self.buyer, outcome=self.yes)
        cost_basis = position.cost_basis

        resolve_market(
            market_id=self.market.id,
            winning_outcome_id=None,
            is_void=True,
            resolved_by=self.admin,
            evidence_summary="Market was voided.",
        )

        notification = Notification.objects.get(
            user=self.buyer,
            event_type=Notification.EventType.POSITION_SETTLED,
        )
        self.assertTrue(notification.data["is_void"])
        self.assertEqual(notification.data["payout"], cost_basis)

    @patch("apps.notifications.services.send_user_notification")
    def test_trade_push_uses_the_registered_device_after_commit(self, mock_send):
        UserDevice.objects.create(
            user=self.buyer,
            token="buyer-device-token",
            platform=UserDevice.Platform.WEB,
        )

        with self.captureOnCommitCallbacks() as callbacks:
            self.buy(self.buyer, self.yes, 6_000, 10, "notify-push-buy")
            self.assertFalse(mock_send.called)

        self.assertEqual(len(callbacks), 1)
        callbacks[0]()

        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.buyer.id)
        self.assertEqual(kwargs["data"]["event_type"], "order_filled")
        self.assertIn("notification_id", kwargs["data"])
        self.assertIn("Order filled", kwargs["title"])
