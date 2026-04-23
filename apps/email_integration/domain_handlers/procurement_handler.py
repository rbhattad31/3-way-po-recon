"""Procurement domain handler for routed email messages with full service integration."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from django.db import transaction

from apps.email_integration.domain_handlers.base_handler import BaseEmailDomainHandler
from apps.email_integration.enums import EmailActionStatus, EmailActionType, TargetDomain
from apps.email_integration.models import EmailAction

logger = logging.getLogger(__name__)


class ProcurementEmailHandler(BaseEmailDomainHandler):
    """Performs governed procurement-side actions by invoking Procurement domain services."""

    handler_name = "procurement_handler"

    def can_handle(self, email_message, routing_decision) -> bool:
        return routing_decision.target_domain == TargetDomain.PROCUREMENT

    @transaction.atomic
    def handle(self, email_message, routing_decision, *, actor_user=None) -> Dict[str, Any]:
        """Route email to Procurement domain with full service integration."""
        action_type = self._determine_action_type(email_message, routing_decision)
        service_result = {}

        try:
            # Route based on determined action type
            if action_type == EmailActionType.LINK_TO_PROCUREMENT_REQUEST:
                service_result = self._link_to_procurement_request(email_message, routing_decision, actor_user)
            elif action_type == EmailActionType.LINK_TO_SUPPLIER_QUOTATION:
                service_result = self._link_to_supplier_quotation(email_message, routing_decision, actor_user)
            elif action_type == EmailActionType.TRIGGER_QUOTATION_PREFILL:
                service_result = self._trigger_quotation_prefill(email_message, routing_decision, actor_user)
            elif action_type == EmailActionType.TRIGGER_PROCUREMENT_ANALYSIS:
                service_result = self._trigger_procurement_analysis(email_message, routing_decision, actor_user)
            else:  # CREATE_SUPPLIER_QUOTATION
                service_result = self._create_supplier_quotation_from_email(email_message, routing_decision, actor_user)

            action_status = EmailActionStatus.COMPLETED if service_result.get("success") else EmailActionStatus.FAILED
        except Exception as e:
            logger.exception("Procurement handler service invocation failed: %s", e)
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
    # Private action methods - each invokes specific Procurement service
    # ============================================================================

    def _determine_action_type(self, email_message, routing_decision) -> str:
        """Determine which action to take based on email context."""
        if email_message.matched_entity_type == "SUPPLIER_QUOTATION":
            return EmailActionType.LINK_TO_SUPPLIER_QUOTATION
        elif email_message.matched_entity_type == "PROCUREMENT_REQUEST" or routing_decision.target_entity_type == "PROCUREMENT_REQUEST":
            return EmailActionType.LINK_TO_PROCUREMENT_REQUEST
        elif email_message.linked_document_upload_id and email_message.message_classification in ["PROCUREMENT_QUOTATION", "PROCUREMENT_PROPOSAL"]:
            return EmailActionType.TRIGGER_QUOTATION_PREFILL
        elif email_message.message_classification == "PROCUREMENT_CLARIFICATION":
            return EmailActionType.TRIGGER_PROCUREMENT_ANALYSIS
        else:
            return EmailActionType.CREATE_SUPPLIER_QUOTATION

    @staticmethod
    def _link_to_procurement_request(email_message, routing_decision, actor_user=None) -> Dict[str, Any]:
        """Link email thread to existing procurement request for ongoing communication."""
        try:
            from apps.procurement.models import ProcurementRequest

            request_id = routing_decision.target_entity_id
            proc_request = ProcurementRequest.objects.filter(pk=request_id, tenant=email_message.tenant).first()

            if not proc_request:
                return {"success": False, "error": f"Procurement request {request_id} not found"}

            # Link email thread to procurement request
            if not proc_request.email_thread_ids:
                proc_request.email_thread_ids = []
            if email_message.thread_id not in proc_request.email_thread_ids:
                proc_request.email_thread_ids.append(email_message.thread_id)
                proc_request.save(update_fields=["email_thread_ids"])

            return {
                "success": True,
                "action": "linked_to_procurement_request",
                "request_id": proc_request.pk,
                "request_title": proc_request.title,
            }
        except Exception as e:
            logger.exception("Failed to link email to procurement request: %s", e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _link_to_supplier_quotation(email_message, routing_decision, actor_user=None) -> Dict[str, Any]:
        """Link email to supplier quotation for quote communication tracking."""
        try:
            from apps.procurement.models import SupplierQuotation

            quotation_id = routing_decision.target_entity_id
            quotation = SupplierQuotation.objects.filter(pk=quotation_id, tenant=email_message.tenant).first()

            if not quotation:
                return {"success": False, "error": f"Supplier quotation {quotation_id} not found"}

            # Track email thread ID in quotation history/comments
            if not hasattr(quotation, "email_thread_history"):
                quotation.email_thread_history = []
            quotation.email_thread_history.append({
                "thread_id": email_message.thread_id,
                "message_id": email_message.pk,
                "timestamp": str(email_message.received_at),
            })

            return {
                "success": True,
                "action": "linked_to_supplier_quotation",
                "quotation_id": quotation.pk,
            }
        except Exception as e:
            logger.exception("Failed to link email to supplier quotation: %s", e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _trigger_quotation_prefill(email_message, routing_decision, actor_user=None) -> Dict[str, Any]:
        """Trigger quotation prefill from attached document."""
        try:
            from apps.procurement.tasks import process_quotation_prefill_task

            if not email_message.linked_document_upload_id:
                return {"success": False, "error": "No document attached for quotation prefill"}

            # Extract supplier/reference from routing decision
            supplier_id = routing_decision.metadata_json.get("supplier_id") if routing_decision.metadata_json else None

            # Queue quotation prefill task
            task_result = process_quotation_prefill_task.delay(
                upload_id=email_message.linked_document_upload_id,
                supplier_id=supplier_id,
                user_id=actor_user.pk if actor_user else None,
                tenant_id=email_message.tenant_id,
                trace_id=email_message.trace_id,
            )

            return {
                "success": True,
                "action": "quotation_prefill_queued",
                "task_id": task_result.id,
                "upload_id": email_message.linked_document_upload_id,
            }
        except Exception as e:
            logger.exception("Failed to trigger quotation prefill: %s", e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _trigger_procurement_analysis(email_message, routing_decision, actor_user=None) -> Dict[str, Any]:
        """Trigger procurement analysis on clarification/proposal documents."""
        try:
            from apps.procurement.tasks import run_analysis_task

            request_id = routing_decision.target_entity_id

            if not request_id:
                return {"success": False, "error": "No procurement request identified for analysis"}

            # Queue analysis task
            task_result = run_analysis_task.delay(
                request_id=request_id,
                analysis_trigger="email_clarification",
                user_id=actor_user.pk if actor_user else None,
                tenant_id=email_message.tenant_id,
                trace_id=email_message.trace_id,
            )

            return {
                "success": True,
                "action": "procurement_analysis_queued",
                "task_id": task_result.id,
                "request_id": request_id,
            }
        except Exception as e:
            logger.exception("Failed to trigger procurement analysis: %s", e)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _create_supplier_quotation_from_email(email_message, routing_decision, actor_user=None) -> Dict[str, Any]:
        """Create new supplier quotation record from email."""
        try:
            from apps.procurement.models import SupplierQuotation
            from apps.procurement.services.recommendation_service import RecommendationService

            # Extract supplier info from email sender
            from_email = email_message.from_email
            supplier_id = None
            supplier_name = from_email.split("@")[0] if "@" in from_email else from_email

            # Try to match supplier from email domain
            from apps.vendors.models import Vendor
            vendor = Vendor.objects.filter(
                tenant=email_message.tenant,
                contact_emails__icontains=from_email,
            ).first()

            if vendor:
                supplier_id = vendor.pk
                supplier_name = vendor.name

            # Create supplier quotation
            quotation = SupplierQuotation.objects.create(
                tenant=email_message.tenant,
                supplier_id=supplier_id,
                supplier_name=supplier_name,
                quotation_source="EMAIL",
                status="RECEIVED",
                email_thread_id=email_message.thread_id,
                received_at=email_message.received_at,
                created_by=actor_user,
            )

            return {
                "success": True,
                "action": "supplier_quotation_created",
                "quotation_id": quotation.pk,
                "supplier_name": supplier_name,
            }
        except Exception as e:
            logger.exception("Failed to create supplier quotation: %s", e)
            return {"success": False, "error": str(e)}
