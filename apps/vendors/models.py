"""Vendor and VendorAlias models."""
from django.db import models

from apps.core.models import BaseModel
from apps.core.mixins import SoftDeleteMixin


class Vendor(BaseModel, SoftDeleteMixin):
    """Master vendor record."""

    code = models.CharField(max_length=50, unique=True, help_text="Unique vendor code from ERP")
    name = models.CharField(max_length=255, db_index=True)
    normalized_name = models.CharField(max_length=255, blank=True, db_index=True)
    tax_id = models.CharField(max_length=50, blank=True, default="")
    address = models.TextField(blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    currency = models.CharField(max_length=10, blank=True, default="USD")
    payment_terms = models.CharField(max_length=100, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")

    class Meta:
        db_table = "vendors_vendor"
        ordering = ["name"]
        verbose_name = "Vendor"
        verbose_name_plural = "Vendors"
        indexes = [
            models.Index(fields=["code"], name="idx_vendor_code"),
            models.Index(fields=["normalized_name"], name="idx_vendor_norm_name"),
        ]

    def __str__(self) -> str:
        return f"{self.code} – {self.name}"


class VendorAlias(BaseModel):
    """Alternative names / spellings for a vendor used in alias resolution."""

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="aliases")
    alias_name = models.CharField(max_length=255, db_index=True)
    normalized_alias = models.CharField(max_length=255, blank=True, db_index=True)
    source = models.CharField(
        max_length=50,
        blank=True,
        default="manual",
        help_text="Origin of alias: manual, extraction, erp",
    )

    class Meta:
        db_table = "vendors_vendor_alias"
        ordering = ["alias_name"]
        verbose_name = "Vendor Alias"
        verbose_name_plural = "Vendor Aliases"
        unique_together = [("vendor", "normalized_alias")]
        indexes = [
            models.Index(fields=["normalized_alias"], name="idx_valias_norm"),
        ]

    def __str__(self) -> str:
        return f"{self.alias_name} → {self.vendor.name}"
