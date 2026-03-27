"""
ConfidenceScorer — Multi-dimensional confidence scoring for extractions.

Produces a ``ConfidenceBreakdown`` with per-category scores
(header, tax, line_item, jurisdiction) and a weighted overall score.

Scoring factors:
    - Per-field raw confidence (from deterministic regex / alias match / LLM)
    - Extraction method weight (regex > LLM > alias)
    - Jurisdiction resolution confidence
    - Field coverage (% of expected fields actually extracted)
    - Validation result pass rate (if available)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    from apps.extraction_core.services.extraction_service import (
        ConfidenceBreakdown,
        ExtractionExecutionResult,
        ExtractionTemplate,
        FieldResult,
    )
    from apps.extraction_core.services.validation_service import (
        ValidationResult,
    )

logger = logging.getLogger(__name__)

# ── Weights for method-based confidence adjustment ────────────────────
# Applied as a multiplier to the raw field confidence.
_METHOD_WEIGHTS: dict[str, float] = {
    "DETERMINISTIC": 1.0,
    "LLM": 0.90,
    "HYBRID": 0.95,
    "MANUAL": 1.0,
    "OCR": 0.85,
}

# ── Category weights when computing the weighted overall score ────────
_CATEGORY_WEIGHTS: dict[str, float] = {
    "header": 0.30,
    "tax": 0.30,
    "line_item": 0.20,
    "jurisdiction": 0.20,
}


class ConfidenceScorer:
    """Stateless scorer — all public methods are classmethods."""

    @classmethod
    def score(
        cls,
        result: "ExtractionExecutionResult",
        template: "ExtractionTemplate",
        validation: "ValidationResult | None" = None,
    ) -> "ConfidenceBreakdown":
        """
        Compute a full ``ConfidenceBreakdown`` for the extraction result.

        Returns the breakdown with ``requires_review`` and ``review_reasons``
        populated based on configurable thresholds.
        """
        from apps.extraction_core.services.extraction_service import (
            ConfidenceBreakdown,
        )

        header_conf = cls._category_confidence(
            list(result.header_fields.values()),
        )
        tax_conf = cls._category_confidence(
            list(result.tax_fields.values()),
        )
        line_item_conf = cls._line_item_confidence(result.line_items)
        jurisdiction_conf = cls._jurisdiction_confidence(result)

        # Validation penalty: reduce overall if many checks failed
        validation_factor = cls._validation_factor(validation)

        # Weighted overall
        overall = (
            _CATEGORY_WEIGHTS["header"] * header_conf
            + _CATEGORY_WEIGHTS["tax"] * tax_conf
            + _CATEGORY_WEIGHTS["line_item"] * line_item_conf
            + _CATEGORY_WEIGHTS["jurisdiction"] * jurisdiction_conf
        ) * validation_factor

        breakdown = ConfidenceBreakdown(
            overall=min(overall, 1.0),
            header=header_conf,
            tax=tax_conf,
            line_item=line_item_conf,
            jurisdiction=jurisdiction_conf,
        )

        # Also set the legacy overall_confidence on the result
        result.overall_confidence = breakdown.overall

        # Evaluate review routing
        cls._evaluate_review(breakdown, result, template)

        return breakdown

    # ── Internal scoring helpers ─────────────────────────────────────

    @classmethod
    def _category_confidence(
        cls,
        fields: list["FieldResult"],
    ) -> float:
        """
        Average method-weighted confidence for a group of fields.

        Returns 1.0 when the group is empty (nothing expected → nothing
        wrong).
        """
        extracted = [f for f in fields if f.extracted]
        if not extracted:
            # If there were expected fields but none extracted → 0
            return 0.0 if fields else 1.0

        total = sum(
            f.confidence * _METHOD_WEIGHTS.get(f.method, 0.85)
            for f in extracted
        )
        return total / len(extracted)

    @classmethod
    def _line_item_confidence(
        cls,
        line_items: list[dict[str, "FieldResult"]],
    ) -> float:
        """Average confidence across all line-item fields."""
        if not line_items:
            return 1.0  # Line items not yet parsed → neutral

        all_fields: list["FieldResult"] = []
        for row in line_items:
            all_fields.extend(row.values())
        return cls._category_confidence(all_fields)

    @classmethod
    def _jurisdiction_confidence(
        cls,
        result: "ExtractionExecutionResult",
    ) -> float:
        """Jurisdiction resolution confidence (0-1)."""
        return result.jurisdiction.confidence if result.jurisdiction else 0.0

    @classmethod
    def _validation_factor(
        cls,
        validation: "ValidationResult | None",
    ) -> float:
        """
        Factor in [0.7, 1.0] based on validation pass rate.

        No validation → 1.0 (no penalty).
        """
        if validation is None:
            return 1.0

        total_checks = len(validation.checks)
        if total_checks == 0:
            return 1.0

        passed = sum(1 for c in validation.checks if c.status == "PASS")
        pass_rate = passed / total_checks

        # Scale: 100% pass → 1.0, 0% pass → 0.7
        return 0.7 + 0.3 * pass_rate

    # ── Review routing evaluation ────────────────────────────────────

    @classmethod
    def _evaluate_review(
        cls,
        breakdown: "ConfidenceBreakdown",
        result: "ExtractionExecutionResult",
        template: "ExtractionTemplate",
    ) -> None:
        """
        Set ``requires_review`` and ``review_reasons`` on the breakdown.

        Uses the configured extraction confidence threshold.
        """
        threshold = getattr(
            settings,
            "EXTRACTION_CONFIDENCE_THRESHOLD",
            0.75,
        )
        reasons: list[str] = []

        if breakdown.overall < threshold:
            reasons.append(
                f"Overall confidence {breakdown.overall:.2%} "
                f"below threshold {threshold:.0%}"
            )

        if breakdown.header < threshold:
            reasons.append(
                f"Header confidence {breakdown.header:.2%} "
                f"below threshold {threshold:.0%}"
            )

        if breakdown.tax < threshold:
            reasons.append(
                f"Tax confidence {breakdown.tax:.2%} "
                f"below threshold {threshold:.0%}"
            )

        if breakdown.line_item < threshold and result.line_items:
            reasons.append(
                f"Line-item confidence {breakdown.line_item:.2%} "
                f"below threshold {threshold:.0%}"
            )

        if breakdown.jurisdiction < threshold:
            reasons.append(
                f"Jurisdiction confidence {breakdown.jurisdiction:.2%} "
                f"below threshold {threshold:.0%}"
            )

        # Mandatory coverage gap
        if template.mandatory_keys:
            all_results = {**result.header_fields, **result.tax_fields}
            missing = [
                k for k in template.mandatory_keys
                if k not in all_results or not all_results[k].extracted
            ]
            if missing:
                reasons.append(
                    f"Missing mandatory fields: {', '.join(missing)}"
                )

        breakdown.requires_review = bool(reasons)
        breakdown.review_reasons = reasons
