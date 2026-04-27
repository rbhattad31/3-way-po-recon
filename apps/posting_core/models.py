"""Posting Core models — authoritative execution records, ERP reference imports, mapping rules.

This module contains:
- PostingRun: authoritative posting execution record
- PostingFieldValue, PostingLineItem: resolved posting field values
- PostingIssue, PostingEvidence: validation outputs
- PostingApprovalRecord: governance mirror
- ERP Reference Import: ERPReferenceImportBatch + per-type reference models
- VendorAliasMapping, ItemAliasMapping: business-owned alias tables
- PostingRule: configurable mapping rules
"""
from django.conf import settings
from django.db import models

from apps.core.enums import (
    ERPReferenceBatchStatus,
    ERPReferenceBatchType,
    PostingApprovalAction,
    PostingFieldCategory,
    PostingFieldSourceType,
    PostingIssueSeverity,
    PostingReviewQueue,
    PostingRuleType,
    PostingRunStatus,
    PostingStage,
)
from apps.core.models import BaseModel, TimestampMixin


# ============================================================================
# Authoritative Posting Execution Record
# ============================================================================


class PostingRun(BaseModel):
    """Authoritative execution record for posting proposal / submission.

    Analogous to ExtractionRun in extraction_core — one per pipeline invocation.
    """

    invoice = models.ForeignKey(
        "documents.Invoice",
        on_delete=models.CASCADE,
        related_name="posting_runs",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    extraction_run = models.ForeignKey(
        "extraction_core.ExtractionRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posting_runs",
    )
    extraction_result = models.ForeignKey(
        "extraction.ExtractionResult",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posting_runs",
    )
    status = models.CharField(
        max_length=20,
        choices=PostingRunStatus.choices,
        default=PostingRunStatus.PENDING,
        db_index=True,
    )
    stage_code = models.CharField(
        max_length=30,
        choices=PostingStage.choices,
        blank=True,
        default="",
    )
    overall_confidence = models.FloatField(null=True, blank=True)
    requires_review = models.BooleanField(default=False)
    review_queue = models.CharField(
        max_length=30,
        choices=PostingReviewQueue.choices,
        blank=True,
        default="",
    )
    review_reasons_json = models.JSONField(default=list, blank=True)

    # Snapshots
    source_invoice_snapshot_json = models.JSONField(default=dict, blank=True)
    normalized_posting_data_json = models.JSONField(default=dict, blank=True)
    posting_payload_json = models.JSONField(default=dict, blank=True)
    response_json = models.JSONField(default=dict, blank=True)
    erp_source_metadata_json = models.JSONField(
        default=dict, blank=True,
        help_text="ERP resolution source metadata (connector, fallback, confidence per field)",
    )

    # Error tracking
    error_code = models.CharField(max_length=50, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    langfuse_trace_id = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="Langfuse root trace ID for this posting run",
    )

    class Meta:
        db_table = "posting_core_posting_run"
        ordering = ["-created_at"]
        verbose_name = "Posting Run"
        verbose_name_plural = "Posting Runs"
        indexes = [
            models.Index(fields=["invoice", "status"], name="idx_pr_inv_status"),
            models.Index(fields=["status", "created_at"], name="idx_pr_status_date"),
        ]

    def __str__(self) -> str:
        return f"PostingRun {self.pk} — Invoice {self.invoice_id} [{self.status}]"


# ============================================================================
# Posting Field Values & Line Items
# ============================================================================


class PostingFieldValue(TimestampMixin):
    """Resolved posting field value with provenance."""

    posting_run = models.ForeignKey(
        PostingRun,
        on_delete=models.CASCADE,
        related_name="field_values",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    field_code = models.CharField(max_length=100)
    category = models.CharField(
        max_length=20,
        choices=PostingFieldCategory.choices,
    )
    source_type = models.CharField(
        max_length=20,
        choices=PostingFieldSourceType.choices,
    )
    source_ref = models.CharField(max_length=255, blank=True, default="")
    value = models.TextField(default="")
    normalized_value = models.TextField(blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)
    line_item_index = models.PositiveIntegerField(null=True, blank=True)
    is_valid = models.BooleanField(default=True)
    validation_message = models.TextField(blank=True, default="")

    class Meta:
        db_table = "posting_core_field_value"
        ordering = ["posting_run", "category", "field_code"]
        verbose_name = "Posting Field Value"
        verbose_name_plural = "Posting Field Values"

    def __str__(self) -> str:
        return f"FieldValue {self.field_code}={self.value[:50]}"


class PostingLineItem(TimestampMixin):
    """Resolved posting line item with mapped ERP values."""

    posting_run = models.ForeignKey(
        PostingRun,
        on_delete=models.CASCADE,
        related_name="line_items",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    line_index = models.PositiveIntegerField()
    invoice_line_item = models.ForeignKey(
        "documents.InvoiceLineItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posting_lines",
    )
    source_description = models.TextField(default="")
    mapped_description = models.TextField(blank=True, default="")
    source_category = models.CharField(max_length=100, blank=True, default="")
    mapped_category = models.CharField(max_length=100, blank=True, default="")
    erp_item_code = models.CharField(max_length=100, blank=True, default="")
    erp_line_type = models.CharField(max_length=50, blank=True, default="")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    line_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    tax_code = models.CharField(max_length=50, blank=True, default="")
    cost_center = models.CharField(max_length=50, blank=True, default="")
    gl_account = models.CharField(max_length=50, blank=True, default="")
    uom = models.CharField(max_length=20, blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)
    is_valid = models.BooleanField(default=True)
    validation_message = models.TextField(blank=True, default="")
    source_json = models.JSONField(default=dict, blank=True)
    resolved_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "posting_core_line_item"
        ordering = ["posting_run", "line_index"]
        verbose_name = "Posting Line Item"
        verbose_name_plural = "Posting Line Items"

    def __str__(self) -> str:
        return f"PostingLine {self.line_index}: {self.source_description[:50]}"


# ============================================================================
# Posting Issues & Evidence
# ============================================================================


class PostingIssue(TimestampMixin):
    """Validation issue found during posting pipeline."""

    posting_run = models.ForeignKey(
        PostingRun,
        on_delete=models.CASCADE,
        related_name="issues",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    severity = models.CharField(
        max_length=10,
        choices=PostingIssueSeverity.choices,
    )
    field_code = models.CharField(max_length=100)
    check_type = models.CharField(max_length=100)
    message = models.TextField()
    details_json = models.JSONField(default=dict, blank=True)
    line_item_index = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "posting_core_issue"
        ordering = ["posting_run", "severity"]
        verbose_name = "Posting Issue"
        verbose_name_plural = "Posting Issues"

    def __str__(self) -> str:
        return f"Issue [{self.severity}] {self.field_code}: {self.message[:60]}"


class PostingEvidence(TimestampMixin):
    """Evidence trail for posting field resolution decisions."""

    posting_run = models.ForeignKey(
        PostingRun,
        on_delete=models.CASCADE,
        related_name="evidence",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    field_code = models.CharField(max_length=100)
    source_type = models.CharField(
        max_length=20,
        choices=PostingFieldSourceType.choices,
    )
    source_path = models.CharField(max_length=500, blank=True, default="")
    snippet = models.TextField(blank=True, default="")
    confidence = models.FloatField(null=True, blank=True)
    line_item_index = models.PositiveIntegerField(null=True, blank=True)
    details_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "posting_core_evidence"
        ordering = ["posting_run", "field_code"]
        verbose_name = "Posting Evidence"
        verbose_name_plural = "Posting Evidence"

    def __str__(self) -> str:
        return f"Evidence {self.field_code} [{self.source_type}]"


# ============================================================================
# Posting Approval Record (governance mirror)
# ============================================================================


class PostingApprovalRecord(TimestampMixin):
    """Governance mirror for posting decisions.

    Written ONLY by PostingGovernanceTrailService — never directly by views.
    """

    posting_run = models.OneToOneField(
        PostingRun,
        on_delete=models.CASCADE,
        related_name="approval_record",
    )
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    action = models.CharField(
        max_length=20,
        choices=PostingApprovalAction.choices,
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posting_approval_decisions",
    )
    comments = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "posting_core_approval_record"
        verbose_name = "Posting Approval Record"
        verbose_name_plural = "Posting Approval Records"

    def __str__(self) -> str:
        return f"Approval {self.action} for PostingRun {self.posting_run_id}"


# ============================================================================
# ERP Reference Import
# ============================================================================


class ERPReferenceImportBatch(BaseModel):
    """Represents one imported Excel reference batch."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    batch_type = models.CharField(
        max_length=20,
        choices=ERPReferenceBatchType.choices,
        db_index=True,
    )
    source_file_name = models.CharField(max_length=500)
    source_file_path = models.CharField(max_length=1000, blank=True, default="")
    source_as_of = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the source ERP data was exported",
    )
    imported_at = models.DateTimeField(auto_now_add=True)
    row_count = models.PositiveIntegerField(default=0)
    valid_row_count = models.PositiveIntegerField(default=0)
    invalid_row_count = models.PositiveIntegerField(default=0)
    checksum = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=ERPReferenceBatchStatus.choices,
        default=ERPReferenceBatchStatus.PENDING,
        db_index=True,
    )
    error_summary = models.TextField(blank=True, default="")
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="erp_import_batches",
    )
    metadata_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "posting_core_erp_import_batch"
        ordering = ["-imported_at"]
        verbose_name = "ERP Reference Import Batch"
        verbose_name_plural = "ERP Reference Import Batches"

    def __str__(self) -> str:
        return f"Batch {self.pk} [{self.batch_type}] — {self.source_file_name}"


class ERPVendorReference(TimestampMixin):
    """Normalized vendor reference imported from ERP Excel export."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    batch = models.ForeignKey(
        ERPReferenceImportBatch,
        on_delete=models.CASCADE,
        related_name="vendor_refs",
    )
    vendor_code = models.CharField(max_length=50, db_index=True)
    vendor_name = models.CharField(max_length=500)
    normalized_vendor_name = models.CharField(max_length=500, db_index=True)
    vendor_group = models.CharField(max_length=100, blank=True, default="")
    country_code = models.CharField(max_length=3, blank=True, default="")
    is_active = models.BooleanField(default=True)
    payment_terms = models.CharField(max_length=100, blank=True, default="")
    currency = models.CharField(max_length=10, blank=True, default="")
    raw_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "posting_core_erp_vendor_ref"
        ordering = ["vendor_code"]
        verbose_name = "ERP Vendor Reference"
        verbose_name_plural = "ERP Vendor References"
        indexes = [
            models.Index(fields=["vendor_code"], name="idx_vref_code"),
            models.Index(fields=["normalized_vendor_name"], name="idx_vref_norm_name"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "vendor_code"],
                name="uq_erp_vendor_ref_tenant_code",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.vendor_code} — {self.vendor_name}"


class ERPItemReference(TimestampMixin):
    """Normalized item/service reference imported from ERP Excel export."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    batch = models.ForeignKey(
        ERPReferenceImportBatch,
        on_delete=models.CASCADE,
        related_name="item_refs",
    )
    item_code = models.CharField(max_length=100, db_index=True)
    item_name = models.CharField(max_length=500)
    normalized_item_name = models.CharField(max_length=500, db_index=True)
    item_type = models.CharField(max_length=50, blank=True, default="")
    category = models.CharField(max_length=100, blank=True, default="")
    uom = models.CharField(max_length=20, blank=True, default="")
    tax_code = models.CharField(max_length=50, blank=True, default="")
    is_active = models.BooleanField(default=True)
    raw_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "posting_core_erp_item_ref"
        ordering = ["item_code"]
        verbose_name = "ERP Item Reference"
        verbose_name_plural = "ERP Item References"
        indexes = [
            models.Index(fields=["item_code"], name="idx_iref_code"),
            models.Index(fields=["normalized_item_name"], name="idx_iref_norm_name"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "item_code"],
                name="uq_erp_item_ref_tenant_code",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.item_code} — {self.item_name}"


class ERPTaxCodeReference(TimestampMixin):
    """Normalized tax code reference imported from ERP Excel export."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    batch = models.ForeignKey(
        ERPReferenceImportBatch,
        on_delete=models.CASCADE,
        related_name="tax_refs",
    )
    tax_code = models.CharField(max_length=50, db_index=True)
    tax_label = models.CharField(max_length=200)
    country_code = models.CharField(max_length=3, blank=True, default="")
    rate = models.DecimalField(max_digits=6, decimal_places=4, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    raw_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "posting_core_erp_tax_ref"
        ordering = ["tax_code"]
        verbose_name = "ERP Tax Code Reference"
        verbose_name_plural = "ERP Tax Code References"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "tax_code"],
                name="uq_erp_tax_ref_tenant_code",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tax_code} — {self.tax_label}"


class ERPCostCenterReference(TimestampMixin):
    """Normalized cost center reference imported from ERP Excel export."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    batch = models.ForeignKey(
        ERPReferenceImportBatch,
        on_delete=models.CASCADE,
        related_name="cost_center_refs",
    )
    cost_center_code = models.CharField(max_length=50, db_index=True)
    cost_center_name = models.CharField(max_length=200)
    department = models.CharField(max_length=100, blank=True, default="")
    business_unit = models.CharField(max_length=100, blank=True, default="")
    is_active = models.BooleanField(default=True)
    raw_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "posting_core_erp_cost_center_ref"
        ordering = ["cost_center_code"]
        verbose_name = "ERP Cost Center Reference"
        verbose_name_plural = "ERP Cost Center References"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "cost_center_code"],
                name="uq_erp_cc_ref_tenant_code",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.cost_center_code} — {self.cost_center_name}"


class ERPPOReference(TimestampMixin):
    """Normalized open PO reference imported from ERP Excel export."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    batch = models.ForeignKey(
        ERPReferenceImportBatch,
        on_delete=models.CASCADE,
        related_name="po_refs",
    )
    po_number = models.CharField(max_length=100, db_index=True)
    po_line_number = models.CharField(max_length=20, blank=True, default="")
    vendor_code = models.CharField(max_length=50, blank=True, default="")
    item_code = models.CharField(max_length=100, blank=True, default="")
    description = models.TextField(blank=True, default="")
    normalized_description = models.TextField(blank=True, default="")
    quantity = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    line_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, blank=True, default="")
    status = models.CharField(max_length=50, blank=True, default="")
    is_open = models.BooleanField(default=True)
    raw_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "posting_core_erp_po_ref"
        ordering = ["po_number", "po_line_number"]
        verbose_name = "ERP PO Reference"
        verbose_name_plural = "ERP PO References"
        indexes = [
            models.Index(fields=["po_number"], name="idx_poref_number"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "po_number", "po_line_number"],
                name="uq_erp_po_ref_tenant_po_line",
            ),
        ]

    def __str__(self) -> str:
        return f"PO {self.po_number}/{self.po_line_number}"


