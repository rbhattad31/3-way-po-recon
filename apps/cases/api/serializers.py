"""DRF serializers for the cases app."""

from rest_framework import serializers

from apps.cases.models import (
    APCase,
    APCaseActivity,
    APCaseArtifact,
    APCaseAssignment,
    APCaseComment,
    APCaseDecision,
    APCaseStage,
    APCaseSummary,
    ManualReviewAction,
    ReviewAssignment,
    ReviewComment,
    ReviewDecision,
)


class APCaseListSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", default="", read_only=True)
    total_amount = serializers.DecimalField(
        source="invoice.total_amount", max_digits=18, decimal_places=2, read_only=True,
    )
    currency = serializers.CharField(source="invoice.currency", read_only=True)
    assigned_to_name = serializers.CharField(
        source="assigned_to.get_full_name", default="", read_only=True,
    )
    age_hours = serializers.SerializerMethodField()

    class Meta:
        model = APCase
        fields = [
            "id", "case_number", "invoice_number", "vendor_name",
            "total_amount", "currency", "processing_path", "status",
            "current_stage", "priority", "assigned_to_name",
            "requires_human_review", "risk_score", "extraction_confidence",
            "age_hours", "created_at",
        ]

    def get_age_hours(self, obj):
        from django.utils import timezone
        delta = timezone.now() - obj.created_at
        return round(delta.total_seconds() / 3600, 1)


class APCaseStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = APCaseStage
        fields = [
            "id", "stage_name", "stage_status", "performed_by_type",
            "started_at", "completed_at", "retry_count", "notes", "created_at",
        ]


class APCaseArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = APCaseArtifact
        fields = [
            "id", "artifact_type", "linked_object_type", "linked_object_id",
            "payload", "version", "created_at",
        ]


class APCaseDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = APCaseDecision
        fields = [
            "id", "decision_type", "decision_source", "decision_value",
            "confidence", "rationale", "evidence", "created_at",
        ]


class APCaseAssignmentSerializer(serializers.ModelSerializer):
    assigned_user_name = serializers.CharField(
        source="assigned_user.get_full_name", default="", read_only=True,
    )

    class Meta:
        model = APCaseAssignment
        fields = [
            "id", "assignment_type", "assigned_user", "assigned_user_name",
            "assigned_role", "queue_name", "due_at", "escalation_level",
            "status", "created_at",
        ]


class APCaseSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = APCaseSummary
        fields = [
            "latest_summary", "reviewer_summary", "finance_summary",
            "recommendation", "updated_at",
        ]


class APCaseCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.get_full_name", default="", read_only=True)

    class Meta:
        model = APCaseComment
        fields = ["id", "author", "author_name", "body", "is_internal", "created_at"]
        read_only_fields = ["author"]


class APCaseActivitySerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(
        source="actor.get_full_name", default="", read_only=True,
    )

    class Meta:
        model = APCaseActivity
        fields = [
            "id", "activity_type", "description", "actor",
            "actor_name", "metadata", "created_at",
        ]


class APCaseDetailSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    vendor_name = serializers.CharField(source="vendor.name", default="", read_only=True)
    total_amount = serializers.DecimalField(
        source="invoice.total_amount", max_digits=18, decimal_places=2, read_only=True,
    )
    stages = APCaseStageSerializer(many=True, read_only=True)
    decisions = APCaseDecisionSerializer(many=True, read_only=True)
    assignments = APCaseAssignmentSerializer(many=True, read_only=True)
    summary = APCaseSummarySerializer(read_only=True)
    comments = APCaseCommentSerializer(many=True, read_only=True)
    activities = APCaseActivitySerializer(many=True, read_only=True)

    class Meta:
        model = APCase
        fields = [
            "id", "case_number", "invoice_number", "vendor_name", "total_amount",
            "source_channel", "invoice_type", "processing_path", "status",
            "current_stage", "priority", "risk_score", "extraction_confidence",
            "requires_human_review", "requires_approval", "eligible_for_posting",
            "duplicate_risk_flag", "reconciliation_mode", "budget_check_status",
            "coding_status", "assigned_to", "assigned_role",
            "stages", "decisions", "assignments", "summary", "comments",
            "activities",
            "created_at", "updated_at",
        ]


class CopilotChatInputSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=2000)
    conversation_id = serializers.CharField(max_length=100, required=False, default="")


class AssignCaseSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(required=False)
    role = serializers.CharField(max_length=30, required=False)
    queue = serializers.CharField(max_length=100, required=False, default="default")


class RunStageSerializer(serializers.Serializer):
    stage = serializers.CharField(max_length=50)


class ReroutePathSerializer(serializers.Serializer):
    new_path = serializers.ChoiceField(choices=["TWO_WAY", "THREE_WAY", "NON_PO"])
    reason = serializers.CharField(max_length=500)


# ---------------------------------------------------------------------------
# Review serializers (merged from apps.reviews)
# ---------------------------------------------------------------------------

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

    # Reviewer-facing exception summary (populated by ExceptionAnalysisAgent)
    reviewer_summary = serializers.CharField(read_only=True)
    reviewer_risk_level = serializers.CharField(read_only=True)
    reviewer_confidence = serializers.FloatField(read_only=True)
    reviewer_recommendation = serializers.CharField(read_only=True)
    reviewer_suggested_actions = serializers.JSONField(read_only=True)
    reviewer_summary_generated_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ReviewAssignment
        fields = [
            "id", "reconciliation_result", "assigned_to", "assigned_to_name",
            "status", "priority", "due_date", "notes",
            "invoice_number", "comments", "actions", "decision",
            "reviewer_summary", "reviewer_risk_level", "reviewer_confidence",
            "reviewer_recommendation", "reviewer_suggested_actions",
            "reviewer_summary_generated_at",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "assigned_to_name", "invoice_number",
            "comments", "actions", "decision", "created_at", "updated_at",
        ]


# ---------------------------------------------------------------------------
# Review write serializers
# ---------------------------------------------------------------------------
class ReviewDecisionWriteSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["APPROVED", "REJECTED", "REPROCESSED"])
    reason = serializers.CharField(required=False, default="", allow_blank=True)


class ReviewCommentWriteSerializer(serializers.Serializer):
    body = serializers.CharField()
    is_internal = serializers.BooleanField(default=True)


class ReviewAssignSerializer(serializers.Serializer):
    assigned_to = serializers.IntegerField()
