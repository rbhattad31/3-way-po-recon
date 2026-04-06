"""Tests for PostingValidationService -- validates posting proposals."""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date
from typing import Dict, List, Optional
from unittest.mock import MagicMock


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
    source_description: str = "Consulting"
    mapped_description: str = "IT Consulting"
    source_category: str = ""
    mapped_category: str = ""
    erp_item_code: str = "SRV-001"
    erp_line_type: str = "SERVICE"
    quantity: Decimal = Decimal("1.00")
    unit_price: Decimal = Decimal("850.00")
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


def _mock_invoice(**overrides):
    defaults = {
        "invoice_number": "INV-001",
        "invoice_date": date(2026, 1, 15),
        "currency": "SAR",
        "total_amount": Decimal("1000.00"),
        "tax_amount": Decimal("150.00"),
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


class TestVendorResolved:
    """V-01 to V-02: Vendor code presence check."""

    def test_vendor_resolved_passes(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal()
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        vendor_issues = [i for i in issues if i["check_type"] == "vendor_required"]
        assert vendor_issues == []

    def test_vendor_unresolved_fails(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal(header=_MockHeader(vendor_code=""))
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        vendor_issues = [i for i in issues if i["check_type"] == "vendor_required"]
        assert len(vendor_issues) == 1
        assert vendor_issues[0]["severity"] == "ERROR"


class TestHeaderCompleteness:
    """H-01 to H-04: Required header fields check."""

    @pytest.mark.parametrize("field_name", [
        "invoice_number", "invoice_date", "currency", "total_amount",
    ])
    def test_missing_header_field(self, field_name):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal()
        inv = _mock_invoice(**{field_name: None if field_name != "invoice_number" else ""})
        issues = PostingValidationService.validate(proposal, inv)
        header_issues = [i for i in issues if i["check_type"] == "header_required" and i["field_code"] == field_name]
        assert len(header_issues) == 1

    def test_all_header_fields_present(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal()
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        header_issues = [i for i in issues if i["check_type"] == "header_required"]
        assert header_issues == []


class TestLinesExist:
    """L-01 to L-02: At least one line is required."""

    def test_no_lines_error(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal(lines=[])
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        line_issues = [i for i in issues if i["check_type"] == "lines_required"]
        assert len(line_issues) == 1
        assert line_issues[0]["severity"] == "ERROR"

    def test_with_lines_passes(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal()
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        line_issues = [i for i in issues if i["check_type"] == "lines_required"]
        assert line_issues == []


class TestLineTotalConsistency:
    """LT-01 to LT-03: Line totals vs invoice subtotal check."""

    def test_consistent_totals(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal()
        inv = _mock_invoice(total_amount=Decimal("1000.00"), tax_amount=Decimal("150.00"))
        issues = PostingValidationService.validate(proposal, inv)
        total_issues = [i for i in issues if i["check_type"] == "line_total_consistency"]
        assert total_issues == []

    def test_inconsistent_totals(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        line = _MockLine(line_amount=Decimal("500.00"))
        proposal = _MockProposal(lines=[line])
        inv = _mock_invoice(total_amount=Decimal("1000.00"), tax_amount=Decimal("150.00"))
        issues = PostingValidationService.validate(proposal, inv)
        total_issues = [i for i in issues if i["check_type"] == "line_total_consistency"]
        assert len(total_issues) == 1
        assert total_issues[0]["severity"] == "WARNING"

    def test_none_total_skipped(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal()
        inv = _mock_invoice(total_amount=None)
        issues = PostingValidationService.validate(proposal, inv)
        total_issues = [i for i in issues if i["check_type"] == "line_total_consistency"]
        assert total_issues == []


class TestLineCompleteness:
    """LC-01 to LC-03: Per-line completeness checks."""

    def test_unresolved_item_low_confidence(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        line = _MockLine(erp_item_code="", confidence=0.3)
        proposal = _MockProposal(lines=[line])
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        item_issues = [i for i in issues if i["check_type"] == "item_mapping_incomplete"]
        assert len(item_issues) == 1
        assert item_issues[0]["severity"] == "WARNING"

    def test_unresolved_item_high_confidence_no_warning(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        line = _MockLine(erp_item_code="", confidence=0.8)
        proposal = _MockProposal(lines=[line])
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        item_issues = [i for i in issues if i["check_type"] == "item_mapping_incomplete"]
        assert item_issues == []

    def test_missing_tax_code(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        line = _MockLine(tax_code="")
        proposal = _MockProposal(lines=[line])
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        tax_issues = [i for i in issues if i["check_type"] == "tax_code_missing"]
        assert len(tax_issues) == 1
        assert tax_issues[0]["severity"] == "INFO"

    def test_complete_line_no_issues(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal()
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        completeness_issues = [
            i for i in issues
            if i["check_type"] in ("item_mapping_incomplete", "tax_code_missing")
        ]
        assert completeness_issues == []


class TestReferenceFreshness:
    """RF-01 to RF-02: Reference batch freshness check."""

    @pytest.mark.django_db
    def test_stale_batch_warning(self):
        from django.utils import timezone
        from datetime import timedelta
        from apps.posting_core.models import ERPReferenceImportBatch
        from apps.core.enums import ERPReferenceBatchType, ERPReferenceBatchStatus
        from apps.posting_core.services.posting_validation import PostingValidationService

        batch = ERPReferenceImportBatch.objects.create(
            batch_type=ERPReferenceBatchType.VENDOR,
            source_file_name="vendors.xlsx",
            status=ERPReferenceBatchStatus.COMPLETED,
        )
        # Force imported_at to 30 days ago
        ERPReferenceImportBatch.objects.filter(pk=batch.pk).update(
            imported_at=timezone.now() - timedelta(days=30),
        )
        batch.refresh_from_db()

        proposal = _MockProposal(batch_refs={"VENDOR": batch.pk})
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        stale_issues = [i for i in issues if i["check_type"] == "reference_staleness"]
        assert len(stale_issues) == 1
        assert stale_issues[0]["severity"] == "WARNING"

    def test_no_batch_refs_clean(self):
        from apps.posting_core.services.posting_validation import PostingValidationService
        proposal = _MockProposal(batch_refs={})
        issues = PostingValidationService.validate(proposal, _mock_invoice())
        stale_issues = [i for i in issues if i["check_type"] == "reference_staleness"]
        assert stale_issues == []
