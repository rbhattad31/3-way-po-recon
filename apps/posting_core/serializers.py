"""DRF serializers for posting_core models."""
from rest_framework import serializers

from apps.posting_core.models import (
    ERPCostCenterReference,
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


# ── PostingRun ──────────────────────────────────────────────────────
class PostingRunListSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(
        source="invoice.invoice_number", read_only=True,
    )

    class Meta:
        model = PostingRun
        fields = [
            "id",
            "invoice",
            "invoice_number",
            "status",
            "stage_code",
            "overall_confidence",
            "requires_review",
            "review_queue",
            "started_at",
            "completed_at",
            "created_at",
        ]


class PostingRunDetailSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(
        source="invoice.invoice_number", read_only=True,
    )
    field_values = serializers.SerializerMethodField()
    line_items = serializers.SerializerMethodField()
    issues = serializers.SerializerMethodField()

    class Meta:
        model = PostingRun
        fields = [
            "id",
            "invoice",
            "invoice_number",
            "extraction_run",
            "extraction_result",
            "status",
            "stage_code",
            "overall_confidence",
            "requires_review",
            "review_queue",
            "review_reasons_json",
            "header_snapshot_json",
            "lines_snapshot_json",
            "proposal_snapshot_json",
            "payload_snapshot_json",
            "batch_refs_json",
            "started_at",
            "completed_at",
            "duration_ms",
            "created_at",
            "updated_at",
            "field_values",
            "line_items",
            "issues",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_field_values(self, obj):
        return PostingFieldValueSerializer(
            obj.field_values.all()[:100], many=True,
        ).data

    def get_line_items(self, obj):
        return PostingLineItemSerializer(
            obj.line_items.order_by("line_index")[:100], many=True,
        ).data

    def get_issues(self, obj):
        return PostingIssueSerializer(
            obj.issues.all()[:50], many=True,
        ).data


# ── PostingRun children ─────────────────────────────────────────────
class PostingFieldValueSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostingFieldValue
        fields = [
            "id", "field_code", "category", "source_type",
            "source_ref", "value", "confidence", "line_item_index",
        ]


class PostingLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostingLineItem
        fields = [
            "id", "line_index", "source_description",
            "mapped_description", "erp_item_code", "tax_code",
            "cost_center", "gl_account", "uom", "confidence",
            "source_json", "resolved_json",
        ]


class PostingIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostingIssue
        fields = [
            "id", "severity", "field_code", "check_type",
            "message", "details_json",
        ]


class PostingEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostingEvidence
        fields = [
            "id", "field_code", "source_type", "source_path",
            "snippet", "confidence",
        ]


class PostingApprovalRecordSerializer(serializers.ModelSerializer):
    approved_by_email = serializers.EmailField(
        source="approved_by.email", read_only=True, default="",
    )

    class Meta:
        model = PostingApprovalRecord
        fields = [
            "id", "posting_run", "action", "approved_by",
            "approved_by_email", "comments", "decided_at",
        ]
        read_only_fields = ["id"]


# ── ERP Reference Import Batch ──────────────────────────────────────
class ERPReferenceImportBatchSerializer(serializers.ModelSerializer):
    imported_by_email = serializers.EmailField(
        source="imported_by.email", read_only=True, default="",
    )

    class Meta:
        model = ERPReferenceImportBatch
        fields = [
            "id", "batch_type", "source_file_name", "source_as_of",
            "checksum", "status", "row_count", "valid_row_count",
            "invalid_row_count", "error_summary", "imported_by",
            "imported_by_email", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ── ERP Reference Data ──────────────────────────────────────────────
class ERPVendorReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ERPVendorReference
        fields = [
            "id", "vendor_code", "vendor_name", "normalized_vendor_name",
            "vendor_group", "country_code", "payment_terms", "currency",
        ]


class ERPItemReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ERPItemReference
        fields = [
            "id", "item_code", "item_name", "normalized_item_name",
            "item_type", "category", "uom", "tax_code",
        ]


class ERPTaxCodeReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ERPTaxCodeReference
        fields = ["id", "tax_code", "tax_label", "country_code", "rate"]


class ERPCostCenterReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ERPCostCenterReference
        fields = [
            "id", "cost_center_code", "cost_center_name",
            "department", "business_unit",
        ]


class ERPPOReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ERPPOReference
        fields = [
            "id", "po_number", "po_line_number", "vendor_code",
            "item_code", "description", "quantity", "unit_price",
            "line_amount", "currency", "status", "is_open",
        ]


# ── Alias Mappings ──────────────────────────────────────────────────
class VendorAliasMappingSerializer(serializers.ModelSerializer):
    vendor_code = serializers.CharField(
        source="vendor_reference.vendor_code", read_only=True, default="",
    )

    class Meta:
        model = VendorAliasMapping
        fields = [
            "id", "alias_text", "normalized_alias",
            "vendor_reference", "vendor_code", "confidence",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class ItemAliasMappingSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(
        source="item_reference.item_code", read_only=True, default="",
    )

    class Meta:
        model = ItemAliasMapping
        fields = [
            "id", "alias_text", "normalized_alias",
            "item_reference", "item_code", "mapped_description",
            "mapped_category", "confidence", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


# ── Posting Rules ───────────────────────────────────────────────────
class PostingRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostingRule
        fields = [
            "id", "name", "rule_type", "priority", "is_active",
            "condition_json", "output_json", "stop_on_match",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ── Upload serializer ──────────────────────────────────────────────
class ERPReferenceUploadSerializer(serializers.Serializer):
    """Multipart upload for ERP reference Excel/CSV files."""
    file = serializers.FileField()
    batch_type = serializers.ChoiceField(choices=[
        ("VENDOR", "Vendor"),
        ("ITEM", "Item"),
        ("TAX", "Tax"),
        ("COST_CENTER", "Cost Center"),
        ("OPEN_PO", "Open PO"),
    ])
    source_as_of = serializers.DateField(required=False, allow_null=True)
