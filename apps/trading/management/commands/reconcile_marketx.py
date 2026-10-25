from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models import Sum

from apps.markets.models import Market
from apps.trading.constants import CREDIT_SCALE
from apps.trading.models import Order, Position, Trade
from apps.wallet.models import Wallet, WalletLedgerEntry


class Command(BaseCommand):
    help = "Report MarketX wallet, order, trade, market, and position accounting discrepancies."

    def handle(self, *args, **options):
        issues = []

        for wallet in Wallet.objects.all().iterator():
            totals = WalletLedgerEntry.objects.filter(wallet=wallet).aggregate(
                available=Sum("available_delta"),
                reserved=Sum("reserved_delta"),
            )
            if wallet.available_balance != (totals["available"] or 0):
                issues.append(f"wallet:{wallet.id}: available balance does not match ledger")
            if wallet.reserved_balance != (totals["reserved"] or 0):
                issues.append(f"wallet:{wallet.id}: reserved balance does not match ledger")

        for order in Order.objects.all().iterator():
            traded = Trade.objects.filter(buy_order=order).aggregate(total=Sum("quantity"))["total"] or 0
            traded += Trade.objects.filter(sell_order=order).aggregate(total=Sum("quantity"))["total"] or 0
            if order.filled_quantity != traded:
                issues.append(f"order:{order.id}: filled quantity does not match trades")

        for position in Position.objects.filter(reserved_quantity__gt=0).iterator():
            open_sells = Order.objects.filter(
                user=position.user,
                outcome=position.outcome,
                side=Order.Side.SELL,
                status__in=[Order.Status.OPEN, Order.Status.PARTIALLY_FILLED],
                cancelled_at__isnull=True,
            ).aggregate(
                original=Sum("original_quantity"),
                filled=Sum("filled_quantity"),
            )
            open_sell_quantity = (open_sells["original"] or 0) - (open_sells["filled"] or 0)
            if position.reserved_quantity != open_sell_quantity:
                issues.append(f"position:{position.id}: reserved quantity does not match open sell orders")

        unsettled_markets = Market.objects.exclude(
            status__in=[Market.Status.RESOLVED, Market.Status.VOID]
        )
        for market in unsettled_markets.iterator():
            if market.issued_quantity > market.issuance_limit:
                issues.append(f"market:{market.id}: issued quantity exceeds issuance limit")
            for outcome in market.outcomes.all():
                if outcome.net_shares > market.issued_quantity:
                    issues.append(
                        f"outcome:{outcome.id}: net shares exceed issued quantity"
                    )
                total_shares = (
                    Position.objects.filter(outcome=outcome).aggregate(
                        total=Sum("quantity")
                    )["total"]
                    or 0
                )
                if total_shares != market.issued_quantity:
                    issues.append(
                        f"outcome:{outcome.id}: outstanding shares do not match issued quantity"
                    )

        house = (
            get_user_model()
            .objects.filter(email=settings.MARKETX_HOUSE_EMAIL)
            .first()
        )
        if house:
            house_wallet = Wallet.objects.filter(user=house).first()
            required_collateral = (
                unsettled_markets.aggregate(total=Sum("issued_quantity"))["total"]
                or 0
            ) * CREDIT_SCALE
            if house_wallet is None:
                if required_collateral:
                    issues.append("house: liquidity wallet is missing")
            elif house_wallet.reserved_balance < required_collateral:
                issues.append(
                    "house: reserved collateral is below the outstanding issuance"
                )

        if not issues:
            self.stdout.write(self.style.SUCCESS("No discrepancies found."))
            return

        for issue in issues:
            self.stdout.write(self.style.ERROR(issue))
        raise SystemExit(1)
