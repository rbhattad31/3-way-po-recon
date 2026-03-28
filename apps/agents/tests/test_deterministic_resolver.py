"""
Tests for DeterministicResolver — pure unit tests (no DB for rule logic).

Rule priority (highest first, from source):
  0. Prior AUTO_CLOSE recommendation with confidence >= 0.80 → AUTO_CLOSE
  1. EXTRACTION_LOW_CONFIDENCE exception → REPROCESS_EXTRACTION
  2. VENDOR_MISMATCH → SEND_TO_VENDOR_CLARIFICATION
  3. GRN / receipt exception types → SEND_TO_PROCUREMENT
  4. 3+ independent issue categories + HIGH severity → ESCALATE_TO_MANAGER
     (QTY_MISMATCH + PRICE_MISMATCH + AMOUNT_MISMATCH + TAX_MISMATCH = ONE category)
  5. Default → SEND_TO_AP_REVIEW
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from apps.agents.services.deterministic_resolver import DeterministicResolver
from apps.core.enums import ExceptionSeverity, ExceptionType, RecommendationType


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_result(match_status="REQUIRES_REVIEW", mode="THREE_WAY",
                grn_available=False, grn_fully_received=False):
    result = MagicMock()
    result.match_status = match_status
    result.reconciliation_mode = mode
    result.grn_available = grn_available
    result.grn_fully_received = grn_fully_received
    result.pk = 1
    result.invoice = MagicMock()
    result.invoice.pk = 1
    result.invoice.invoice_number = "INV-001"
    result.invoice.total_amount = 1000
    result.invoice.currency = "SAR"
    result.invoice.vendor = MagicMock()
    result.invoice.vendor.name = "Test Vendor"
    result.invoice.raw_vendor_name = "Test Vendor"
    result.purchase_order = MagicMock()
    result.purchase_order.po_number = "PO-001"
    return result


def make_exc(exc_type, severity="MEDIUM", resolved=False, message="Test exception"):
    return {
        "exception_type": exc_type,
        "severity": severity,
        "resolved": resolved,
        "message": message,
    }


def resolve(result, exceptions, prior_recommendation=None, prior_confidence=0.0):
    from apps.agents.services.deterministic_resolver import DeterministicResolver
    # resolve() is an instance method, not a classmethod
    return DeterministicResolver().resolve(
        result=result,
        exceptions=exceptions,
        prior_recommendation=prior_recommendation,
        prior_confidence=prior_confidence,
    )


# ─── Priority 0: Prior AUTO_CLOSE ─────────────────────────────────────────────

class TestPriorAutoClose:
    def test_prior_auto_close_with_high_confidence(self):
        """Prior AUTO_CLOSE recommendation with >= 0.80 + exceptions present -> AUTO_CLOSE.

        NOTE: When exceptions list is EMPTY the resolver returns early with
        SEND_TO_AP_REVIEW before checking prior recommendations. Prior AUTO_CLOSE
        is only respected when there are active exceptions to route.
        """
        result = make_result()
        # Must have at least one exception to avoid the empty-exception early return
        excs = [make_exc(ExceptionType.AMOUNT_MISMATCH)]
        resolution = resolve(
            result, excs,
            prior_recommendation=RecommendationType.AUTO_CLOSE,
            prior_confidence=0.85,
        )
        assert resolution.recommendation_type == RecommendationType.AUTO_CLOSE

    def test_prior_auto_close_with_low_confidence_not_applied(self):
        """Prior AUTO_CLOSE but confidence < 0.80 -> falls through to other rules."""
        result = make_result()
        excs = [make_exc(ExceptionType.AMOUNT_MISMATCH)]
        resolution = resolve(
            result, excs,
            prior_recommendation=RecommendationType.AUTO_CLOSE,
            prior_confidence=0.75,
        )
        # Confidence too low -> should NOT return AUTO_CLOSE
        assert resolution.recommendation_type != RecommendationType.AUTO_CLOSE

    def test_prior_non_auto_close_recommendation_ignored(self):
        """Prior SEND_TO_AP_REVIEW recommendation does not trigger priority 0."""
        result = make_result()
        excs = [make_exc(ExceptionType.EXTRACTION_LOW_CONFIDENCE)]
        resolution = resolve(
            result, excs,
            prior_recommendation=RecommendationType.SEND_TO_AP_REVIEW,
            prior_confidence=0.95,
        )
        assert resolution.recommendation_type == RecommendationType.REPROCESS_EXTRACTION


# ─── Priority 1: EXTRACTION_LOW_CONFIDENCE ───────────────────────────────────

class TestExtractionLowConfidence:
    def test_low_confidence_exception_reprocess(self):
        """EXTRACTION_LOW_CONFIDENCE → REPROCESS_EXTRACTION."""
        result = make_result()
        excs = [make_exc(ExceptionType.EXTRACTION_LOW_CONFIDENCE)]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.REPROCESS_EXTRACTION

    def test_low_confidence_takes_priority_over_vendor_mismatch(self):
        """P1 (EXTRACTION_LOW_CONFIDENCE) beats P2 (VENDOR_MISMATCH)."""
        result = make_result()
        excs = [
            make_exc(ExceptionType.EXTRACTION_LOW_CONFIDENCE),
            make_exc(ExceptionType.VENDOR_MISMATCH),
        ]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.REPROCESS_EXTRACTION


# ─── Priority 2: VENDOR_MISMATCH ─────────────────────────────────────────────

class TestVendorMismatch:
    def test_vendor_mismatch_vendor_clarification(self):
        """VENDOR_MISMATCH → SEND_TO_VENDOR_CLARIFICATION."""
        result = make_result()
        excs = [make_exc(ExceptionType.VENDOR_MISMATCH)]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.SEND_TO_VENDOR_CLARIFICATION

    def test_vendor_mismatch_takes_priority_over_grn(self):
        """P2 (VENDOR_MISMATCH) beats P3 (GRN issues)."""
        result = make_result()
        excs = [
            make_exc(ExceptionType.VENDOR_MISMATCH),
            make_exc(ExceptionType.GRN_NOT_FOUND),
        ]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.SEND_TO_VENDOR_CLARIFICATION


# ─── Priority 3: GRN / receipt issues ────────────────────────────────────────

class TestGRNReceiptIssues:
    def test_grn_not_found_procurement(self):
        """GRN_NOT_FOUND → SEND_TO_PROCUREMENT."""
        result = make_result()
        excs = [make_exc(ExceptionType.GRN_NOT_FOUND)]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.SEND_TO_PROCUREMENT

    def test_receipt_shortage_procurement(self):
        """RECEIPT_SHORTAGE → SEND_TO_PROCUREMENT."""
        result = make_result()
        excs = [make_exc(ExceptionType.RECEIPT_SHORTAGE)]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.SEND_TO_PROCUREMENT

    def test_over_receipt_procurement(self):
        """OVER_RECEIPT → SEND_TO_PROCUREMENT."""
        result = make_result()
        excs = [make_exc(ExceptionType.OVER_RECEIPT)]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.SEND_TO_PROCUREMENT

    def test_invoice_qty_exceeds_received_procurement(self):
        """INVOICE_QTY_EXCEEDS_RECEIVED → SEND_TO_PROCUREMENT."""
        result = make_result()
        excs = [make_exc(ExceptionType.INVOICE_QTY_EXCEEDS_RECEIVED)]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.SEND_TO_PROCUREMENT


# ─── Priority 4: Complexity-based escalation ─────────────────────────────────

class TestEscalation:
    def test_three_independent_categories_with_high_severity_escalates(self):
        """3+ independent issue categories + HIGH severity → ESCALATE_TO_MANAGER."""
        result = make_result()
        excs = [
            make_exc(ExceptionType.CURRENCY_MISMATCH, severity=ExceptionSeverity.HIGH),
            make_exc(ExceptionType.ITEM_MISMATCH, severity=ExceptionSeverity.HIGH),
            make_exc(ExceptionType.LOCATION_MISMATCH, severity=ExceptionSeverity.HIGH),
        ]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.ESCALATE_TO_MANAGER

    def test_numeric_mismatches_count_as_one_category(self):
        """QTY+PRICE+AMOUNT+TAX together count as ONE category — not 4."""
        result = make_result()
        excs = [
            make_exc(ExceptionType.QTY_MISMATCH, severity=ExceptionSeverity.HIGH),
            make_exc(ExceptionType.PRICE_MISMATCH, severity=ExceptionSeverity.HIGH),
            make_exc(ExceptionType.AMOUNT_MISMATCH, severity=ExceptionSeverity.HIGH),
            make_exc(ExceptionType.TAX_MISMATCH, severity=ExceptionSeverity.HIGH),
        ]
        resolution = resolve(result, excs)
        # Only 1 category (numeric mismatches) → NOT escalated
        assert resolution.recommendation_type != RecommendationType.ESCALATE_TO_MANAGER

    def test_two_categories_with_high_severity_not_escalated(self):
        """Only 2 independent categories + HIGH → NOT escalated (need 3+)."""
        result = make_result()
        excs = [
            make_exc(ExceptionType.CURRENCY_MISMATCH, severity=ExceptionSeverity.HIGH),
            make_exc(ExceptionType.ITEM_MISMATCH, severity=ExceptionSeverity.HIGH),
        ]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type != RecommendationType.ESCALATE_TO_MANAGER

    def test_three_categories_without_high_severity_not_escalated(self):
        """3+ categories but no HIGH severity → NOT escalated."""
        result = make_result()
        excs = [
            make_exc(ExceptionType.CURRENCY_MISMATCH, severity=ExceptionSeverity.MEDIUM),
            make_exc(ExceptionType.ITEM_MISMATCH, severity=ExceptionSeverity.MEDIUM),
            make_exc(ExceptionType.LOCATION_MISMATCH, severity=ExceptionSeverity.MEDIUM),
        ]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type != RecommendationType.ESCALATE_TO_MANAGER


# ─── Priority 5: Default fallback ────────────────────────────────────────────

class TestDefaultFallback:
    def test_no_special_exceptions_defaults_to_ap_review(self):
        """Amount mismatch alone → SEND_TO_AP_REVIEW (default)."""
        result = make_result()
        excs = [make_exc(ExceptionType.AMOUNT_MISMATCH, severity=ExceptionSeverity.MEDIUM)]
        resolution = resolve(result, excs)
        assert resolution.recommendation_type == RecommendationType.SEND_TO_AP_REVIEW

    def test_empty_exceptions_defaults_to_ap_review(self):
        """No exceptions at all → SEND_TO_AP_REVIEW."""
        result = make_result()
        resolution = resolve(result, [])
        assert resolution.recommendation_type == RecommendationType.SEND_TO_AP_REVIEW


# ─── Output shape ─────────────────────────────────────────────────────────────

class TestOutputShape:
    def test_resolution_has_required_fields(self):
        result = make_result()
        resolution = resolve(result, [])
        assert hasattr(resolution, "recommendation_type")
        assert hasattr(resolution, "confidence")
        assert hasattr(resolution, "reasoning")
        assert hasattr(resolution, "evidence")
        assert hasattr(resolution, "case_summary")
        assert isinstance(resolution.confidence, float)
        assert 0.0 <= resolution.confidence <= 1.0

    def test_evidence_contains_exception_count(self):
        result = make_result()
        excs = [make_exc(ExceptionType.AMOUNT_MISMATCH)]
        resolution = resolve(result, excs)
        assert resolution.evidence.get("exception_count") == 1
