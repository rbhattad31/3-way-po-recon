"""Governed outbound templated email service."""
from __future__ import annotations

from apps.core.decorators import observed_service
from apps.email_integration.enums import EmailActionStatus, EmailActionType
from apps.email_integration.models import EmailAction, EmailTemplate
from apps.email_integration.services.mailbox_service import MailboxService


class OutboundEmailService:
    """Renders templates and sends outbound email via mailbox adapters."""

    @staticmethod
    def _render(template: str, variables: dict) -> str:
        safe = dict(variables or {})
        return (template or "").format_map({k: str(v) for k, v in safe.items()})

    @classmethod
    @observed_service("email.outbound.send")
    def send_templated_email(
        cls,
        *,
        tenant,
        mailbox,
        template_code: str,
        variables: dict,
        to_recipients: list,
        actor_user=None,
        trace_id: str = "",
    ) -> dict:
        template = EmailTemplate.objects.filter(tenant=tenant, template_code=template_code, is_active=True).first()
        if template is None:
            template = EmailTemplate.objects.filter(tenant__isnull=True, template_code=template_code, is_active=True).first()
        if template is None:
            raise ValueError(f"No active template found for code={template_code}")

        payload = {
            "subject": cls._render(template.subject_template, variables),
            "body_text": cls._render(template.body_text_template, variables),
            "body_html": cls._render(template.body_html_template, variables),
            "to": to_recipients,
        }
        adapter = MailboxService.get_adapter(mailbox)
        result = adapter.send_message(mailbox, payload)

        EmailAction.objects.create(
            tenant=tenant,
            action_type=EmailActionType.SEND_OUTBOUND_EMAIL,
            action_status=EmailActionStatus.COMPLETED,
            performed_by_user=actor_user,
            actor_primary_role=(getattr(actor_user, "role", "") or "") if actor_user else "",
            payload_json=payload,
            result_json=result,
            trace_id=trace_id,
        )
        return result
