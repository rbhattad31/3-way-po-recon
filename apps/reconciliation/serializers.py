"""Reconciliation API serializers."""
from rest_framework import serializers

from apps.reconciliation.models import (
    ReconciliationConfig,
    ReconciliationException,
    ReconciliationResult,
    ReconciliationResultLine,
    ReconciliationRun,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
class ReconciliationConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReconciliationConfig
        fields = [
            "id", "name", "quantity_tolerance_pct", "price_tolerance_pct",
            "amount_tolerance_pct", "auto_close_on_match", "enable_agents",
            "extraction_confidence_threshold", "is_default",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
class ReconciliationRunListSerializer(serializers.ModelSerializer):
    triggered_by_email = serializers.EmailField(
        source="triggered_by.email", read_only=True, default=None
    )

    class Meta:
        model = ReconciliationRun
        fields = [
            "id", "status", "started_at", "completed_at",
            "total_invoices", "matched_count", "partial_count",
            "unmatched_count", "error_count", "review_count",
            "triggered_by_email", "created_at",
        ]


class ReconciliationRunDetailSerializer(ReconciliationRunListSerializer):
    class Meta(ReconciliationRunListSerializer.Meta):
        fields = ReconciliationRunListSerializer.Meta.fields + [
            "error_message", "celery_task_id",
        ]


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------
class ReconciliationExceptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReconciliationException
        fields = [
            "id", "exception_type", "severity", "message",
            "details", "resolved", "resolved_by", "resolved_at",
            "created_at",
        ]


# ---------------------------------------------------------------------------
# Result Line
# ---------------------------------------------------------------------------
class ReconciliationResultLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReconciliationResultLine
        fields = [
            "id", "match_status",
            "qty_invoice", "qty_po", "qty_received", "qty_difference", "qty_within_tolerance",
            "price_invoice", "price_po", "price_difference", "price_within_tolerance",
            "amount_invoice", "amount_po", "amount_difference", "amount_within_tolerance",
            "description_similarity",
        ]


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
class ReconciliationResultListSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    vendor_name = serializers.SerializerMethodField()
    po_number = serializers.CharField(source="purchase_order.po_number", read_only=True, default="")
    exception_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ReconciliationResult
        fields = [
            "id", "run", "invoice", "invoice_number",
            "vendor_name", "po_number", "match_status",
            "requires_review", "deterministic_confidence",
            "total_amount_difference", "total_amount_difference_pct",
            "exception_count", "created_at",
        ]

    def get_vendor_name(self, obj):
        if obj.invoice.vendor:
            return obj.invoice.vendor.name
        return obj.invoice.raw_vendor_name


class ReconciliationResultDetailSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    vendor_name = serializers.SerializerMethodField()
    po_number = serializers.CharField(source="purchase_order.po_number", read_only=True, default="")
    line_results = ReconciliationResultLineSerializer(many=True, read_only=True)
    exceptions = ReconciliationExceptionSerializer(many=True, read_only=True)

    class Meta:
        model = ReconciliationResult
        fields = [
            "id", "run", "invoice", "invoice_number",
            "purchase_order", "po_number", "vendor_name",
            "match_status", "requires_review",
            "vendor_match", "currency_match", "po_total_match",
            "invoice_total_vs_po", "total_amount_difference",
            "total_amount_difference_pct",
            "grn_available", "grn_fully_received",
            "extraction_confidence", "deterministic_confidence",
            "summary", "line_results", "exceptions",
            "created_at", "updated_at",
        ]

    def get_vendor_name(self, obj):
        if obj.invoice.vendor:
            return obj.invoice.vendor.name
        return obj.invoice.raw_vendor_name
