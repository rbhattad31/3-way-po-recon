"""
ValidationService — Jurisdiction-driven field validation.

Loads validation rules from the resolved TaxJurisdictionProfile +
TaxFieldDefinition registry and runs a layered validation pipeline:

    1. Generic validations:
       - Mandatory field presence
       - Data type checks (date, amount, integer, percentage, boolean)
    2. Country-specific validations (derived from jurisdiction profile):
       - Tax registration format (GSTIN / TRN / VAT ID)
       - Tax rate validity (against known rates from config_json)
       - Header vs line-item consistency (totals reconciliation)

Output is a list of ``ValidationCheckResult`` dataclasses with:
    check_type, status, severity, message, affected_fields

Design:
    - Zero hardcoded country rules — all driven by TaxJurisdictionProfile
      config_json, TaxFieldDefinition.validation_rules_json, and
      NormalizationProfile metadata
    - Modular: each check is a separate method, easy to extend
    - New country profiles automatically get their rules applied
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from apps.extraction_configs.models import NormalizationProfile, TaxFieldDefinition
from apps.extraction_core.models import TaxJurisdictionProfile
from apps.extraction_core.services.extraction_service import (
    ExtractionTemplate,
    FieldResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class ValidationCheckResult:
    """Outcome of a single validation check."""

    check_type: str  # e.g. MANDATORY, DATA_TYPE, TAX_ID_FORMAT, AMOUNT_CONSISTENCY
    status: str  # PASS | FAIL | WARN
    severity: str  # ERROR | WARNING | INFO
    message: str
    affected_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "check_type": self.check_type,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "affected_fields": self.affected_fields,
        }


@dataclass
class ValidationResult:
    """Aggregate validation outcome for an extraction."""

    is_valid: bool = True
    error_count: int = 0
    warning_count: int = 0
    checks: list[ValidationCheckResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ValidationService:
    """
    Runs jurisdiction-aware validation on extracted FieldResults.

    Usage::

        svc = ValidationService(country_code="IN", regime_code="GST")
        result = svc.validate(
            header_fields=header_fields,
            tax_fields=tax_fields,
            template=template,
        )
    """

    def __init__(
        self,
        country_code: str,
        regime_code: str = "",
    ):
        self._country_code = country_code
        self._regime_code = regime_code
        self._jurisdiction: Optional[TaxJurisdictionProfile] = None
        self._profile: Optional[NormalizationProfile] = None
        self._field_defs: dict[str, TaxFieldDefinition] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True

        try:
            qs = TaxJurisdictionProfile.objects.filter(
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
                "Failed to load jurisdiction for validation %s/%s",
                self._country_code,
                self._regime_code,
            )

        # Preload all field definitions for fast lookup
        try:
            for fd in TaxFieldDefinition.objects.filter(is_active=True):
                self._field_defs[fd.field_key] = fd
        except Exception:
            logger.exception("Failed to load field definitions for validation")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        header_fields: dict[str, FieldResult],
        tax_fields: dict[str, FieldResult],
        template: Optional[ExtractionTemplate] = None,
    ) -> ValidationResult:
        """
        Run all validation checks and return a ``ValidationResult``.
        """
        self._ensure_loaded()
        result = ValidationResult()
        all_fields = {**header_fields, **tax_fields}

        # ── 1. Generic validations ────────────────────────────────────
        self._check_mandatory_fields(all_fields, template, result)
        self._check_data_types(all_fields, result)

        # ── 2. Country-specific validations ───────────────────────────
        self._check_tax_id_formats(all_fields, result)
        self._check_tax_rates(all_fields, result)
        self._check_field_rules(all_fields, result)
        self._check_amount_consistency(header_fields, tax_fields, result)

        # Summarize
        result.error_count = sum(
            1 for c in result.checks if c.severity == "ERROR" and c.status == "FAIL"
        )
        result.warning_count = sum(
            1 for c in result.checks if c.severity == "WARNING" and c.status == "FAIL"
        )
        result.is_valid = result.error_count == 0

        logger.info(
            "Validation for %s/%s: valid=%s errors=%d warnings=%d checks=%d",
            self._country_code,
            self._regime_code,
            result.is_valid,
            result.error_count,
            result.warning_count,
            len(result.checks),
        )

        return result

    # ------------------------------------------------------------------
    # 1a. Mandatory fields
    # ------------------------------------------------------------------

    def _check_mandatory_fields(
        self,
        all_fields: dict[str, FieldResult],
        template: Optional[ExtractionTemplate],
        result: ValidationResult,
    ) -> None:
        """Check that all mandatory fields have extracted values."""
        mandatory_keys: set[str] = set()
        if template:
            mandatory_keys = template.mandatory_keys

        # Also honour field definitions
        for key, fd in self._field_defs.items():
            if fd.is_mandatory:
                mandatory_keys.add(key)

        for key in mandatory_keys:
            fr = all_fields.get(key)
            value = (fr.normalized_value or fr.raw_value) if fr else ""
            if not fr or not fr.extracted or not value.strip():
                result.checks.append(ValidationCheckResult(
                    check_type="MANDATORY",
                    status="FAIL",
                    severity="ERROR",
                    message=f"Mandatory field '{key}' is missing or empty",
                    affected_fields=[key],
                ))
            else:
                result.checks.append(ValidationCheckResult(
                    check_type="MANDATORY",
                    status="PASS",
                    severity="INFO",
                    message=f"Mandatory field '{key}' present",
                    affected_fields=[key],
                ))

    # ------------------------------------------------------------------
    # 1b. Data type checks
    # ------------------------------------------------------------------

    def _check_data_types(
        self,
        all_fields: dict[str, FieldResult],
        result: ValidationResult,
    ) -> None:
        """Validate extracted values match their declared data types."""
        for key, fr in all_fields.items():
            if not fr.extracted:
                continue

            value = fr.normalized_value or fr.raw_value
            if not value:
                continue

            check = self._validate_type(key, value, fr.data_type)
            if check:
                result.checks.append(check)

    def _validate_type(
        self,
        field_key: str,
        value: str,
        data_type: str,
    ) -> Optional[ValidationCheckResult]:
        """Return a check result if type validation fails, else None."""
        if data_type == "DATE":
            return self._validate_date(field_key, value)
        if data_type in ("CURRENCY", "DECIMAL"):
            return self._validate_decimal(field_key, value)
        if data_type == "INTEGER":
            return self._validate_integer(field_key, value)
        if data_type == "PERCENTAGE":
            return self._validate_percentage(field_key, value)
        if data_type == "BOOLEAN":
            return self._validate_boolean(field_key, value)
        return None

    def _validate_date(
        self, field_key: str, value: str,
    ) -> Optional[ValidationCheckResult]:
        formats = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]
        for fmt in formats:
            try:
                datetime.strptime(value.strip(), fmt)
                return None  # Valid
            except ValueError:
                continue
        return ValidationCheckResult(
            check_type="DATA_TYPE",
            status="FAIL",
            severity="WARNING",
            message=f"Field '{field_key}' value '{value}' is not a valid date",
            affected_fields=[field_key],
        )

    @staticmethod
    def _validate_decimal(
        field_key: str, value: str,
    ) -> Optional[ValidationCheckResult]:
        try:
            Decimal(value.strip())
            return None
        except InvalidOperation:
            return ValidationCheckResult(
                check_type="DATA_TYPE",
                status="FAIL",
                severity="WARNING",
                message=f"Field '{field_key}' value '{value}' is not a valid decimal",
                affected_fields=[field_key],
            )

    @staticmethod
    def _validate_integer(
        field_key: str, value: str,
    ) -> Optional[ValidationCheckResult]:
        try:
            int(value.strip())
            return None
        except ValueError:
            return ValidationCheckResult(
                check_type="DATA_TYPE",
                status="FAIL",
                severity="WARNING",
                message=f"Field '{field_key}' value '{value}' is not a valid integer",
                affected_fields=[field_key],
            )

    @staticmethod
    def _validate_percentage(
        field_key: str, value: str,
    ) -> Optional[ValidationCheckResult]:
        try:
            val = float(value.strip().replace("%", ""))
            if val < 0 or val > 100:
                return ValidationCheckResult(
                    check_type="DATA_TYPE",
                    status="FAIL",
                    severity="WARNING",
                    message=f"Field '{field_key}' percentage {val} is outside 0–100 range",
                    affected_fields=[field_key],
                )
            return None
        except ValueError:
            return ValidationCheckResult(
                check_type="DATA_TYPE",
                status="FAIL",
                severity="WARNING",
                message=f"Field '{field_key}' value '{value}' is not a valid percentage",
                affected_fields=[field_key],
            )

    @staticmethod
    def _validate_boolean(
        field_key: str, value: str,
    ) -> Optional[ValidationCheckResult]:
        valid = {
            "true", "false", "yes", "no", "1", "0",
            "y", "n", "applicable", "not applicable",
        }
        if value.strip().lower() not in valid:
            return ValidationCheckResult(
                check_type="DATA_TYPE",
                status="FAIL",
                severity="WARNING",
                message=f"Field '{field_key}' value '{value}' is not a valid boolean",
                affected_fields=[field_key],
            )
        return None

    # ------------------------------------------------------------------
    # 2a. Tax ID format validation
    # ------------------------------------------------------------------

    def _check_tax_id_formats(
        self,
        all_fields: dict[str, FieldResult],
        result: ValidationResult,
    ) -> None:
        """Validate tax IDs against jurisdiction regex patterns."""
        if not self._jurisdiction:
            return

        tax_id_regex = self._jurisdiction.tax_id_regex
        if not tax_id_regex:
            return

        # Find all TAX_ID fields
        for key, fr in all_fields.items():
            if fr.data_type != "TAX_ID" or not fr.extracted:
                continue

            value = (fr.normalized_value or fr.raw_value).strip()
            if not value:
                continue

            # Use field-specific regex if available, else jurisdiction default
            fd = self._field_defs.get(key)
            regex = (fd.validation_regex if fd and fd.validation_regex else tax_id_regex)

            try:
                if re.fullmatch(regex, value):
                    result.checks.append(ValidationCheckResult(
                        check_type="TAX_ID_FORMAT",
                        status="PASS",
                        severity="INFO",
                        message=(
                            f"{self._jurisdiction.tax_id_label} '{key}' "
                            f"format is valid"
                        ),
                        affected_fields=[key],
                    ))
                else:
                    result.checks.append(ValidationCheckResult(
                        check_type="TAX_ID_FORMAT",
                        status="FAIL",
                        severity="ERROR",
                        message=(
                            f"{self._jurisdiction.tax_id_label} '{key}' "
                            f"value '{value}' does not match expected format"
                        ),
                        affected_fields=[key],
                    ))
            except re.error:
                logger.warning("Invalid regex for tax ID validation: %s", regex)

    # ------------------------------------------------------------------
    # 2b. Tax rate validation
    # ------------------------------------------------------------------

    def _check_tax_rates(
        self,
        all_fields: dict[str, FieldResult],
        result: ValidationResult,
    ) -> None:
        """
        Validate tax rates against jurisdiction config_json.

        Uses ``standard_vat_rate`` and ``tax_components`` from
        TaxJurisdictionProfile.config_json to determine valid rates.
        """
        if not self._jurisdiction:
            return

        config = self._jurisdiction.config_json or {}
        standard_rate = config.get("standard_vat_rate") or config.get(
            "standard_tax_rate",
        )

        # Build set of valid rates for this jurisdiction
        valid_rates: set[float] = {0.0}  # zero-rated is always valid
        if standard_rate is not None:
            valid_rates.add(float(standard_rate))
            # Common split: half-rate for CGST/SGST
            if config.get("has_state_level_tax"):
                valid_rates.add(float(standard_rate) / 2)

        # Check all PERCENTAGE tax fields
        rate_fields = [
            (k, fr) for k, fr in all_fields.items()
            if fr.data_type == "PERCENTAGE" and fr.extracted and fr.is_mandatory is not None
            and ("rate" in k.lower())
        ]

        for key, fr in rate_fields:
            value = (fr.normalized_value or fr.raw_value).strip().replace("%", "")
            try:
                rate = float(value)
            except ValueError:
                continue

            if valid_rates and rate not in valid_rates:
                result.checks.append(ValidationCheckResult(
                    check_type="TAX_RATE",
                    status="FAIL",
                    severity="WARNING",
                    message=(
                        f"Tax rate field '{key}' has value {rate}% which is "
                        f"not among expected rates {sorted(valid_rates)} for "
                        f"{self._jurisdiction.tax_regime}"
                    ),
                    affected_fields=[key],
                ))

    # ------------------------------------------------------------------
    # 2c. Per-field validation rules
    # ------------------------------------------------------------------

    def _check_field_rules(
        self,
        all_fields: dict[str, FieldResult],
        result: ValidationResult,
    ) -> None:
        """
        Apply ``TaxFieldDefinition.validation_rules_json`` per field.

        Supported rules:
            - allowed_values: list of valid string values
            - min_value / max_value: numeric range
            - min_length / max_length: string length
            - regex: additional regex to check
        """
        for key, fr in all_fields.items():
            if not fr.extracted:
                continue
            fd = self._field_defs.get(key)
            if not fd or not fd.validation_rules_json:
                continue

            rules = fd.validation_rules_json
            value = (fr.normalized_value or fr.raw_value).strip()

            # Allowed values
            allowed = rules.get("allowed_values")
            if allowed and value.upper() not in [v.upper() for v in allowed]:
                result.checks.append(ValidationCheckResult(
                    check_type="FIELD_RULE",
                    status="FAIL",
                    severity="ERROR",
                    message=(
                        f"Field '{key}' value '{value}' not in allowed "
                        f"values: {allowed}"
                    ),
                    affected_fields=[key],
                ))

            # Numeric range
            min_val = rules.get("min_value")
            max_val = rules.get("max_value")
            if min_val is not None or max_val is not None:
                try:
                    num = float(value)
                    if min_val is not None and num < float(min_val):
                        result.checks.append(ValidationCheckResult(
                            check_type="FIELD_RULE",
                            status="FAIL",
                            severity="WARNING",
                            message=(
                                f"Field '{key}' value {num} is below "
                                f"minimum {min_val}"
                            ),
                            affected_fields=[key],
                        ))
                    if max_val is not None and num > float(max_val):
                        result.checks.append(ValidationCheckResult(
                            check_type="FIELD_RULE",
                            status="FAIL",
                            severity="WARNING",
                            message=(
                                f"Field '{key}' value {num} exceeds "
                                f"maximum {max_val}"
                            ),
                            affected_fields=[key],
                        ))
                except ValueError:
                    pass

            # String length
            min_len = rules.get("min_length")
            max_len = rules.get("max_length")
            if min_len is not None and len(value) < int(min_len):
                result.checks.append(ValidationCheckResult(
                    check_type="FIELD_RULE",
                    status="FAIL",
                    severity="WARNING",
                    message=(
                        f"Field '{key}' length {len(value)} is below "
                        f"minimum {min_len}"
                    ),
                    affected_fields=[key],
                ))
            if max_len is not None and len(value) > int(max_len):
                result.checks.append(ValidationCheckResult(
                    check_type="FIELD_RULE",
                    status="FAIL",
                    severity="WARNING",
                    message=(
                        f"Field '{key}' length {len(value)} exceeds "
                        f"maximum {max_len}"
                    ),
                    affected_fields=[key],
                ))

            # Additional regex
            rule_regex = rules.get("regex")
            if rule_regex:
                try:
                    if not re.fullmatch(rule_regex, value):
                        result.checks.append(ValidationCheckResult(
                            check_type="FIELD_RULE",
                            status="FAIL",
                            severity="WARNING",
                            message=(
                                f"Field '{key}' value '{value}' does not "
                                f"match validation pattern"
                            ),
                            affected_fields=[key],
                        ))
                except re.error:
                    logger.warning(
                        "Invalid validation regex for field %s: %s",
                        key, rule_regex,
                    )

    # ------------------------------------------------------------------
    # 2d. Header vs line-item consistency
    # ------------------------------------------------------------------

    def _check_amount_consistency(
        self,
        header_fields: dict[str, FieldResult],
        tax_fields: dict[str, FieldResult],
        result: ValidationResult,
    ) -> None:
        """
        Check that header totals are consistent with tax breakdowns.

        Verifies:
            - grand_total ≈ total_amount + total_tax_amount
            - Tax component totals align (jurisdiction-specific from config)
        """
        grand_total = self._get_decimal(header_fields, "grand_total")
        total_amount = self._get_decimal(header_fields, "total_amount")
        total_taxable = self._get_decimal(header_fields, "total_taxable_amount")

        # Try jurisdiction-specific total tax field names
        total_tax = self._get_decimal(header_fields, "total_tax_amount")

        # Check grand_total = total_amount + total_tax
        if grand_total is not None and total_amount is not None and total_tax is not None:
            expected = total_amount + total_tax
            diff = abs(grand_total - expected)
            tolerance = Decimal("0.02")  # rounding tolerance

            if diff > tolerance:
                result.checks.append(ValidationCheckResult(
                    check_type="AMOUNT_CONSISTENCY",
                    status="FAIL",
                    severity="WARNING",
                    message=(
                        f"Grand total ({grand_total}) does not equal "
                        f"total amount ({total_amount}) + total tax "
                        f"({total_tax}). Difference: {diff}"
                    ),
                    affected_fields=[
                        "grand_total", "total_amount", "total_tax_amount",
                    ],
                ))
            else:
                result.checks.append(ValidationCheckResult(
                    check_type="AMOUNT_CONSISTENCY",
                    status="PASS",
                    severity="INFO",
                    message="Grand total matches total amount + total tax",
                    affected_fields=[
                        "grand_total", "total_amount", "total_tax_amount",
                    ],
                ))

        # Jurisdiction-specific tax component consistency
        self._check_tax_component_totals(header_fields, tax_fields, result)

    def _check_tax_component_totals(
        self,
        header_fields: dict[str, FieldResult],
        tax_fields: dict[str, FieldResult],
        result: ValidationResult,
    ) -> None:
        """
        Check that individual tax component totals sum to total tax.

        Uses ``tax_components`` from jurisdiction config_json to discover
        which total_<component> fields to expect.
        """
        if not self._jurisdiction:
            return

        config = self._jurisdiction.config_json or {}
        components = config.get("tax_components", [])
        if not components:
            return

        all_fields = {**header_fields, **tax_fields}
        component_sum = Decimal("0")
        component_keys: list[str] = []
        found_any = False

        for comp in components:
            key = f"total_{comp.lower()}"
            val = self._get_decimal(all_fields, key)
            if val is not None:
                component_sum += val
                found_any = True
                component_keys.append(key)

        if not found_any:
            return

        # Compare sum of components against header total tax
        total_tax = (
            self._get_decimal(all_fields, "total_tax_amount")
            or self._get_decimal(all_fields, "total_vat_amount")
        )

        if total_tax is not None:
            diff = abs(total_tax - component_sum)
            tolerance = Decimal("0.02")

            if diff > tolerance:
                result.checks.append(ValidationCheckResult(
                    check_type="TAX_COMPONENT_CONSISTENCY",
                    status="FAIL",
                    severity="WARNING",
                    message=(
                        f"Sum of tax components ({component_sum}) does not "
                        f"match total tax amount ({total_tax}). "
                        f"Difference: {diff}"
                    ),
                    affected_fields=component_keys + ["total_tax_amount"],
                ))
            else:
                result.checks.append(ValidationCheckResult(
                    check_type="TAX_COMPONENT_CONSISTENCY",
                    status="PASS",
                    severity="INFO",
                    message="Tax component totals sum correctly",
                    affected_fields=component_keys,
                ))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_decimal(
        fields: dict[str, FieldResult],
        key: str,
    ) -> Optional[Decimal]:
        """Safely extract a Decimal value from a FieldResult dict."""
        fr = fields.get(key)
        if not fr or not fr.extracted:
            return None
        value = (fr.normalized_value or fr.raw_value).strip()
        if not value:
            return None
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
