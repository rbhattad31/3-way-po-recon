"""Tests for PostingConfidenceService -- calculates confidence dimensions."""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional


# ---------- lightweight data classes mirroring production code ----------

@dataclass
class _MockHeader:
    vendor_code: str = "V001"
    vendor_name: str = "Test Vendor"
    invoice_number: str = "INV-001"
    invoice_date: str = "2026-01-15"
    currency: str = "SAR"
    total_amount: Decimal = Decimal("1000.00")
    tax_amount: Decimal = Decimal("150.00")
    subtotal: Decimal = Decimal("850.00")
    po_number: str = ""
    vendor_confidence: float = 0.95
    vendor_source: str = "VENDOR_REF"
    batch_refs: Dict = field(default_factory=dict)


@dataclass
class _MockLine:
    line_index: int = 0
    invoice_line_item_id: Optional[int] = None
    source_description: str = "Item"
    mapped_description: str = "Item"
    source_category: str = ""
    mapped_category: str = ""
    erp_item_code: str = "ITM-001"
    erp_line_type: str = "MATERIAL"
    quantity: Decimal = Decimal("10.00")
    unit_price: Decimal = Decimal("85.00")
    line_amount: Decimal = Decimal("850.00")
    tax_code: str = "VAT15"
    cost_center: str = "CC100"
    gl_account: str = ""
    uom: str = "EA"
    confidence: float = 0.90
    item_source: str = "ITEM_REF"
    tax_source: str = "TAX_REF"
    cost_center_source: str = "COST_CENTER_REF"


@dataclass
class _MockProposal:
    header: _MockHeader = field(default_factory=_MockHeader)
    lines: List[_MockLine] = field(default_factory=lambda: [_MockLine()])
    issues: List = field(default_factory=list)
    evidence: List = field(default_factory=list)
    batch_refs: Dict = field(default_factory=dict)


class TestWeights:
    """CW-01: Weight values consistency."""

    def test_weights_sum_to_one(self):
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        total = sum(PostingConfidenceService.WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_five_dimension_keys(self):
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        expected = {"header_completeness", "vendor_mapping", "line_mapping", "tax_completeness", "reference_freshness"}
        assert set(PostingConfidenceService.WEIGHTS.keys()) == expected


class TestCalculate:
    """CC-01 to CC-06: Overall confidence calculation."""

    def test_perfect_proposal(self):
        """CC-01: All fields present, high vendor confidence, all lines resolved."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        proposal = _MockProposal()
        score = PostingConfidenceService.calculate(proposal, [])
        assert score >= 0.90
        assert score <= 1.0

    def test_no_vendor_code_lowers_score(self):
        """CC-02: Missing vendor confidence drops vendor_mapping dimension."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        proposal = _MockProposal(header=_MockHeader(vendor_confidence=0.0))
        score = PostingConfidenceService.calculate(proposal, [])
        assert score < 0.80  # 25% weight at 0.0

    def test_no_lines_zero_line_mapping(self):
        """CC-03: No line items => line_mapping=0, tax_completeness=0."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        proposal = _MockProposal(lines=[])
        score = PostingConfidenceService.calculate(proposal, [])
        # line_mapping (0.30) + tax_completeness (0.15) = 0.45 worth zeroed
        assert score < 0.60

    def test_stale_references_degrade_freshness(self):
        """CC-04: Staleness issues degrade reference_freshness dimension."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        stale_issues = [
            {"check_type": "reference_staleness", "severity": "WARNING"},
            {"check_type": "reference_staleness", "severity": "WARNING"},
        ]
        proposal = _MockProposal()
        score_fresh = PostingConfidenceService.calculate(proposal, [])
        score_stale = PostingConfidenceService.calculate(proposal, stale_issues)
        assert score_stale < score_fresh

    def test_score_clamped_to_unit_interval(self):
        """CC-05: Score is always in [0.0, 1.0]."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        proposal = _MockProposal()
        score = PostingConfidenceService.calculate(proposal, [])
        assert 0.0 <= score <= 1.0

    def test_all_zero_dimensions(self):
        """CC-06: Worst-case proposal still returns >= 0.0."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        header = _MockHeader(
            vendor_code="", vendor_confidence=0.0,
            invoice_number="", invoice_date="", currency="", total_amount=None,
        )
        proposal = _MockProposal(header=header, lines=[])
        many_stale = [{"check_type": "reference_staleness"}] * 10
        score = PostingConfidenceService.calculate(proposal, many_stale)
        assert 0.0 <= score <= 0.10


class TestDimensions:
    """CD-01 to CD-05: Individual dimension calculations."""

    def test_header_completeness_full(self):
        """CD-01: All 4 header fields present => 1.0."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        proposal = _MockProposal()
        dims = PostingConfidenceService.dimensions(proposal, [])
        assert dims["header_completeness"] == 1.0

    def test_header_completeness_partial(self):
        """CD-02: 2 of 4 header fields present => 0.5."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        header = _MockHeader(invoice_number="", currency="")
        proposal = _MockProposal(header=header)
        dims = PostingConfidenceService.dimensions(proposal, [])
        assert dims["header_completeness"] == 0.5

    def test_vendor_mapping_passthrough(self):
        """CD-03: vendor_mapping equals vendor_confidence."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        proposal = _MockProposal(header=_MockHeader(vendor_confidence=0.72))
        dims = PostingConfidenceService.dimensions(proposal, [])
        assert dims["vendor_mapping"] == 0.72

    def test_line_mapping_average(self):
        """CD-04: line_mapping is average of line confidences."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        lines = [_MockLine(confidence=0.80), _MockLine(confidence=0.60, line_index=1)]
        proposal = _MockProposal(lines=lines)
        dims = PostingConfidenceService.dimensions(proposal, [])
        assert abs(dims["line_mapping"] - 0.70) < 1e-9

    def test_tax_completeness_ratio(self):
        """CD-05: tax_completeness = (lines with tax_code) / total lines."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        lines = [
            _MockLine(tax_code="VAT15"),
            _MockLine(tax_code="", line_index=1),
            _MockLine(tax_code="VAT5", line_index=2),
        ]
        proposal = _MockProposal(lines=lines)
        dims = PostingConfidenceService.dimensions(proposal, [])
        assert abs(dims["tax_completeness"] - 2 / 3) < 1e-9

    def test_reference_freshness_default(self):
        """CD-06: No staleness issues => freshness = 1.0."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        proposal = _MockProposal()
        dims = PostingConfidenceService.dimensions(proposal, [])
        assert dims["reference_freshness"] == 1.0

    def test_reference_freshness_degraded(self):
        """CD-07: Staleness degrades by 0.15 per issue, min 0.3."""
        from apps.posting_core.services.posting_confidence import PostingConfidenceService
        stale = [{"check_type": "reference_staleness"}] * 5
        proposal = _MockProposal()
        dims = PostingConfidenceService.dimensions(proposal, stale)
        assert dims["reference_freshness"] == 0.3  # max(0.3, 1 - 0.75)
