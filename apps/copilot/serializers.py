"""DRF serializers for the AP Copilot."""
from rest_framework import serializers

from apps.copilot.models import CopilotMessage, CopilotSession, CopilotSessionArtifact


class CopilotSessionListSerializer(serializers.ModelSerializer):
    message_count = serializers.SerializerMethodField()
    case_number = serializers.CharField(source="linked_case.case_number", default=None, read_only=True)

    class Meta:
        model = CopilotSession
        fields = [
            "id", "title", "status", "is_pinned", "is_archived",
            "linked_case", "case_number", "last_message_at",
            "created_at", "message_count",
        ]
        read_only_fields = fields

    def get_message_count(self, obj):
        return obj.messages.count()


class CopilotSessionDetailSerializer(serializers.ModelSerializer):
    messages = serializers.SerializerMethodField()
    case_number = serializers.CharField(source="linked_case.case_number", default=None, read_only=True)

    class Meta:
        model = CopilotSession
        fields = [
            "id", "title", "status", "is_pinned", "is_archived",
            "actor_primary_role", "actor_roles_snapshot_json",
            "linked_case", "case_number", "linked_invoice",
            "last_message_at", "created_at", "updated_at",
            "trace_id", "messages",
        ]
        read_only_fields = fields

    def get_messages(self, obj):
        qs = obj.messages.order_by("created_at")
        return CopilotMessageSerializer(qs, many=True).data


class CopilotMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = CopilotMessage
        fields = [
            "id", "session", "message_type", "content",
            "structured_payload_json", "consulted_agents_json",
            "evidence_payload_json", "governance_payload_json",
            "linked_case_id", "token_count",
            "trace_id", "span_id", "created_at",
        ]
        read_only_fields = fields


class CopilotSessionArtifactSerializer(serializers.ModelSerializer):
    class Meta:
        model = CopilotSessionArtifact
        fields = [
            "id", "session", "artifact_type",
            "linked_object_type", "linked_object_id",
            "payload_json", "created_at",
        ]
        read_only_fields = fields


# --- Request serializers ---

class StartSessionRequestSerializer(serializers.Serializer):
    case_id = serializers.IntegerField(required=False, allow_null=True)


class ChatRequestSerializer(serializers.Serializer):
    session_id = serializers.UUIDField()
    message = serializers.CharField(max_length=4000)
    case_id = serializers.IntegerField(required=False, allow_null=True)