class ERPGRNReference(TimestampMixin):
    """GRN line item imported from ERP goods-receipt table (EFIMRDetailsTable).

    Each row represents one PO line received under a GRN.
    Natural key: (tenant, grn_number, po_voucher_no, po_line_number).
    """

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    batch = models.ForeignKey(
        ERPReferenceImportBatch,
        on_delete=models.CASCADE,
        related_name="grn_refs",
    )
    grn_number = models.CharField(max_length=100, db_index=True)
    # Human-readable PO reference (PartyRefDoc, e.g. "616/2025-26")
    po_number = models.CharField(max_length=100, blank=True, default="", db_index=True)
    # Raw integer VoucherNo from ERP (POrderNum column)
    po_voucher_no = models.CharField(max_length=50, blank=True, default="")
    po_line_number = models.CharField(max_length=20, blank=True, default="")
    receipt_date = models.DateField(null=True, blank=True)
    supplier_code = models.CharField(max_length=50, blank=True, default="")
    supplier_name = models.CharField(max_length=255, blank=True, default="")
    item_code = models.CharField(max_length=100, blank=True, default="")
    item_description = models.TextField(blank=True, default="")
    order_qty = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    grn_qty = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    grn_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    grn_value = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=10, blank=True, default="")
    po_date = models.DateField(null=True, blank=True)
    raw_json = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "posting_core_erp_grn_ref"
        ordering = ["grn_number", "po_line_number"]
        verbose_name = "ERP GRN Reference"
        verbose_name_plural = "ERP GRN References"
        indexes = [
            models.Index(fields=["grn_number"], name="idx_grnref_number"),
            models.Index(fields=["po_number"], name="idx_grnref_po_number"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "grn_number", "po_voucher_no", "po_line_number"],
                name="uq_erp_grn_ref_tenant_grn_line",
            ),
        ]

    def __str__(self) -> str:
        return f"GRN {self.grn_number} / PO {self.po_number} line {self.po_line_number}"


