"""Supervisor context builder -- prepares AgentContext for supervisor runs."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from apps.agents.services.agent_memory import AgentMemory
from apps.agents.services.base_agent import AgentContext

logger = logging.getLogger(__name__)


def build_supervisor_context(
    *,
    invoice_id: int,
    document_upload_id: Optional[int] = None,
    reconciliation_result: Any = None,
    po_number: Optional[str] = None,
    reconciliation_mode: str = "",
    actor_user_id: Optional[int] = None,
    actor_primary_role: str = "",
    actor_roles_snapshot: Optional[list] = None,
    permission_checked: str = "",
    permission_source: str = "",
    access_granted: bool = False,
    trace_id: str = "",
    span_id: str = "",
    tenant: Any = None,
    langfuse_trace: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> AgentContext:
    """Build a fully-populated AgentContext for a supervisor run.

    This gathers invoice metadata, existing extraction/reconciliation state,
    and RBAC context into a single context bag.
    """
    # Build initial memory with known facts
    memory = AgentMemory()
    if reconciliation_mode:
        memory.facts["reconciliation_mode"] = reconciliation_mode
        memory.facts["is_two_way"] = reconciliation_mode == "TWO_WAY"

    # Populate invoice-level facts
    try:
        from apps.documents.models import Invoice
        qs = Invoice.objects.select_related("vendor")
        if tenant:
            qs = qs.filter(tenant=tenant)
        invoice = qs.filter(pk=invoice_id).first()
        if invoice:
            memory.facts["invoice_number"] = invoice.invoice_number or ""
            memory.facts["vendor_name"] = (
                invoice.vendor.name if invoice.vendor else ""
            )
            memory.facts["vendor_id"] = (
                invoice.vendor.pk if invoice.vendor else None
            )
            memory.facts["extraction_confidence"] = float(
                invoice.extraction_confidence or 0
            )
            memory.facts["invoice_status"] = str(invoice.status)
            if not po_number:
                po_number = invoice.po_number
            if not document_upload_id:
                document_upload_id = getattr(invoice, "document_upload_id", None)
    except Exception:
        logger.debug("Failed to populate invoice facts (non-fatal)", exc_info=True)

    # Gather exceptions if reconciliation result exists
    exceptions = []
    if reconciliation_result:
        try:
            from apps.reconciliation.models import ReconciliationException
            exc_qs = ReconciliationException.objects.filter(
                reconciliation_result=reconciliation_result
            ).values("exception_type", "severity", "field_name", "description")
            exceptions = list(exc_qs)
        except Exception:
            logger.debug("Failed to load exceptions (non-fatal)", exc_info=True)

    return AgentContext(
        reconciliation_result=reconciliation_result,
        invoice_id=invoice_id,
        po_number=po_number or "",
        exceptions=exceptions,
        extra=extra or {},
        reconciliation_mode=reconciliation_mode,
        actor_user_id=actor_user_id,
        actor_primary_role=actor_primary_role,
        actor_roles_snapshot=actor_roles_snapshot or [],
        permission_checked=permission_checked,
        permission_source=permission_source,
        access_granted=access_granted,
        trace_id=trace_id,
        span_id=span_id,
        document_upload_id=document_upload_id,
        memory=memory,
        _langfuse_trace=langfuse_trace,
        tenant=tenant,
    )
