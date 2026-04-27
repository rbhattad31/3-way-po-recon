"""Admin registration for posting_core app models."""
from django.contrib import admin

from apps.posting_core.models import (
    ERPCostCenterReference,
    ERPGRNReference,
    ERPItemReference,
    ERPPOReference,
    ERPReferenceImportBatch,
    ERPTaxCodeReference,
    ERPVendorReference,
    ItemAliasMapping,
    PostingApprovalRecord,
    PostingEvidence,
    PostingFieldValue,
    PostingIssue,
    PostingLineItem,
    PostingRule,
    PostingRun,
    VendorAliasMapping,
)


@admin.register(PostingRun)
class PostingRunAdmin(admin.ModelAdmin):
    list_display = ("id", "invoice", "status", "stage_code", "overall_confidence", "requires_review", "review_queue", "created_at")
    list_filter = ("status", "requires_review", "review_queue")
    search_fields = ("invoice__invoice_number",)
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("invoice", "extraction_run", "extraction_result")


@admin.register(PostingFieldValue)
class PostingFieldValueAdmin(admin.ModelAdmin):
    list_display = ("id", "posting_run", "field_code", "category", "source_type", "value", "confidence")
    list_filter = ("category", "source_type")
    search_fields = ("field_code", "value")
    raw_id_fields = ("posting_run",)


@admin.register(PostingLineItem)
class PostingLineItemAdmin(admin.ModelAdmin):
    list_display = ("id", "posting_run", "line_index", "erp_item_code", "tax_code", "cost_center", "confidence")
    list_filter = ("tax_code",)
    raw_id_fields = ("posting_run", "invoice_line_item")


@admin.register(PostingIssue)
class PostingIssueAdmin(admin.ModelAdmin):
    list_display = ("id", "posting_run", "severity", "field_code", "check_type", "message")
    list_filter = ("severity", "check_type")
    raw_id_fields = ("posting_run",)


@admin.register(PostingEvidence)
class PostingEvidenceAdmin(admin.ModelAdmin):
    list_display = ("id", "posting_run", "field_code", "source_type", "confidence")
    raw_id_fields = ("posting_run",)


@admin.register(PostingApprovalRecord)
class PostingApprovalRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "posting_run", "action", "approved_by", "decided_at")
    list_filter = ("action",)
    raw_id_fields = ("posting_run", "approved_by")


@admin.register(ERPReferenceImportBatch)
class ERPReferenceImportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "batch_type", "source_file_name", "status", "row_count", "valid_row_count", "invalid_row_count", "created_at")
    list_filter = ("batch_type", "status")
    search_fields = ("source_file_name",)
    raw_id_fields = ("imported_by",)


@admin.register(ERPVendorReference)
class ERPVendorReferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "vendor_code", "vendor_name", "vendor_group", "country_code", "currency")
    search_fields = ("vendor_code", "vendor_name")
    list_filter = ("country_code",)
    raw_id_fields = ("batch",)


@admin.register(ERPItemReference)
class ERPItemReferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "item_code", "item_name", "item_type", "category", "uom", "tax_code")
    search_fields = ("item_code", "item_name")
    list_filter = ("item_type",)
    raw_id_fields = ("batch",)


@admin.register(ERPTaxCodeReference)
class ERPTaxCodeReferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "tax_code", "tax_label", "country_code", "rate")
    search_fields = ("tax_code", "tax_label")
    list_filter = ("country_code",)
    raw_id_fields = ("batch",)


@admin.register(ERPCostCenterReference)
class ERPCostCenterReferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "cost_center_code", "cost_center_name", "department", "business_unit")
    search_fields = ("cost_center_code", "cost_center_name")
    raw_id_fields = ("batch",)


@admin.register(ERPPOReference)
class ERPPOReferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "po_number", "po_line_number", "vendor_code", "item_code", "quantity", "unit_price", "is_open")
    search_fields = ("po_number", "vendor_code", "item_code")
    list_filter = ("is_open",)
    raw_id_fields = ("batch",)


@admin.register(ERPGRNReference)
class ERPGRNReferenceAdmin(admin.ModelAdmin):
    list_display = ("id", "grn_number", "po_number", "po_line_number", "supplier_name", "item_code", "grn_qty", "grn_value", "receipt_date")
    search_fields = ("grn_number", "po_number", "supplier_name", "item_code")
    list_filter = ("currency",)
    raw_id_fields = ("batch",)


@admin.register(VendorAliasMapping)
class VendorAliasMappingAdmin(admin.ModelAdmin):
    list_display = ("id", "alias_text", "vendor_reference", "confidence")
    search_fields = ("alias_text",)
    raw_id_fields = ("vendor_reference",)


@admin.register(ItemAliasMapping)
class ItemAliasMappingAdmin(admin.ModelAdmin):
    list_display = ("id", "alias_text", "item_reference", "mapped_description", "confidence")
    search_fields = ("alias_text",)
    raw_id_fields = ("item_reference",)


@admin.register(PostingRule)
class PostingRuleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "rule_type", "priority", "is_active", "stop_on_match")
    list_filter = ("rule_type", "is_active")
    search_fields = ("name",)
