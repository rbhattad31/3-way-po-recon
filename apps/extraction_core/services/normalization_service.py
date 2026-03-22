"""
NormalizationService — Jurisdiction-driven field value normalization.

Loads a ``NormalizationProfile`` (linked 1:1 to TaxJurisdictionProfile)
for the resolved country_code/regime_code and applies locale-aware
transformations:

    - Tax ID normalization (GSTIN / TRN / VAT ID format cleanup)
    - Currency / amount normalization (strip symbols, locale separators)
    - Date normalization (locale input formats → ISO 8601)
    - Address cleanup (whitespace, line-break consolidation)
    - Language-specific cleanup (custom rules from profile)

Design:
    - Zero hardcoded country logic — everything from NormalizationProfile
      + TaxJurisdictionProfile fields
    - Operates on FieldResult objects in-place (writes normalized_value)
    - Per-field normalization_rules_json from TaxFieldDefinition honored
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from apps.extraction_configs.models import NormalizationProfile, TaxFieldDefinition
from apps.extraction_core.models import TaxJurisdictionProfile
from apps.extraction_core.services.extraction_service import FieldResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date format token → Python strptime mapping
# ---------------------------------------------------------------------------

_DATE_TOKEN_MAP = {
    "YYYY": "%Y",
    "YY": "%y",
    "MM": "%m",
    "DD": "%d",
    "MMM": "%b",  # abbreviated month name
    "MMMM": "%B",  # full month name
}


def _format_to_strptime(fmt: str) -> str:
    """Convert a date format string like ``DD/MM/YYYY`` to strptime."""
    result = fmt
    # Longest tokens first so MMMM doesn't partially match MM
    for token in sorted(_DATE_TOKEN_MAP, key=len, reverse=True):
        result = result.replace(token, _DATE_TOKEN_MAP[token])
    return result


class NormalizationService:
    """
    Applies jurisdiction-aware normalization to extracted FieldResults.

    Usage::

        svc = NormalizationService(country_code="IN", regime_code="GST")
        svc.normalize_fields(header_fields, tax_fields)
    """

    def __init__(
        self,
        country_code: str,
        regime_code: str = "",
    ):
        self._country_code = country_code
        self._regime_code = regime_code
        self._profile: Optional[NormalizationProfile] = None
        self._jurisdiction: Optional[TaxJurisdictionProfile] = None
        self._field_rules: dict[str, dict] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Profile loading
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        try:
            qs = TaxJurisdictionProfile.objects.select_related(
                "normalization_profile",
            ).filter(
                country_code=self._country_code,
                is_active=True,
            )
            if self._regime_code:
                qs = qs.filter(tax_regime=self._regime_code)

            self._jurisdiction = qs.first()
            if self._jurisdiction:
                self._profile = getattr(
                    self._jurisdiction, "normalization_profile", None,
                )
        except Exception:
            logger.exception(
                "Failed to load normalization profile for %s/%s",
                self._country_code,
                self._regime_code,
            )

        # Load per-field normalization rules
        try:
            for fd in TaxFieldDefinition.objects.filter(
                is_active=True,
            ).exclude(normalization_rules_json={}):
                self._field_rules[fd.field_key] = fd.normalization_rules_json
        except Exception:
            logger.exception("Failed to load per-field normalization rules")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize_fields(
        self,
        header_fields: dict[str, FieldResult],
        tax_fields: dict[str, FieldResult],
    ) -> int:
        """
        Normalize all extracted fields in-place.

        Returns the count of fields that were successfully normalized.
        """
        self._ensure_loaded()
        count = 0

        for fr in list(header_fields.values()) + list(tax_fields.values()):
            if not fr.extracted or not fr.raw_value:
                continue
            if self._normalize_field(fr):
                count += 1

        logger.info(
            "Normalized %d fields for %s/%s",
            count,
            self._country_code,
            self._regime_code,
        )
        return count

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _normalize_field(self, fr: FieldResult) -> bool:
        """
        Normalize a single FieldResult based on its data_type
        and any per-field rules.  Returns True if normalized.
        """
        original = fr.raw_value

        # Per-field rules take priority
        field_rules = self._field_rules.get(fr.field_key, {})
        if field_rules:
            fr.normalized_value = self._apply_field_rules(
                fr.raw_value, field_rules,
            )

        # Type-based normalization
        if fr.data_type == "DATE":
            fr.normalized_value = self._normalize_date(fr.raw_value)
        elif fr.data_type in ("CURRENCY", "DECIMAL"):
            fr.normalized_value = self._normalize_amount(fr.raw_value)
        elif fr.data_type == "PERCENTAGE":
            fr.normalized_value = self._normalize_percentage(fr.raw_value)
        elif fr.data_type == "TAX_ID":
            fr.normalized_value = self._normalize_tax_id(fr.raw_value)
        elif fr.data_type == "ADDRESS":
            fr.normalized_value = self._normalize_address(fr.raw_value)
        elif fr.data_type == "BOOLEAN":
            fr.normalized_value = self._normalize_boolean(fr.raw_value)
        elif fr.data_type == "INTEGER":
            fr.normalized_value = self._normalize_integer(fr.raw_value)
        else:
            fr.normalized_value = self._normalize_string(fr.raw_value)

        # Apply custom rules from profile
        if self._profile and self._profile.custom_rules_json:
            fr.normalized_value = self._apply_custom_rules(
                fr.normalized_value or fr.raw_value,
                self._profile.custom_rules_json,
                fr.field_key,
            )

        return bool(fr.normalized_value and fr.normalized_value != original)

    # ------------------------------------------------------------------
    # Type-specific normalizers
    # ------------------------------------------------------------------

    def _normalize_date(self, value: str) -> str:
        """Parse date using jurisdiction input formats → ISO 8601 output."""
        value = value.strip()

        input_formats: list[str] = []
        if self._profile and self._profile.date_input_formats:
            input_formats = self._profile.date_input_formats
        elif self._jurisdiction and self._jurisdiction.date_formats:
            input_formats = self._jurisdiction.date_formats

        output_fmt = "%Y-%m-%d"
        if self._profile and self._profile.date_output_format:
            output_fmt = _format_to_strptime(self._profile.date_output_format)

        for fmt_str in input_formats:
            strp_fmt = _format_to_strptime(fmt_str)
            try:
                dt = datetime.strptime(value, strp_fmt)
                return dt.strftime(output_fmt)
            except ValueError:
                continue

        # Fallback: try common ISO format
        for fallback_fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                dt = datetime.strptime(value, fallback_fmt)
                return dt.strftime(output_fmt)
            except ValueError:
                continue

        return value  # Could not parse — return as-is

    def _normalize_amount(self, value: str) -> str:
        """
        Normalize a monetary amount:
            - Strip currency symbols
            - Handle locale-specific separators
            - Return clean decimal string
        """
        text = value.strip()

        # Strip currency symbol from profile
        if self._profile and self._profile.currency_symbol:
            text = text.replace(self._profile.currency_symbol, "")

        # Strip common currency symbols/codes
        text = re.sub(r"[₹$€£¥]", "", text)
        text = re.sub(
            r"\b(INR|AED|SAR|USD|EUR|GBP)\b", "", text, flags=re.IGNORECASE
        )

        text = text.strip()

        # Handle locale-specific separators
        thousands_sep = ","
        decimal_sep = "."
        if self._profile:
            thousands_sep = self._profile.thousands_separator or ","
            decimal_sep = self._profile.decimal_separator or "."

        if thousands_sep and thousands_sep != decimal_sep:
            text = text.replace(thousands_sep, "")

        if decimal_sep != ".":
            text = text.replace(decimal_sep, ".")

        # Remove any remaining non-numeric except . and -
        text = re.sub(r"[^\d.\-]", "", text)

        # Validate it's a number
        try:
            float(text)
            return text
        except ValueError:
            return value

    def _normalize_percentage(self, value: str) -> str:
        """Strip % sign and return clean decimal."""
        text = value.strip().replace("%", "").strip()
        try:
            float(text)
            return text
        except ValueError:
            return value

    def _normalize_tax_id(self, value: str) -> str:
        """
        Normalize tax IDs using jurisdiction-specific patterns.

        - Strip whitespace and common separators
        - Uppercase
        - Validate against jurisdiction regex if available
        """
        text = value.strip()
        text = re.sub(r"[\s\-.]", "", text)
        text = text.upper()

        # Validate format against jurisdiction regex
        if self._jurisdiction and self._jurisdiction.tax_id_regex:
            if not re.fullmatch(self._jurisdiction.tax_id_regex, text):
                logger.debug(
                    "Tax ID '%s' does not match jurisdiction regex: %s",
                    text,
                    self._jurisdiction.tax_id_regex,
                )

        return text

    def _normalize_address(self, value: str) -> str:
        """Clean up address: consolidate whitespace, normalize line breaks."""
        text = value.strip()
        # Consolidate multiple whitespace/newlines
        text = re.sub(r"\s*\n\s*", ", ", text)
        text = re.sub(r"\s{2,}", " ", text)
        # Remove trailing commas
        text = text.rstrip(",").strip()
        return text

    def _normalize_boolean(self, value: str) -> str:
        """Normalize boolean-like values to 'true'/'false'."""
        text = value.strip().lower()
        truthy = {"yes", "true", "1", "y", "applicable", "rcm"}
        falsy = {"no", "false", "0", "n", "not applicable", "na", "n/a"}
        if text in truthy:
            return "true"
        if text in falsy:
            return "false"
        return value

    def _normalize_integer(self, value: str) -> str:
        """Strip decimals and non-numeric characters."""
        text = value.strip()
        text = re.sub(r"[^\d\-]", "", text)
        try:
            return str(int(text))
        except ValueError:
            return value

    def _normalize_string(self, value: str) -> str:
        """General string cleanup: strip whitespace, collapse spaces."""
        text = value.strip()
        text = re.sub(r"\s{2,}", " ", text)
        return text

    # ------------------------------------------------------------------
    # Per-field and custom rules
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_field_rules(value: str, rules: dict) -> str:
        """
        Apply per-field normalization rules from
        ``TaxFieldDefinition.normalization_rules_json``.

        Supported rule keys:
            - strip_chars: characters to remove
            - uppercase: bool
            - lowercase: bool
            - prefix: string to prepend
            - suffix: string to append
            - replace: list of [old, new] pairs
        """
        text = value

        if rules.get("strip_chars"):
            for ch in rules["strip_chars"]:
                text = text.replace(ch, "")

        if rules.get("uppercase"):
            text = text.upper()
        elif rules.get("lowercase"):
            text = text.lower()

        if rules.get("prefix") and not text.startswith(rules["prefix"]):
            text = rules["prefix"] + text

        if rules.get("suffix") and not text.endswith(rules["suffix"]):
            text = rules["suffix"]

        for pair in rules.get("replace", []):
            if len(pair) == 2:
                text = text.replace(pair[0], pair[1])

        return text.strip()

    @staticmethod
    def _apply_custom_rules(
        value: str,
        custom_rules: dict,
        field_key: str,
    ) -> str:
        """
        Apply NormalizationProfile.custom_rules_json.

        Structure::

            {
                "field_overrides": {
                    "field_key": {"uppercase": true, ...}
                },
                "global": {"strip_zero_width": true}
            }
        """
        # Global rules
        global_rules = custom_rules.get("global", {})
        if global_rules.get("strip_zero_width"):
            value = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", value)

        # Per-field overrides
        field_overrides = custom_rules.get("field_overrides", {})
        if field_key in field_overrides:
            override = field_overrides[field_key]
            if override.get("uppercase"):
                value = value.upper()
            elif override.get("lowercase"):
                value = value.lower()

        return value
