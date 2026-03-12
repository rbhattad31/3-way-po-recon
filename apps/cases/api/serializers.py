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
