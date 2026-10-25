from rest_framework import serializers

from .models import Market, MarketCategory, MarketOutcome, MarketPricePoint
from apps.trading.constants import CREDIT_SCALE


class MarketCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketCategory
        fields = ["id", "name", "slug", "description", "icon", "is_active", "display_order"]


class MarketOutcomeSerializer(serializers.ModelSerializer):
    latest_price = serializers.SerializerMethodField()

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
            "latest_price",
        ]

    def get_latest_price(self, obj):
        annotated = getattr(obj, "latest_price_value", None)
        if annotated is not None:
            return annotated
        point = obj.price_points.order_by("-timestamp").values_list("price", flat=True).first()
        return point

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
            "outcomes",
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
