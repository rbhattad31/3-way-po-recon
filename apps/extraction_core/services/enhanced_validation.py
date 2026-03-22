"""
EnhancedValidationService — Country-aware extraction validation.

Validates:
- Required fields present
- Tax consistency (rates × base = tax amount)
- Header vs line totals
- Tax ID format per jurisdiction
- Data type compliance
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from apps.extraction_core.models import ExtractionIssue, ExtractionRun, TaxJurisdictionProfile
from apps.extraction_core.services.output_contract import (
    ExtractionOutputContract,
    FieldValue,
    WarningItem,
)

logger = logging.getLogger(__name__)


class EnhancedValidationService:
    """
    Country-aware extraction validation.

    Runs generic + jurisdiction-specific validation checks and
    persists ExtractionIssue records.
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

    def validate(
        self,
        output: ExtractionOutputContract,
        extraction_run: Optional[ExtractionRun] = None,
        mandatory_fields: Optional[set[str]] = None,
    ) -> list[dict]:
        """
        Run all validation checks against the extraction output.

        Returns list of issue dicts and optionally persists ExtractionIssue
        records to the extraction_run.
        """
        issues: list[dict] = []

        # 1. Required fields
        issues.extend(
            self._check_required_fields(output, mandatory_fields or set())
        )

        # 2. Tax consistency
        issues.extend(self._check_tax_consistency(output))

        # 3. Header vs line totals
        issues.extend(self._check_totals_consistency(output))

        # 4. Tax ID format
        issues.extend(self._check_tax_id_format(output))

        # 5. Country-specific checks
        issues.extend(self._country_specific_checks(output))

        # Persist issues
        if extraction_run and issues:
            self._persist_issues(extraction_run, issues)

        # Also add warnings to the output
        for issue in issues:
            output.warnings.append(WarningItem(
                code=issue.get("check_type", ""),
                message=issue["message"],
                field_code=issue.get("field_code", ""),
                severity=issue["severity"],
            ))

        return issues

    def _check_required_fields(
        self,
        output: ExtractionOutputContract,
        mandatory_fields: set[str],
    ) -> list[dict]:
        """Check that all mandatory fields have non-null values."""
        issues = []
        present_codes = output.get_all_field_codes()

        for field_code in mandatory_fields:
            fv = output.get_field_value(field_code)
            if not fv or fv.value is None or str(fv.value).strip() == "":
                issues.append({
                    "severity": "ERROR",
                    "field_code": field_code,
                    "check_type": "REQUIRED_FIELD",
                    "message": f"Required field '{field_code}' is missing or empty",
                })

        return issues

    def _check_tax_consistency(
        self,
        output: ExtractionOutputContract,
    ) -> list[dict]:
        """Check that tax amounts are consistent with rates and base amounts."""
        issues = []
        tax_fields = output.tax.tax_fields

        subtotal = self._get_decimal(output, "subtotal")
        total = self._get_decimal(output, "total_amount")
        tax_amount = self._get_decimal(output, "tax_amount")

        if subtotal and total and tax_amount:
            expected_total = subtotal + tax_amount
            if abs(expected_total - total) > Decimal("0.01"):
                issues.append({
                    "severity": "WARNING",
                    "field_code": "total_amount",
                    "check_type": "TAX_CONSISTENCY",
                    "message": (
                        f"Total ({total}) != Subtotal ({subtotal}) + "
                        f"Tax ({tax_amount}) = {expected_total}"
                    ),
                })

        return issues

    def _check_totals_consistency(
        self,
        output: ExtractionOutputContract,
    ) -> list[dict]:
        """Check header totals vs sum of line items."""
        issues = []

        if not output.line_items:
            return issues

        subtotal = self._get_decimal(output, "subtotal")
        if not subtotal:
            return issues

        line_sum = Decimal("0")
        for li in output.line_items:
            amount_fv = li.fields.get("line_amount") or li.fields.get("amount")
            if amount_fv and amount_fv.value:
                try:
                    line_sum += Decimal(str(amount_fv.value))
                except (InvalidOperation, ValueError):
                    pass

        if line_sum and abs(subtotal - line_sum) > Decimal("0.01"):
            issues.append({
                "severity": "WARNING",
                "field_code": "subtotal",
                "check_type": "TOTALS_CONSISTENCY",
                "message": (
                    f"Header subtotal ({subtotal}) != sum of line amounts ({line_sum})"
                ),
            })

        return issues

    def _check_tax_id_format(
        self,
        output: ExtractionOutputContract,
    ) -> list[dict]:
        """Validate tax ID format against jurisdiction regex."""
        issues = []
        if not self.jurisdiction_profile or not self.jurisdiction_profile.tax_id_regex:
            return issues

        pattern = self.jurisdiction_profile.tax_id_regex
        tax_id_fields = ["supplier_gstin", "buyer_gstin", "supplier_trn",
                         "buyer_trn", "supplier_vat_id", "buyer_vat_id",
                         "tax_id", "gstin"]

        for field_code in tax_id_fields:
            fv = output.get_field_value(field_code)
            if not fv or not fv.value:
                continue
            # Check supplier party fields too
            for party_data in (output.parties.supplier, output.parties.buyer):
                if field_code in party_data:
                    fv = party_data[field_code]
                    break

            if fv and fv.value and not re.match(pattern, str(fv.value)):
                issues.append({
                    "severity": "WARNING",
                    "field_code": field_code,
                    "check_type": "TAX_ID_FORMAT",
                    "message": (
                        f"Tax ID '{fv.value}' does not match expected format "
                        f"for {self.country_code}"
                    ),
                })

        return issues

    def _country_specific_checks(
        self,
        output: ExtractionOutputContract,
    ) -> list[dict]:
        """Run country-specific validation checks."""
        issues = []

        if self.regime_code == "GST":
            issues.extend(self._validate_gst(output))
        elif self.regime_code in ("VAT_UAE", "VAT_SA"):
            issues.extend(self._validate_vat(output))

        return issues

    def _validate_gst(self, output: ExtractionOutputContract) -> list[dict]:
        """India GST-specific validations."""
        issues = []
        tax_fields = output.tax.tax_fields

        cgst = self._get_tax_decimal(tax_fields, "cgst_amount")
        sgst = self._get_tax_decimal(tax_fields, "sgst_amount")
        igst = self._get_tax_decimal(tax_fields, "igst_amount")

        # GST: either CGST+SGST or IGST, not both
        if cgst and sgst and igst:
            if igst > 0 and (cgst > 0 or sgst > 0):
                issues.append({
                    "severity": "WARNING",
                    "field_code": "igst_amount",
                    "check_type": "GST_CONSISTENCY",
                    "message": "Both IGST and CGST/SGST are present — unusual for GST",
                })

        # CGST should equal SGST
        if cgst and sgst and abs(cgst - sgst) > Decimal("0.01"):
            issues.append({
                "severity": "WARNING",
                "field_code": "cgst_amount",
                "check_type": "GST_CONSISTENCY",
                "message": f"CGST ({cgst}) != SGST ({sgst}) — should be equal for intra-state",
            })

        return issues

    def _validate_vat(self, output: ExtractionOutputContract) -> list[dict]:
        """UAE/Saudi VAT-specific validations."""
        issues = []
        tax_fields = output.tax.tax_fields

        vat_amount = self._get_tax_decimal(tax_fields, "vat_amount")
        vat_rate = self._get_tax_decimal(tax_fields, "vat_rate")
        subtotal = self._get_decimal(output, "subtotal")

        if vat_amount and vat_rate and subtotal:
            expected_vat = subtotal * vat_rate / Decimal("100")
            if abs(vat_amount - expected_vat) > Decimal("0.50"):
                issues.append({
                    "severity": "WARNING",
                    "field_code": "vat_amount",
                    "check_type": "VAT_CONSISTENCY",
                    "message": (
                        f"VAT amount ({vat_amount}) differs from "
                        f"expected ({expected_vat}) based on rate {vat_rate}%"
                    ),
                })

        return issues

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_issues(
        self,
        extraction_run: ExtractionRun,
        issues: list[dict],
    ) -> None:
        """Bulk create ExtractionIssue records."""
        records = [
            ExtractionIssue(
                extraction_run=extraction_run,
                severity=issue["severity"],
                field_code=issue.get("field_code", ""),
                check_type=issue.get("check_type", ""),
                message=issue["message"],
                details_json=issue.get("details", {}),
            )
            for issue in issues
        ]
        if records:
            ExtractionIssue.objects.bulk_create(records)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_decimal(self, output: ExtractionOutputContract, field_code: str) -> Optional[Decimal]:
        """Get a decimal value from the output."""
        fv = output.get_field_value(field_code)
        if not fv or fv.value is None:
            return None
        try:
            return Decimal(str(fv.value))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _get_tax_decimal(tax_fields: dict[str, FieldValue], key: str) -> Optional[Decimal]:
        """Get a decimal value from tax fields."""
        fv = tax_fields.get(key)
        if not fv or fv.value is None:
            return None
        try:
            return Decimal(str(fv.value))
        except (InvalidOperation, ValueError):
            return None
