from celery import shared_task
from django.utils import timezone

from apps.markets.models import Market
from .services import close_market, settle_market


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def close_expired_markets(self):
    market_ids = list(Market.objects.filter(
        status=Market.Status.OPEN,
        closes_at__lte=timezone.now(),
    ).values_list("id", flat=True))
    for market_id in market_ids:
        close_market(market_id=market_id)
    return {"closed": len(market_ids)}


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def settle_market_task(self, market_id):
    settle_market(market_id=market_id)
    return {"market_id": market_id, "settled": True}
