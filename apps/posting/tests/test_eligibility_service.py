"""Tests for PostingEligibilityService -- checks whether an invoice can enter posting."""
from __future__ import annotations

import pytest
from decimal import Decimal
from datetime import date
from unittest.mock import MagicMock, patch

from apps.core.enums import InvoicePostingStatus, InvoiceStatus, PostingRunStatus


@pytest.fixture
def _mock_invoice():
    """Return a factory for mock Invoice objects with sensible defaults."""
    def _make(**overrides):
        defaults = {
            "pk": 1,
            "id": 1,
            "invoice_number": "INV-001",
            "invoice_date": date(2026, 1, 1),
            "currency": "SAR",
            "total_amount": Decimal("1000.00"),
            "raw_vendor_name": "Test Vendor",
            "vendor_id": None,
            "is_duplicate": False,
            "status": InvoiceStatus.RECONCILED,
        }
        defaults.update(overrides)
        inv = MagicMock(**defaults)
        inv.pk = defaults["pk"]
        inv.id = defaults["id"]
        return inv
    return _make


class TestEligibilityChecks:
    """Tests for PostingEligibilityService.check()."""

    @pytest.mark.django_db
    def test_invoice_not_found(self):
        """E-01: Non-existent invoice ID returns ineligible."""
        from apps.posting.services.eligibility_service import PostingEligibilityService

        result = PostingEligibilityService.check(999999)
        assert not result.eligible
        assert any("does not exist" in r for r in result.reasons)

    @pytest.mark.django_db
    def test_eligible_reconciled_invoice(self):
        """E-02: Reconciled invoice with approved extraction is eligible."""
        from apps.documents.models import Invoice
        from apps.posting.services.eligibility_service import PostingEligibilityService

        inv = Invoice.objects.create(
            invoice_number="INV-E02",
            invoice_date=date(2026, 1, 15),
            currency="SAR",
            total_amount=Decimal("5000.00"),
            raw_vendor_name="Acme Corp",
            status=InvoiceStatus.RECONCILED,
        )
        # Create an approved extraction
        from apps.documents.models import DocumentUpload
        upload = DocumentUpload.objects.create(
            original_filename="test.pdf",
            file="test.pdf",
        )
        from apps.extraction.models import ExtractionApproval
        from apps.core.enums import ExtractionApprovalStatus
        ExtractionApproval.objects.create(
            invoice=inv,
            status=ExtractionApprovalStatus.APPROVED,
        )

        result = PostingEligibilityService.check(inv.pk)
        assert result.eligible
        assert result.reasons == []

    @pytest.mark.django_db
    def test_wrong_status(self):
        """E-03: Invoice with UPLOADED status is ineligible."""
        from apps.documents.models import Invoice
        from apps.posting.services.eligibility_service import PostingEligibilityService
        from apps.core.enums import ExtractionApprovalStatus
        from apps.extraction.models import ExtractionApproval

        inv = Invoice.objects.create(
            invoice_number="INV-E03",
            invoice_date=date(2026, 1, 15),
            currency="SAR",
            total_amount=Decimal("1000.00"),
            raw_vendor_name="Test",
            status=InvoiceStatus.UPLOADED,
        )
        ExtractionApproval.objects.create(
            invoice=inv,
            status=ExtractionApprovalStatus.APPROVED,
        )
        result = PostingEligibilityService.check(inv.pk)
        assert not result.eligible
        assert any("status" in r.lower() for r in result.reasons)

    @pytest.mark.django_db
    def test_duplicate_invoice(self):
        """E-04: Duplicate-flagged invoice is ineligible."""
        from apps.documents.models import Invoice
        from apps.posting.services.eligibility_service import PostingEligibilityService
        from apps.core.enums import ExtractionApprovalStatus
        from apps.extraction.models import ExtractionApproval

        inv = Invoice.objects.create(
            invoice_number="INV-E04",
            invoice_date=date(2026, 1, 15),
            currency="SAR",
            total_amount=Decimal("1000.00"),
            raw_vendor_name="Test",
            status=InvoiceStatus.RECONCILED,
            is_duplicate=True,
        )
        ExtractionApproval.objects.create(
            invoice=inv,
            status=ExtractionApprovalStatus.APPROVED,
        )
        result = PostingEligibilityService.check(inv.pk)
        assert not result.eligible
        assert any("duplicate" in r.lower() for r in result.reasons)

    @pytest.mark.django_db
    def test_missing_invoice_number(self):
        """E-05: Missing invoice_number makes ineligible."""
        from apps.documents.models import Invoice
        from apps.posting.services.eligibility_service import PostingEligibilityService
        from apps.core.enums import ExtractionApprovalStatus
        from apps.extraction.models import ExtractionApproval

        inv = Invoice.objects.create(
            invoice_number="",
            invoice_date=date(2026, 1, 15),
            currency="SAR",
            total_amount=Decimal("1000.00"),
            raw_vendor_name="Test",
            status=InvoiceStatus.RECONCILED,
        )
        ExtractionApproval.objects.create(
            invoice=inv,
            status=ExtractionApprovalStatus.APPROVED,
        )
        result = PostingEligibilityService.check(inv.pk)
        assert not result.eligible
        assert any("invoice number" in r.lower() for r in result.reasons)

    @pytest.mark.django_db
    def test_missing_vendor_info(self):
        """E-06: No vendor name and no vendor FK => ineligible."""
        from apps.documents.models import Invoice
        from apps.posting.services.eligibility_service import PostingEligibilityService
        from apps.core.enums import ExtractionApprovalStatus
        from apps.extraction.models import ExtractionApproval

        inv = Invoice.objects.create(
            invoice_number="INV-E06",
            invoice_date=date(2026, 1, 15),
            currency="SAR",
            total_amount=Decimal("1000.00"),
            raw_vendor_name="",
            vendor=None,
            status=InvoiceStatus.RECONCILED,
        )
        ExtractionApproval.objects.create(
            invoice=inv,
            status=ExtractionApprovalStatus.APPROVED,
        )
        result = PostingEligibilityService.check(inv.pk)
        assert not result.eligible
        assert any("vendor" in r.lower() for r in result.reasons)

    @pytest.mark.django_db
    def test_no_extraction_approval(self):
        """E-07: Invoice without ExtractionApproval is ineligible."""
        from apps.documents.models import Invoice
        from apps.posting.services.eligibility_service import PostingEligibilityService

        inv = Invoice.objects.create(
            invoice_number="INV-E07",
            invoice_date=date(2026, 1, 15),
            currency="SAR",
            total_amount=Decimal("1000.00"),
            raw_vendor_name="Test",
            status=InvoiceStatus.RECONCILED,
        )
        result = PostingEligibilityService.check(inv.pk)
        assert not result.eligible
        assert any("extraction" in r.lower() or "approval" in r.lower() for r in result.reasons)

    @pytest.mark.django_db
    def test_already_posted(self):
        """E-08: Invoice with an existing POSTED InvoicePosting is ineligible."""
        from apps.documents.models import Invoice
        from apps.posting.models import InvoicePosting
        from apps.posting.services.eligibility_service import PostingEligibilityService
        from apps.core.enums import ExtractionApprovalStatus
        from apps.extraction.models import ExtractionApproval

        inv = Invoice.objects.create(
            invoice_number="INV-E08",
            invoice_date=date(2026, 1, 15),
            currency="SAR",
            total_amount=Decimal("1000.00"),
            raw_vendor_name="Test",
            status=InvoiceStatus.RECONCILED,
        )
        ExtractionApproval.objects.create(
            invoice=inv,
            status=ExtractionApprovalStatus.APPROVED,
        )
        InvoicePosting.objects.create(
            invoice=inv,
            status=InvoicePostingStatus.POSTED,
        )
        result = PostingEligibilityService.check(inv.pk)
        assert not result.eligible
        assert any("already posted" in r.lower() for r in result.reasons)

    @pytest.mark.django_db
    def test_active_running_posting_run(self):
        """E-09: Invoice with a RUNNING PostingRun blocks new posting."""
        from apps.documents.models import Invoice
        from apps.posting_core.models import PostingRun
        from apps.posting.services.eligibility_service import PostingEligibilityService
        from apps.core.enums import ExtractionApprovalStatus
        from apps.extraction.models import ExtractionApproval

        inv = Invoice.objects.create(
            invoice_number="INV-E09",
            invoice_date=date(2026, 1, 15),
            currency="SAR",
            total_amount=Decimal("1000.00"),
            raw_vendor_name="Test",
            status=InvoiceStatus.RECONCILED,
        )
        ExtractionApproval.objects.create(
            invoice=inv,
            status=ExtractionApprovalStatus.APPROVED,
        )
        PostingRun.objects.create(
            invoice=inv,
            status=PostingRunStatus.RUNNING,
        )
        result = PostingEligibilityService.check(inv.pk)
        assert not result.eligible
        assert any("already in progress" in r.lower() for r in result.reasons)

    @pytest.mark.django_db
    def test_multiple_failures_accumulated(self):
        """E-10: Multiple reasons are returned when multiple checks fail."""
        from apps.documents.models import Invoice
        from apps.posting.services.eligibility_service import PostingEligibilityService

        inv = Invoice.objects.create(
            invoice_number="",
            invoice_date=None,
            currency="",
            total_amount=None,
            raw_vendor_name="",
            status=InvoiceStatus.UPLOADED,
            is_duplicate=True,
        )
        result = PostingEligibilityService.check(inv.pk)
        assert not result.eligible
        assert len(result.reasons) >= 3
