"""AP domain handler for routed email messages with full service integration."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from django.db import transaction

from apps.email_integration.domain_handlers.base_handler import BaseEmailDomainHandler
from apps.email_integration.enums import EmailActionStatus, EmailActionType, TargetDomain
from apps.email_integration.models import EmailAction
from apps.core.enums import SourceChannel

logger = logging.getLogger(__name__)


class APEmailHandler(BaseEmailDomainHandler):
    """Performs governed AP-side actions by invoking AP domain services."""

    handler_name = "ap_handler"

    def can_handle(self, email_message, routing_decision) -> bool:
        return routing_decision.target_domain == TargetDomain.AP

    @transaction.atomic
    def handle(self, email_message, routing_decision, *, actor_user=None) -> Dict[str, Any]:
        """Route email to AP domain with full service integration."""
        action_type = self._determine_action_type(email_message, routing_decision)
        service_result = {}

        try:
            # Route based on determined action type
            if action_type == EmailActionType.LINK_TO_AP_CASE:
                service_result = self._link_to_existing_case(email_message, routing_decision, actor_user)
            elif action_type == EmailActionType.TRIGGER_EXTRACTION:
                service_result = self._trigger_extraction_from_email(email_message, routing_decision, actor_user)
            elif action_type == EmailActionType.TRIGGER_RECONCILIATION:
                service_result = self._trigger_reconciliation_from_email(email_message, routing_decision, actor_user)
            else:  # CREATE_DOCUMENT_UPLOAD
                service_result = self._create_case_from_email(email_message, routing_decision, actor_user)

            action_status = EmailActionStatus.COMPLETED if service_result.get("success") else EmailActionStatus.FAILED
        except Exception as e:
            logger.exception("AP handler service invocation failed: %s", e)
            action_status = EmailActionStatus.FAILED
            service_result = {"success": False, "error": str(e)}

        # Audit the action
        action = EmailAction.objects.create(
            tenant=email_message.tenant,
            email_message=email_message,
            thread=email_message.thread,
            action_type=action_type,
            action_status=action_status,
            performed_by_user=actor_user,
            actor_primary_role=self._actor_role(actor_user),
            target_entity_type=routing_decision.target_entity_type,
            target_entity_id=routing_decision.target_entity_id,
            trace_id=email_message.trace_id,
            payload_json=self._payload_base(email_message, routing_decision),
            result_json={
                **self._result_base(email_message, routing_decision),
                **service_result,
            },
        )

        return {
            "handled": action_status == EmailActionStatus.COMPLETED,
            "action_id": action.pk,
            "action_type": action.action_type,
            "service_result": service_result,
        }

    def process(self, email_message, routing_decision, *, actor_user=None):
        """Process wrapper."""
        return super().process(email_message, routing_decision, actor_user=actor_user)

    # ============================================================================
    # Private action methods - each invokes specific AP service
    # ============================================================================

    def _determine_action_type(self, email_message, routing_decision) -> str:
        """Determine which action to take based on email context."""
        if routing_decision.target_entity_id and routing_decision.target_entity_type == "AP_CASE":
            return EmailActionType.LINK_TO_AP_CASE
        elif email_message.matched_entity_type == "AP_CASE":
            return EmailActionType.LINK_TO_AP_CASE
        elif email_message.message_classification == "AP_SUPPORTING_DOCUMENT":
            return EmailActionType.TRIGGER_EXTRACTION
        elif email_message.message_classification == "APPROVAL_RESPONSE":
            return EmailActionType.TRIGGER_RECONCILIATION
        else:
            return EmailActionType.CREATE_DOCUMENT_UPLOAD

    @staticmethod
    def _create_case_from_email(email_message, routing_decision, actor_user=None) -> Dict[str, Any]:
        """Create a new AP case from email with attachments as initial documents."""
        try:
            from apps.cases.services.case_creation_service import CaseCreationService
            from apps.documents.models import DocumentUpload

            # Check if email has attachments that can serve as upload
            if email_message.linked_document_upload_id:
                upload = DocumentUpload.objects.filter(pk=email_message.linked_document_upload_id).first()
                if upload:
                    case = CaseCreationService.create_from_document_upload(
                        upload=upload,
                        uploaded_by=actor_user,
                        source_channel=SourceChannel.EMAIL_INGESTION,
                        tenant=email_message.tenant,
                    )
                    return {
                        "success": True,
                        "action": "created_case",
                        "case_id": case.pk,
                        "case_number": case.case_number,
                    }

            # No document to attach - just log the action needs manual review
            return {
                "success": False,
                "action": "no_attachments",
                "message": "Email has no attachable documents; manual case creation required",
            }
        except Exception as e:
            logger.exception("Failed to create case from email: %s", e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _link_to_existing_case(email_message, routing_decision, actor_user=None) -> Dict[str, Any]:
        """Link email thread to existing AP case for ongoing communication."""
        try:
            from apps.cases.models import APCase
            from apps.cases.services.case_activity_service import CaseActivityService

            case_id = routing_decision.target_entity_id
            case = APCase.objects.filter(pk=case_id, tenant=email_message.tenant).first()

            if not case:
                return {"success": False, "error": f"Case {case_id} not found"}

            # Record email attachment to case activity
            CaseActivityService.record_email_attachment(
                case=case,
                email_thread_id=email_message.thread_id,
                email_message_id=email_message.pk,
                actor=actor_user,
                comment=f"Email linked: {email_message.subject}",
            )

            return {
                "success": True,
                "action": "linked_to_case",
                "case_id": case.pk,
                "case_number": case.case_number,
            }
        except Exception as e:
            logger.exception("Failed to link email to case: %s", e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _trigger_extraction_from_email(email_message, routing_decision, actor_user=None) -> Dict[str, Any]:
        """Trigger extraction pipeline for documents embedded in email."""
        try:
            from apps.extraction.tasks import process_invoice_upload_task

            if not email_message.linked_document_upload_id:
                return {"success": False, "error": "No document attached to trigger extraction"}

            # Enqueue extraction task
            task_result = process_invoice_upload_task.delay(
                upload_id=email_message.linked_document_upload_id,
                user_id=actor_user.pk if actor_user else None,
                tenant_id=email_message.tenant_id,
                trace_id=email_message.trace_id,
            )

            return {
                "success": True,
                "action": "extraction_queued",
                "task_id": task_result.id,
                "upload_id": email_message.linked_document_upload_id,
            }
        except Exception as e:
            logger.exception("Failed to trigger extraction: %s", e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _trigger_reconciliation_from_email(email_message, routing_decision, actor_user=None) -> Dict[str, Any]:
        """Trigger reconciliation for approval responses received via email."""
        try:
            from apps.reconciliation.tasks import run_reconciliation_task
            from apps.cases.models import APCase

            if not routing_decision.target_entity_id:
                return {"success": False, "error": "Cannot trigger reconciliation without target entity"}

            case_id = routing_decision.target_entity_id
            case = APCase.objects.filter(pk=case_id, tenant=email_message.tenant).first()

            if not case or not case.invoice:
                return {"success": False, "error": "Associated case or invoice not found"}

            # Enqueue reconciliation task
            task_result = run_reconciliation_task.delay(
                invoice_ids=[case.invoice.pk],
                user_id=actor_user.pk if actor_user else None,
                tenant_id=email_message.tenant_id,
                trace_id=email_message.trace_id,
            )

            return {
                "success": True,
                "action": "reconciliation_queued",
                "task_id": task_result.id,
                "case_id": case.pk,
                "invoice_id": case.invoice.pk,
            }
        except Exception as e:
            logger.exception("Failed to trigger reconciliation: %s", e)
            return {"success": False, "error": str(e)}
