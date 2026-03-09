"""Review workflow API serializers."""
from rest_framework import serializers

from apps.reviews.models import (
    ManualReviewAction,
    ReviewAssignment,
    ReviewComment,
    ReviewDecision,
)


class ReviewCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.get_full_name", read_only=True, default="")

    class Meta:
        model = ReviewComment
        fields = ["id", "author", "author_name", "body", "is_internal", "created_at"]
        read_only_fields = ["id", "author", "author_name", "created_at"]


class ManualReviewActionSerializer(serializers.ModelSerializer):
    performed_by_name = serializers.CharField(
        source="performed_by.get_full_name", read_only=True, default=""
    )

    class Meta:
        model = ManualReviewAction
        fields = [
            "id", "performed_by", "performed_by_name", "action_type",
            "field_name", "old_value", "new_value", "reason", "created_at",
        ]
        read_only_fields = ["id", "performed_by", "performed_by_name", "created_at"]


class ReviewDecisionSerializer(serializers.ModelSerializer):
    decided_by_name = serializers.CharField(
        source="decided_by.get_full_name", read_only=True, default=""
    )

    class Meta:
        model = ReviewDecision
        fields = [
            "id", "decided_by", "decided_by_name", "decision",
            "reason", "decided_at",
        ]
        read_only_fields = ["id", "decided_by", "decided_by_name", "decided_at"]


class ReviewAssignmentListSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.CharField(
        source="assigned_to.get_full_name", read_only=True, default=""
    )
    invoice_number = serializers.CharField(
        source="reconciliation_result.invoice.invoice_number", read_only=True
    )
    match_status = serializers.CharField(
        source="reconciliation_result.match_status", read_only=True
    )

    class Meta:
        model = ReviewAssignment
        fields = [
            "id", "reconciliation_result", "assigned_to", "assigned_to_name",
            "status", "priority", "due_date",
            "invoice_number", "match_status", "created_at",
        ]


class ReviewAssignmentDetailSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.CharField(
        source="assigned_to.get_full_name", read_only=True, default=""
    )
    comments = ReviewCommentSerializer(many=True, read_only=True)
    actions = ManualReviewActionSerializer(many=True, read_only=True)
    decision = ReviewDecisionSerializer(read_only=True)
    invoice_number = serializers.CharField(
        source="reconciliation_result.invoice.invoice_number", read_only=True
    )

    class Meta:
        model = ReviewAssignment
        fields = [
            "id", "reconciliation_result", "assigned_to", "assigned_to_name",
            "status", "priority", "due_date", "notes",
            "invoice_number", "comments", "actions", "decision",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "assigned_to_name", "invoice_number",
            "comments", "actions", "decision", "created_at", "updated_at",
        ]


# ---------------------------------------------------------------------------
# Write serializers (for review actions)
# ---------------------------------------------------------------------------
class ReviewDecisionWriteSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["APPROVED", "REJECTED", "REPROCESSED"])
    reason = serializers.CharField(required=False, default="", allow_blank=True)


class ReviewCommentWriteSerializer(serializers.Serializer):
    body = serializers.CharField()
    is_internal = serializers.BooleanField(default=True)


class ReviewAssignSerializer(serializers.Serializer):
    assigned_to = serializers.IntegerField()
