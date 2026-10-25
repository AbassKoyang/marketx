from rest_framework import serializers

from .models import Market, MarketCategory, MarketOutcome, MarketPricePoint
from apps.trading.constants import CREDIT_SCALE
from apps.trading.pricing import lmsr_prices


def price_map_for_market(market, context):
    """Compute and cache the current tick for every active outcome."""
    cache = context.setdefault("market_price_map", {})
    if market.pk not in cache:
        outcomes = [outcome for outcome in market.outcomes.all() if outcome.is_active]
        resolution = getattr(market, "resolution", None)
        if (
            resolution is not None
            and not resolution.is_void
            and resolution.winning_outcome_id
        ):
            # A resolved market is certain: the winner pays CREDIT_SCALE and the
            # other outcomes pay zero. The vector still sums to CREDIT_SCALE.
            cache[market.pk] = {
                outcome.id: (
                    CREDIT_SCALE
                    if outcome.id == resolution.winning_outcome_id
                    else 0
                )
                for outcome in outcomes
            }
        else:
            ticks = lmsr_prices(
                [outcome.net_shares for outcome in outcomes],
                market.liquidity_b,
            )
            cache[market.pk] = {
                outcome.id: tick for outcome, tick in zip(outcomes, ticks)
            }
    return cache[market.pk]


class MarketCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketCategory
        fields = ["id", "name", "slug", "description", "icon", "is_active", "display_order"]


class MarketOutcomeSerializer(serializers.ModelSerializer):
    latest_price = serializers.SerializerMethodField()
    current_price = serializers.SerializerMethodField()
    current_price_decimal = serializers.SerializerMethodField()

    class Meta:
        model = MarketOutcome
        fields = [
            "id",
            "market",
            "name",
            "slug",
            "description",
            "image_url",
            "display_order",
            "is_active",
            "net_shares",
            "latest_price",
            "current_price",
            "current_price_decimal",
        ]
        read_only_fields = ["net_shares"]

    def get_current_price(self, obj):
        if not obj.is_active:
            return None
        return price_map_for_market(obj.market, self.context).get(obj.id)

    def get_current_price_decimal(self, obj):
        tick = self.get_current_price(obj)
        return tick / CREDIT_SCALE if tick is not None else None

    def get_latest_price(self, obj):
        # Backwards-compatible alias. Prices are now a unified LMSR vector, not
        # the last trade price for each outcome in isolation.
        return self.get_current_price(obj)

    def validate(self, attrs):
        market = attrs.get("market") or getattr(self.instance, "market", None)
        if market and market.status != Market.Status.DRAFT:
            if self.instance is None:
                raise serializers.ValidationError("Outcomes can only be added while the market is draft.")
            if "slug" in attrs and attrs["slug"] != self.instance.slug:
                raise serializers.ValidationError("Outcome slug cannot change after draft.")
        if (
            self.instance is not None
            and "is_active" in attrs
            and attrs["is_active"] != self.instance.is_active
            and Market.objects.filter(
                pk=self.instance.market_id, issued_quantity__gt=0
            ).exists()
        ):
            raise serializers.ValidationError(
                "Outcomes cannot be activated or deactivated after share issuance has started."
            )
        return attrs


class MarketSerializer(serializers.ModelSerializer):
    category = MarketCategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        source="category",
        queryset=MarketCategory.objects.all(),
        write_only=True,
    )
    outcomes = MarketOutcomeSerializer(many=True, read_only=True)
    current_prices = serializers.SerializerMethodField()
    resolution = serializers.SerializerMethodField()

    class Meta:
        model = Market
        fields = [
            "id",
            "title",
            "slug",
            "market_type",
            "question",
            "description",
            "category",
            "category_id",
            "resolution_criteria",
            "resolution_source_description",
            "resolution_source_url",
            "image_url",
            "image_public_id",
            "status",
            "opens_at",
            "closes_at",
            "resolution_deadline",
            "is_featured",
            "issuance_limit",
            "issued_quantity",
            "liquidity_b",
            "outcomes",
            "current_prices",
            "resolution",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "status",
            "image_url",
            "image_public_id",
            "issuance_limit",
            "issued_quantity",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "liquidity_b": {"min_value": 1},
        }

    def get_current_prices(self, obj):
        prices = price_map_for_market(obj, self.context)
        rows = []
        for outcome in obj.outcomes.all():
            if not outcome.is_active:
                continue
            tick = prices.get(outcome.id)
            rows.append({
                "outcome_id": outcome.id,
                "name": outcome.name,
                "slug": outcome.slug,
                "price": tick,
                "price_decimal": tick / CREDIT_SCALE if tick is not None else None,
            })
        return rows

    def get_resolution(self, obj):
        resolution = getattr(obj, "resolution", None)
        if resolution is None:
            return None
        return {
            "winning_outcome": resolution.winning_outcome_id,
            "is_void": resolution.is_void,
            "evidence_summary": resolution.evidence_summary,
            "evidence_url": resolution.evidence_url,
            "resolved_at": resolution.resolved_at,
        }


class MarketPricePointSerializer(serializers.ModelSerializer):
    timestamp = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()

    class Meta:
        model = MarketPricePoint
        fields = ["timestamp", "price", "resolution_seconds", "volume"]

    def get_timestamp(self, obj):
        return int(obj.timestamp.timestamp())

    def get_price(self, obj):
        return obj.price / CREDIT_SCALE
