from rest_framework import serializers

from .models import Market, MarketCategory, MarketOutcome, MarketPricePoint
from apps.trading.constants import CREDIT_SCALE


class MarketCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketCategory
        fields = ["id", "name", "slug", "description", "icon", "is_active", "display_order"]


class MarketOutcomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarketOutcome
        fields = [
            "id",
            "market",
            "name",
            "slug",
            "description",
            "image_url",
            "image_public_id",
            "display_order",
            "is_active",
        ]

    def validate(self, attrs):
        market = attrs.get("market") or getattr(self.instance, "market", None)
        if market and market.status != Market.Status.DRAFT:
            if self.instance is None:
                raise serializers.ValidationError("Outcomes can only be added while the market is draft.")
            if "slug" in attrs and attrs["slug"] != self.instance.slug:
                raise serializers.ValidationError("Outcome slug cannot change after draft.")
        return attrs


class MarketSerializer(serializers.ModelSerializer):
    category = MarketCategorySerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        source="category",
        queryset=MarketCategory.objects.all(),
        write_only=True,
    )
    outcomes = MarketOutcomeSerializer(many=True, read_only=True)

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
            "status",
            "opens_at",
            "closes_at",
            "resolution_deadline",
            "is_featured",
            "outcomes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["status", "image_url", "image_public_id", "created_at", "updated_at"]


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
