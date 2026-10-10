
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
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"
        RESOLVED = "resolved", "Resolved"
        VOID = "void", "Void"

    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True, blank=True)

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


class MarketResolution(models.Model):
    class Outcome(models.TextChoices):
        YES = "yes", "Yes"
        NO = "no", "No"
        VOID = "void", "Void"

    market = models.OneToOneField(
        Market,
        on_delete=models.PROTECT,
        related_name="resolution",
    )

    outcome = models.CharField(
        max_length=4,
        choices=Outcome.choices,
    )

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

    def __str__(self):
        return f"{self.market.title}: {self.get_outcome_display()}"
