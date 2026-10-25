
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify


class MarketCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    icon = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "market categories"

    def __str__(self):
        return self.name


class Market(models.Model):
    class MarketType(models.TextChoices):
        BINARY = "binary", "Binary"
        MULTI_OUTCOME = "multi_outcome", "Multi-outcome"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        RESOLVED = "resolved", "Resolved"
        VOID = "void", "Void"

    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True, blank=True)
    market_type = models.CharField(
        max_length=20,
        choices=MarketType.choices,
        default=MarketType.BINARY,
        db_index=True,
    )

    question = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    category = models.ForeignKey(
        MarketCategory,
        on_delete=models.PROTECT,
        related_name="markets",
    )

    # The user who created this market, usually an admin.
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_markets",
    )

    # Explain precisely how YES or NO will be determined.
    resolution_criteria = models.TextField()
    resolution_source_description = models.CharField(max_length=255)
    resolution_source_url = models.URLField(blank=True)

    # Optional image for market discovery cards.
    image_url = models.URLField(blank=True)
    image_public_id = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    opens_at = models.DateTimeField()
    closes_at = models.DateTimeField()
    resolution_deadline = models.DateTimeField()

    is_featured = models.BooleanField(default=False)

    # Primary issuance: the platform liquidity account mints complete sets on
    # demand so buyers can trade before any user-owned shares exist. Each set
    # is backed by CREDIT_SCALE credits of collateral.
    issuance_limit = models.PositiveBigIntegerField(default=10_000)
    issued_quantity = models.PositiveBigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["status", "closes_at"],
                name="market_status_close_idx",
            ),
            models.Index(
                fields=["category", "status"],
                name="market_category_status_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(closes_at__gt=models.F("opens_at")),
                name="market_close_after_open",
            ),
            models.CheckConstraint(
                condition=Q(
                    resolution_deadline__gte=models.F("closes_at")
                ),
                name="market_deadline_after_close",
            ),
            models.CheckConstraint(
                condition=Q(issued_quantity__lte=models.F("issuance_limit")),
                name="market_issued_within_limit",
            ),
        ]

    def clean(self):
        super().clean()

        if self.opens_at and self.closes_at:
            if self.closes_at <= self.opens_at:
                raise ValidationError({
                    "closes_at": "The market must close after it opens."
                })

        if self.closes_at and self.resolution_deadline:
            if self.resolution_deadline < self.closes_at:
                raise ValidationError({
                    "resolution_deadline":
                        "The resolution deadline cannot precede closing."
                })

        if self.issuance_limit < self.issued_quantity:
            raise ValidationError({
                "issuance_limit":
                    "The issuance limit cannot be lower than the quantity "
                    "already issued."
            })

    def save(self, *args, **kwargs):
        if not self.slug and self.title:
            base_slug = slugify(self.title)[:180].strip("-")
            candidate = base_slug or "market"
            suffix = 2

            while Market.objects.filter(slug=candidate).exclude(
                pk=self.pk
            ).exists():
                candidate = f"{base_slug[:175]}-{suffix}"
                suffix += 1

            self.slug = candidate

        super().save(*args, **kwargs)

    def __str__(self):
        return self.question


class MarketOutcome(models.Model):
    market = models.ForeignKey(
        Market,
        on_delete=models.PROTECT,
        related_name="outcomes",
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    description = models.CharField(max_length=255, blank=True)
    image_url = models.URLField(blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["market", "slug"],
                name="market_outcome_unique_slug",
            ),
            models.UniqueConstraint(
                fields=["market", "display_order"],
                name="market_outcome_unique_order",
            ),
        ]
        indexes = [
            models.Index(fields=["market", "is_active"], name="outcome_market_active_idx"),
        ]

    def clean(self):
        super().clean()
        if self.market_id and self.market.status != Market.Status.DRAFT:
            original = MarketOutcome.objects.filter(pk=self.pk).first()
            if original is None:
                raise ValidationError("Outcomes can only be added while the market is draft.")
            if original.slug != self.slug:
                raise ValidationError("Outcome identity cannot change after draft.")

        if self.pk and self.market_id:
            previous_active = (
                MarketOutcome.objects.filter(pk=self.pk)
                .values_list("is_active", flat=True)
                .first()
            )
            if (
                previous_active is not None
                and previous_active != self.is_active
                and Market.objects.filter(
                    pk=self.market_id, issued_quantity__gt=0
                ).exists()
            ):
                raise ValidationError(
                    "Outcomes cannot be activated or deactivated after share "
                    "issuance has started."
                )

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = slugify(self.name)[:130].strip("-") or "outcome"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.market.title}: {self.name}"


class MarketResolution(models.Model):
    market = models.OneToOneField(
        Market,
        on_delete=models.PROTECT,
        related_name="resolution",
    )
    winning_outcome = models.ForeignKey(
        MarketOutcome,
        on_delete=models.PROTECT,
        related_name="resolutions",
        null=True,
        blank=True,
    )
    is_void = models.BooleanField(default=False)

    evidence_summary = models.TextField()
    evidence_url = models.URLField(blank=True)

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="market_resolutions",
    )

    resolved_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-resolved_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(is_void=True, winning_outcome__isnull=True)
                    | Q(is_void=False, winning_outcome__isnull=False)
                ),
                name="resolution_void_xor_winner",
            ),
        ]

    def clean(self):
        super().clean()
        if self.winning_outcome_id and self.winning_outcome.market_id != self.market_id:
            raise ValidationError({
                "winning_outcome": "Winning outcome must belong to the resolved market."
            })

    def __str__(self):
        outcome = "Void" if self.is_void else self.winning_outcome
        return f"{self.market.title}: {outcome}"


class MarketPricePoint(models.Model):
    outcome = models.ForeignKey(
        MarketOutcome,
        on_delete=models.CASCADE,
        related_name="price_points",
    )
    price = models.PositiveIntegerField()
    volume = models.PositiveBigIntegerField(default=0)
    resolution_seconds = models.PositiveIntegerField(default=0)
    timestamp = models.DateTimeField(db_index=True)
    source_trade_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["timestamp"]
        constraints = [
            models.CheckConstraint(
                condition=Q(price__gte=0) & Q(price__lte=10000),
                name="price_point_valid_price",
            ),
            models.UniqueConstraint(
                fields=["outcome", "timestamp"],
                name="price_point_unique_bucket",
            ),
        ]
        indexes = [
            models.Index(fields=["outcome", "timestamp"], name="price_point_outcome_time_idx"),
        ]
