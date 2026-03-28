"""Validation service — checks an extracted / normalised invoice for completeness and integrity."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List

from django.conf import settings

from apps.extraction.services.normalization_service import NormalizedInvoice
from apps.extraction.services.confidence_scorer import ExtractionConfidenceScorer

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
    # Critical field validation — populated when field_confidence is available
    critical_failures: List[str] = field(default_factory=list)       # field names below confidence threshold
    field_review_flags: Dict[str, str] = field(default_factory=dict) # field -> reason string
    requires_review_override: bool = False  # True forces human approval regardless of confidence score

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

        # ── Deterministic confidence scoring ──────────────────────────
        # Replace LLM self-reported confidence with a score computed from
        # what was actually extracted: field coverage (50%), line-item
        # quality (30%), and cross-field consistency (20%).
        llm_confidence = inv.confidence  # preserve for audit
        breakdown = ExtractionConfidenceScorer.score(inv, llm_confidence=llm_confidence)
        inv.confidence = breakdown.overall

        if breakdown.overall != llm_confidence:
            result.add_warning(
                "confidence_recomputed",
                f"Confidence recomputed: LLM reported {llm_confidence:.2f}, "
                f"deterministic score {breakdown.overall:.2f} "
                f"(coverage={breakdown.field_coverage:.2f}, "
                f"lines={breakdown.line_item_quality:.2f}, "
                f"consistency={breakdown.consistency:.2f})",
            )

        if breakdown.penalties:
            result.add_warning(
                "confidence_penalties",
                f"Confidence penalties: {', '.join(breakdown.penalties)}",
            )

        # Low extraction confidence
        threshold = getattr(settings, "EXTRACTION_CONFIDENCE_THRESHOLD", 0.75)
        if inv.confidence < threshold:
            result.add_warning(
                "extraction_confidence",
                f"Low extraction confidence ({inv.confidence:.2f} < {threshold})",
            )

        # ── Critical field confidence check ───────────────────────────────────
        # When FieldConfidenceService has already populated inv.field_confidence,
        # check each critical field.  Any critical field with confidence < 0.6
        # forces human review regardless of the overall confidence score.
        fc = getattr(inv, "field_confidence", {}) or {}
        if fc:
            from apps.extraction.services.field_confidence_service import CRITICAL_FIELDS
            _CRIT_CONF_THRESHOLD = 0.60
            for cf in CRITICAL_FIELDS:
                score = fc.get("header", {}).get(cf)
                if score is not None and score < _CRIT_CONF_THRESHOLD:
                    result.critical_failures.append(cf)
                    reason = f"field_confidence={score:.2f} < {_CRIT_CONF_THRESHOLD}"
                    result.field_review_flags[cf] = reason
                    result.add_warning(
                        f"critical_field.{cf}",
                        f"Critical field '{cf}' has low confidence ({score:.2f}); human review required",
                    )
            if result.critical_failures:
                result.requires_review_override = True
                logger.info(
                    "Critical field confidence failures: %s — review override set",
                    result.critical_failures,
                )

        logger.info(
            "Validation complete: valid=%s errors=%d warnings=%d confidence=%.2f (llm=%.2f) "
            "critical_failures=%s review_override=%s",
            result.is_valid, len(result.errors), len(result.warnings),
            inv.confidence, llm_confidence,
            result.critical_failures, result.requires_review_override,
        )
        return result
