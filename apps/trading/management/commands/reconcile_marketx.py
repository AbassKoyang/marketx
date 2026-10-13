from django.core.management.base import BaseCommand
from django.db.models import Sum

from apps.trading.models import Order, Position, Trade
from apps.wallet.models import Wallet, WalletLedgerEntry


class Command(BaseCommand):
    help = "Report MarketX wallet, order, trade, and position accounting discrepancies."

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
            ).aggregate(
                original=Sum("original_quantity"),
                filled=Sum("filled_quantity"),
            )
            open_sell_quantity = (open_sells["original"] or 0) - (open_sells["filled"] or 0)
            if position.reserved_quantity != open_sell_quantity:
                issues.append(f"position:{position.id}: reserved quantity does not match open sell orders")

        if not issues:
            self.stdout.write(self.style.SUCCESS("No discrepancies found."))
            return

        for issue in issues:
            self.stdout.write(self.style.ERROR(issue))
        raise SystemExit(1)
