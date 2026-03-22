"""Validation service — checks an extracted / normalised invoice for completeness and integrity."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

from django.conf import settings

from apps.extraction.services.normalization_service import NormalizedInvoice

from apps.core.decorators import observed_service

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    field: str
    severity: str  # "error" | "warning"
    message: str


@dataclass
class ValidationResult:
    is_valid: bool = True
    issues: List[ValidationIssue] = field(default_factory=list)

    def add_error(self, fld: str, msg: str) -> None:
        self.issues.append(ValidationIssue(field=fld, severity="error", message=msg))
        self.is_valid = False

    def add_warning(self, fld: str, msg: str) -> None:
        self.issues.append(ValidationIssue(field=fld, severity="warning", message=msg))

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


class ValidationService:
    """Run validation rules on a NormalizedInvoice."""

    @observed_service("extraction.validate", entity_type="Invoice")
    def validate(self, inv: NormalizedInvoice) -> ValidationResult:
        result = ValidationResult()

        # Mandatory header fields
        if not inv.normalized_invoice_number:
            result.add_error("invoice_number", "Invoice number is missing")
        if not inv.vendor_name_normalized:
            result.add_error("vendor_name", "Vendor name is missing")
        if inv.total_amount is None:
            result.add_error("total_amount", "Total amount is missing or non-numeric")

        # Recommended but not blocking
        if not inv.normalized_po_number:
            result.add_warning("po_number", "PO number is missing — will require agent lookup")
        if inv.invoice_date is None:
            result.add_warning("invoice_date", "Invoice date could not be parsed")
        if inv.subtotal is None:
            result.add_warning("subtotal", "Subtotal is missing or non-numeric")

        # Line items
        if not inv.line_items:
            result.add_warning("line_items", "No line items extracted")

        for li in inv.line_items:
            prefix = f"line_{li.line_number}"
            if not li.description:
                result.add_warning(f"{prefix}.description", f"Line {li.line_number}: description missing")
            if li.quantity is None:
                result.add_warning(f"{prefix}.quantity", f"Line {li.line_number}: quantity missing")
            if li.unit_price is None:
                result.add_warning(f"{prefix}.unit_price", f"Line {li.line_number}: unit price missing")

        # Low extraction confidence
        threshold = getattr(settings, "EXTRACTION_CONFIDENCE_THRESHOLD", 0.75)
        if inv.confidence < threshold:
            result.add_warning(
                "extraction_confidence",
                f"Low extraction confidence ({inv.confidence:.2f} < {threshold})",
            )

        logger.info(
            "Validation complete: valid=%s errors=%d warnings=%d",
            result.is_valid, len(result.errors), len(result.warnings),
        )
        return result