# ============================================================================
# Alias Mappings (business-owned, human-curated)
# ============================================================================


class VendorAliasMapping(BaseModel):
    """Canonical vendor alias table used by extraction, reconciliation, posting, and ERP layers."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    alias_text = models.CharField(max_length=500)
    normalized_alias = models.CharField(max_length=500, db_index=True)
    # FK to Django Vendor master (used by extraction/reconciliation to resolve invoice.vendor)
    vendor = models.ForeignKey(
        "vendors.Vendor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="alias_mappings",
        help_text="Resolved Vendor record (used by extraction and reconciliation)",
    )
    # FK to ERP reference record (used by posting pipeline to map to ERP vendor code)
    vendor_reference = models.ForeignKey(
        ERPVendorReference,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="aliases",
    )
    source = models.CharField(
        max_length=50,
        blank=True,
        default="manual",
        help_text="Origin: manual, extraction, erp",
    )
    confidence = models.FloatField(default=1.0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "posting_core_vendor_alias"
        ordering = ["alias_text"]
        verbose_name = "Vendor Alias Mapping"
        verbose_name_plural = "Vendor Alias Mappings"

    def __str__(self) -> str:
        return f"Alias '{self.alias_text}' → {self.vendor_reference}"


class ItemAliasMapping(BaseModel):
    """Business-owned alias mapping for item/service matching."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    alias_text = models.CharField(max_length=500)
    normalized_alias = models.CharField(max_length=500, db_index=True)
    item_reference = models.ForeignKey(
        ERPItemReference,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="aliases",
    )
    mapped_description = models.TextField(blank=True, default="")
    mapped_category = models.CharField(max_length=100, blank=True, default="")
    confidence = models.FloatField(default=1.0)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "posting_core_item_alias"
        ordering = ["alias_text"]
        verbose_name = "Item Alias Mapping"
        verbose_name_plural = "Item Alias Mappings"

    def __str__(self) -> str:
        return f"Alias '{self.alias_text}' → {self.item_reference}"


# ============================================================================
# Posting Rules
# ============================================================================


class PostingRule(BaseModel):
    """Configurable mapping/blocking rules for posting."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    name = models.CharField(max_length=200)
    rule_type = models.CharField(
        max_length=20,
        choices=PostingRuleType.choices,
        db_index=True,
    )
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True, db_index=True)
    condition_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Matching conditions (JSON object with field/value pairs)",
    )
    output_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Output values when conditions match (e.g. {tax_code: 'V1'})",
    )
    stop_on_match = models.BooleanField(default=True)

    class Meta:
        db_table = "posting_core_posting_rule"
        ordering = ["rule_type", "priority"]
        verbose_name = "Posting Rule"
        verbose_name_plural = "Posting Rules"

    def __str__(self) -> str:
        return f"Rule {self.name} [{self.rule_type}] (priority={self.priority})"
