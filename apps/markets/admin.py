from django.contrib import admin

from .models import Market, MarketCategory, MarketOutcome, MarketPricePoint, MarketResolution


class MarketOutcomeInline(admin.TabularInline):
    model = MarketOutcome
    extra = 0


@admin.register(Market)
class MarketAdmin(admin.ModelAdmin):
    list_display = ("title", "market_type", "status", "category", "closes_at", "is_featured")
    list_filter = ("status", "market_type", "category", "is_featured")
    search_fields = ("title", "question")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [MarketOutcomeInline]


admin.site.register(MarketCategory)
admin.site.register(MarketResolution)
admin.site.register(MarketPricePoint)
