"""Serializers for email integration domain models."""
from rest_framework import serializers

from apps.email_integration.models import (
    EmailAction,
    EmailAttachment,
    EmailMessage,
    EmailRoutingDecision,
    EmailTemplate,
    EmailThread,
    MailboxConfig,
)


class MailboxConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = MailboxConfig
        fields = "__all__"


class EmailAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailAttachment
        fields = "__all__"


class EmailMessageListSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailMessage
        fields = [
            "id",
            "mailbox",
            "thread",
            "direction",
            "subject",
            "from_email",
            "received_at",
            "message_classification",
            "processing_status",
            "routing_status",
            "trace_id",
        ]


class EmailMessageDetailSerializer(serializers.ModelSerializer):
    attachments = EmailAttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = EmailMessage
        fields = "__all__"


class EmailThreadSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailThread
        fields = "__all__"


class EmailRoutingDecisionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailRoutingDecision
        fields = "__all__"


class EmailActionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailAction
        fields = "__all__"


class EmailTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailTemplate
        fields = "__all__"
