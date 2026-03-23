"""
EnhancedNormalizationService — Country-specific field normalization.

Extends base normalization with:
- Country-specific date/number/address formats
- Currency normalization using jurisdiction profiles
- Normalization profile integration
- Audit event emission
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from apps.extraction_core.models import TaxJurisdictionProfile
from apps.extraction_core.services.output_contract import (
    ExtractionOutputContract,
    FieldValue,
)

logger = logging.getLogger(__name__)

# Locale-specific decimal/thousands separators
LOCALE_FORMATS = {
    "IN": {"decimal": ".", "thousands": ","},
    "AE": {"decimal": ".", "thousands": ","},
    "SA": {"decimal": ".", "thousands": ","},
    "DE": {"decimal": ",", "thousands": "."},
    "FR": {"decimal": ",", "thousands": " "},
}


class EnhancedNormalizationService:
    """
    Country-aware field normalization.

    Reads the jurisdiction's NormalizationProfile if available,
    otherwise uses built-in locale defaults.
    """

    def __init__(
        self,
        country_code: str = "",
        regime_code: str = "",
        jurisdiction_profile: Optional[TaxJurisdictionProfile] = None,
    ):
        self.country_code = country_code.upper()
        self.regime_code = regime_code
        self.jurisdiction_profile = jurisdiction_profile
        self._norm_profile = None

        if jurisdiction_profile:
            try:
                self._norm_profile = getattr(
                    jurisdiction_profile, "normalization_profile", None,
                )
            except Exception:
                pass

    def normalize_output(
        self,
        output: ExtractionOutputContract,
    ) -> ExtractionOutputContract:
        """
        Normalize all fields in the output contract in-place.

        Returns the same output object for chaining.
        """
        # Header fields
        for field_code, fv in output.header.items():
            self._normalize_field(field_code, fv)

        # References
        for field_code, fv in output.references.items():
            self._normalize_field(field_code, fv)

        # Commercial terms
        for field_code, fv in output.commercial_terms.items():
            self._normalize_field(field_code, fv)

        # Tax fields
        for field_code, fv in output.tax.tax_fields.items():
            self._normalize_field(field_code, fv)

        # Parties
        for party_data in (
            output.parties.supplier,
            output.parties.buyer,
            output.parties.ship_to,
            output.parties.bill_to,
        ):
            for field_code, fv in party_data.items():
                self._normalize_field(field_code, fv)

        # Line items
        for li in output.line_items:
            for field_code, fv in li.fields.items():
                self._normalize_field(field_code, fv)

        return output

    def _normalize_field(self, field_code: str, fv: FieldValue) -> None:
        """Normalize a single field value based on its likely type."""
        if fv.value is None:
            return

        value_str = str(fv.value)

        # Detect field type from code suffix patterns
        if self._is_date_field(field_code):
            fv.value = self._normalize_date(value_str)
        elif self._is_amount_field(field_code):
            fv.value = self._normalize_amount(value_str)
        elif self._is_percentage_field(field_code):
            fv.value = self._normalize_percentage(value_str)
        elif self._is_tax_id_field(field_code):
            fv.value = self._normalize_tax_id(value_str)
        else:
            fv.value = self._normalize_string(value_str)

    def _normalize_date(self, value: str) -> str:
        """Normalize date to ISO 8601 format (YYYY-MM-DD)."""
        if not value:
            return value

        target_format = "%Y-%m-%d"
        if self._norm_profile and self._norm_profile.date_output_format:
            # Map common format strings
            fmt_map = {
                "YYYY-MM-DD": "%Y-%m-%d",
                "DD/MM/YYYY": "%d/%m/%Y",
                "MM/DD/YYYY": "%m/%d/%Y",
            }
            target_format = fmt_map.get(
                self._norm_profile.date_output_format, "%Y-%m-%d",
            )

        input_formats = [
            "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
            "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%d.%m.%Y",
        ]

        if self._norm_profile and self._norm_profile.date_input_formats:
            fmt_map = {
                "DD/MM/YYYY": "%d/%m/%Y",
                "MM/DD/YYYY": "%m/%d/%Y",
                "DD-MM-YYYY": "%d-%m-%Y",
                "YYYY-MM-DD": "%Y-%m-%d",
                "DD-MMM-YYYY": "%d-%b-%Y",
                "DD.MM.YYYY": "%d.%m.%Y",
            }
            profile_fmts = [
                fmt_map.get(f, f) for f in self._norm_profile.date_input_formats
            ]
            input_formats = profile_fmts + input_formats

        for fmt in input_formats:
            try:
                parsed = datetime.strptime(value.strip(), fmt)
                return parsed.strftime(target_format)
            except ValueError:
                continue

        return value

    def _normalize_amount(self, value: str) -> str:
        """Normalize monetary amount to plain decimal string."""
        if not value:
            return value

        locale_cfg = LOCALE_FORMATS.get(self.country_code, {"decimal": ".", "thousands": ","})

        if self._norm_profile:
            locale_cfg = {
                "decimal": self._norm_profile.decimal_separator or ".",
                "thousands": self._norm_profile.thousands_separator or ",",
            }

        # Strip currency symbols and whitespace
        cleaned = re.sub(r"[₹$€£¥AED\sSAR,INR]", "", value)

        # Handle locale-specific separators
        if locale_cfg["decimal"] == "," and locale_cfg["thousands"] == ".":
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(locale_cfg["thousands"], "")
            if locale_cfg["decimal"] != ".":
                cleaned = cleaned.replace(locale_cfg["decimal"], ".")

        # Remove any remaining non-numeric chars except . and -
        cleaned = re.sub(r"[^\d.\-]", "", cleaned)

        try:
            return str(Decimal(cleaned).normalize())
        except (InvalidOperation, ValueError):
            return value

    def _normalize_percentage(self, value: str) -> str:
        """Normalize percentage to plain decimal string."""
        cleaned = re.sub(r"[%\s]", "", str(value))
        try:
            return str(Decimal(cleaned).normalize())
        except (InvalidOperation, ValueError):
            return value

    def _normalize_tax_id(self, value: str) -> str:
        """Normalize tax ID — strip whitespace, uppercase."""
        return re.sub(r"\s+", "", str(value)).upper()

    def _normalize_string(self, value: str) -> str:
        """Basic string normalization — trim whitespace."""
        return str(value).strip()

    # ------------------------------------------------------------------
    # Field type detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_date_field(field_code: str) -> bool:
        return any(
            kw in field_code.lower()
            for kw in ("date", "_dt", "due_date", "invoice_date", "grn_date")
        )

    @staticmethod
    def _is_amount_field(field_code: str) -> bool:
        return any(
            kw in field_code.lower()
            for kw in (
                "amount", "total", "subtotal", "price", "value",
                "cgst", "sgst", "igst", "vat", "tax_amount",
            )
        )

    @staticmethod
    def _is_percentage_field(field_code: str) -> bool:
        return any(
            kw in field_code.lower()
            for kw in ("rate", "percent", "pct", "_rate")
        )

    @staticmethod
    def _is_tax_id_field(field_code: str) -> bool:
        return any(
            kw in field_code.lower()
            for kw in ("gstin", "trn", "vat_id", "tax_id", "tin")
        )
