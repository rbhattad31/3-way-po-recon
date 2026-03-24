"""DRF serializers for posting app."""
from rest_framework import serializers

from apps.posting.models import InvoicePosting, InvoicePostingFieldCorrection


class InvoicePostingListSerializer(serializers.ModelSerializer):
    """Lightweight list serializer."""
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    vendor_name = serializers.CharField(source="invoice.raw_vendor_name", read_only=True)

    class Meta:
        model = InvoicePosting
        fields = [
            "id",
            "invoice",
            "invoice_number",
            "vendor_name",
            "status",
            "stage",
            "posting_confidence",
            "review_queue",
            "is_touchless",
            "erp_document_number",
            "retry_count",
            "created_at",
            "updated_at",
        ]


class InvoicePostingDetailSerializer(serializers.ModelSerializer):
    """Full detail serializer."""
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    vendor_name = serializers.CharField(source="invoice.raw_vendor_name", read_only=True)
    reviewed_by_email = serializers.EmailField(
        source="reviewed_by.email", read_only=True, default=""
    )
    corrections = serializers.SerializerMethodField()

    class Meta:
        model = InvoicePosting
        fields = [
            "id",
            "invoice",
            "invoice_number",
            "vendor_name",
            "status",
            "stage",
            "posting_confidence",
            "review_queue",
            "is_touchless",
            "reviewed_by",
            "reviewed_by_email",
            "reviewed_at",
            "rejection_reason",
            "mapping_summary_json",
            "payload_snapshot_json",
            "posting_snapshot_batch_refs_json",
            "erp_document_number",
            "last_error_code",
            "last_error_message",
            "retry_count",
            "created_at",
            "updated_at",
            "corrections",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_corrections(self, obj):
        qs = obj.field_corrections.order_by("-created_at")[:50]
        return InvoicePostingFieldCorrectionSerializer(qs, many=True).data


class InvoicePostingFieldCorrectionSerializer(serializers.ModelSerializer):
    corrected_by_email = serializers.EmailField(
        source="corrected_by.email", read_only=True, default=""
    )

    class Meta:
        model = InvoicePostingFieldCorrection
        fields = [
            "id",
            "entity_type",
            "entity_id",
            "field_name",
            "original_value",
            "corrected_value",
            "corrected_by",
            "corrected_by_email",
            "reason",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class PostingApproveRequestSerializer(serializers.Serializer):
    """Request body for approve action."""
    corrections = serializers.DictField(required=False, default=dict)


class PostingRejectRequestSerializer(serializers.Serializer):
    """Request body for reject action."""
    reason = serializers.CharField(required=False, default="", allow_blank=True)


class PostingPrepareRequestSerializer(serializers.Serializer):
    """Request body for triggering posting preparation."""
    invoice_id = serializers.IntegerField()
    trigger = serializers.CharField(required=False, default="manual")
