"""
Traceability & observability seed data.

Creates AgentStep, AgentMessage, ToolCall, DecisionLog, AgentEscalation,
ProcessingLog, FileProcessingStatus, and ManualReviewAction records.
Also enriches existing AgentRun and AuditEvent records with trace/RBAC fields.
"""
from __future__ import annotations

import logging
import random
import uuid
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.accounts.models import User
from apps.agents.models import (
    AgentEscalation,
    AgentMessage,
    AgentRun,
    AgentStep,
    DecisionLog,
)
from apps.auditlog.models import AuditEvent, FileProcessingStatus, ProcessingLog
from apps.cases.models import APCase
from apps.core.enums import (
    AgentRunStatus,
    AgentType,
    AuditEventType,
    ExceptionSeverity,
    ReviewActionType,
    ToolCallStatus,
)
from apps.documents.models import DocumentUpload
from apps.reviews.models import ManualReviewAction, ReviewAssignment
from apps.tools.models import ToolCall, ToolDefinition

logger = logging.getLogger(__name__)

_rng = random.Random(42)

# ============================================================
# Trace ID helpers
# ============================================================

def _trace_id() -> str:
    return uuid.uuid4().hex[:32]


def _span_id() -> str:
    return uuid.uuid4().hex[:16]


# ============================================================
# Tool mapping per agent type
# ============================================================

_AGENT_TOOL_MAP = {
    AgentType.INVOICE_UNDERSTANDING: ["invoice_details"],
    AgentType.PO_RETRIEVAL: ["po_lookup", "vendor_search"],
    AgentType.GRN_RETRIEVAL: ["grn_lookup"],
    AgentType.RECONCILIATION_ASSIST: ["reconciliation_summary", "exception_list"],
    AgentType.EXCEPTION_ANALYSIS: ["exception_list", "invoice_details"],
    AgentType.REVIEW_ROUTING: [],
    AgentType.CASE_SUMMARY: ["reconciliation_summary"],
}

# ============================================================
# Step templates per agent type
# ============================================================

_STEP_TEMPLATES = {
    AgentType.INVOICE_UNDERSTANDING: [
        ("Parse document metadata", {"source": "document_upload"}, {"format": "PDF", "pages": 1}),
        ("Extract header fields", {"fields": ["vendor", "date", "total"]}, {"confidence": 0.92}),
        ("Validate line items", {"check": "completeness"}, {"valid": True, "items": 3}),
    ],
    AgentType.PO_RETRIEVAL: [
        ("Search PO by number", {"strategy": "exact_match"}, {"found": True}),
        ("Validate vendor match", {"check": "vendor_linkage"}, {"match": True}),
    ],
    AgentType.GRN_RETRIEVAL: [
        ("Query GRN records", {"po_number": "PO-REF"}, {"grns_found": 1}),
        ("Validate receipt quantities", {"check": "qty_match"}, {"within_tolerance": True}),
    ],
    AgentType.RECONCILIATION_ASSIST: [
        ("Load reconciliation context", {"mode": "auto"}, {"exceptions_count": 0}),
        ("Compare line items", {"strategy": "line_by_line"}, {"mismatches": 0}),
        ("Evaluate tolerance bands", {"strict": True}, {"pass": True}),
    ],
    AgentType.EXCEPTION_ANALYSIS: [
        ("Classify exceptions", {"input": "exception_list"}, {"categories": ["AMOUNT"]}),
        ("Assess severity", {"strategy": "rule_based"}, {"severity": "MEDIUM"}),
        ("Generate resolution suggestions", {}, {"suggestions": 2}),
    ],
    AgentType.REVIEW_ROUTING: [
        ("Evaluate case priority", {"factors": ["amount", "exceptions"]}, {"priority": "MEDIUM"}),
        ("Select reviewer queue", {"strategy": "role_based"}, {"queue": "AP Review"}),
    ],
    AgentType.CASE_SUMMARY: [
        ("Aggregate case data", {"sources": ["recon", "agent", "review"]}, {"sections": 3}),
        ("Generate narrative", {"style": "structured"}, {"word_count": 120}),
    ],
}

# ============================================================
# Message templates per agent type
# ============================================================

_SYSTEM_PROMPTS = {
    AgentType.INVOICE_UNDERSTANDING: "You are an Invoice Understanding Agent. Analyze uploaded invoices, validate extracted data, and flag quality issues.",
    AgentType.PO_RETRIEVAL: "You are a PO Retrieval Agent. Find and validate purchase orders matching the invoice reference.",
    AgentType.GRN_RETRIEVAL: "You are a GRN Specialist Agent. Locate goods receipt notes for PO-invoice matching.",
    AgentType.RECONCILIATION_ASSIST: "You are a Reconciliation Assistant. Perform 2-way or 3-way matching and identify discrepancies.",
    AgentType.EXCEPTION_ANALYSIS: "You are an Exception Analysis Agent. Classify, assess, and suggest resolutions for reconciliation exceptions.",
    AgentType.REVIEW_ROUTING: "You are a Review Routing Agent. Determine the appropriate review queue and assignee based on case attributes.",
    AgentType.CASE_SUMMARY: "You are a Case Summary Agent. Generate concise, role-specific summaries for AP cases.",
}


# ============================================================
# Main entry point
# ============================================================

def seed_observability_data(
    scenario_data: dict,
    case_data: dict,
    users: dict[str, User],
    admin: User,
) -> dict:
    """
    Enrich seed data with full observability/traceability records:
    - AgentStep, AgentMessage, ToolCall per AgentRun
    - DecisionLog per case
    - AgentEscalation for escalated cases
    - ProcessingLog per case processing stage
    - ManualReviewAction for reviewed cases
    - Enrich AgentRun with trace_id, token counts, cost
    - Enrich AuditEvent with trace_id, RBAC fields, cross-references
    """
    stats = {
        "agent_steps": 0,
        "agent_messages": 0,
        "tool_calls": 0,
        "decision_logs": 0,
        "escalations": 0,
        "processing_logs": 0,
        "review_actions": 0,
    }

    # Pre-load tool definitions
    tool_defs = {td.name: td for td in ToolDefinition.objects.all()}

    for sc_num, sd in scenario_data.items():
        sc = sd["scenario"]
        cd = case_data.get(sc_num)
        if not cd:
            continue

        case: APCase = cd["case"]
        recon_result = cd.get("recon_result")
        invoice = sd["invoice"]
        po = sd.get("po")

        # Generate a shared trace_id for this case's lifecycle
        case_trace_id = _trace_id()
        base_time = timezone.now() - timedelta(hours=48, minutes=sc_num * 30)

        # ---- Enrich AgentRuns with trace/observability fields ----
        agent_runs = list(
            AgentRun.objects.filter(reconciliation_result=recon_result).order_by("created_at")
        ) if recon_result else []

        for idx, ar in enumerate(agent_runs):
            run_span = _span_id()
            _enrich_agent_run(ar, case_trace_id, run_span, admin, idx)

            # ---- AgentStep records (ReAct loop steps) ----
            n_steps = _create_agent_steps(ar, run_span, stats)

            # ---- AgentMessage records (LLM conversation) ----
            _create_agent_messages(ar, sc, invoice, stats)

            # ---- ToolCall records ----
            _create_tool_calls(ar, tool_defs, invoice, po, case, stats)

        # ---- DecisionLog records (1-3 per case) ----
        _create_decision_logs(
            agent_runs, case, sc, recon_result, invoice,
            case_trace_id, admin, stats,
        )

        # ---- AgentEscalation for escalated cases ----
        if sc["status"] == "ESCALATED" and agent_runs and recon_result:
            _create_escalation(agent_runs[-1], recon_result, sc, stats)

        # ---- ProcessingLog entries (pipeline trace) ----
        _create_processing_logs(
            case, sc, invoice, recon_result, agent_runs,
            case_trace_id, admin, base_time, stats,
        )

        # ---- ManualReviewAction for reviewed cases ----
        if sc.get("review_required") and recon_result:
            _create_review_actions(
                case, sc, recon_result, admin, users, stats,
            )

        # ---- Enrich AuditEvents with trace/RBAC fields ----
        _enrich_audit_events(case, case_trace_id, admin, invoice, recon_result)

    logger.info(
        "Observability data: %d steps, %d messages, %d tool_calls, "
        "%d decisions, %d escalations, %d proc_logs, %d review_actions",
        stats["agent_steps"], stats["agent_messages"], stats["tool_calls"],
        stats["decision_logs"], stats["escalations"],
        stats["processing_logs"], stats["review_actions"],
    )
    return stats


# ============================================================
# Helper: Enrich AgentRun
# ============================================================

def _enrich_agent_run(ar: AgentRun, trace_id: str, span_id: str, admin: User, idx: int):
    """Add trace fields, token counts, cost estimate to an AgentRun."""
    prompt_tokens = _rng.randint(400, 1800)
    completion_tokens = _rng.randint(100, 600)
    total_tokens = prompt_tokens + completion_tokens
    # GPT-4o pricing approx: $5/1M input, $15/1M output
    cost = Decimal(str(prompt_tokens * 5 / 1_000_000 + completion_tokens * 15 / 1_000_000))

    ar.trace_id = trace_id
    ar.span_id = span_id
    ar.llm_model_used = "gpt-4o"
    ar.prompt_tokens = prompt_tokens
    ar.completion_tokens = completion_tokens
    ar.total_tokens = total_tokens
    ar.cost_estimate = cost.quantize(Decimal("0.000001"))
    ar.prompt_version = "1.0"
    ar.invocation_reason = f"Auto-triggered by pipeline stage {idx + 1}"
    ar.actor_user_id = admin.pk
    ar.permission_checked = "agents.use_copilot"
    ar.save(update_fields=[
        "trace_id", "span_id", "llm_model_used",
        "prompt_tokens", "completion_tokens", "total_tokens",
        "cost_estimate", "prompt_version", "invocation_reason",
        "actor_user_id", "permission_checked",
    ])


# ============================================================
# Helper: Create AgentStep records
# ============================================================

def _create_agent_steps(ar: AgentRun, parent_span: str, stats: dict) -> int:
    """Create ReAct loop steps for an agent run."""
    templates = _STEP_TEMPLATES.get(ar.agent_type, [])
    if not templates:
        return 0

    # Pick 2-3 steps from available templates
    n = min(len(templates), _rng.randint(2, 3))
    selected = templates[:n]
    created = 0

    for step_num, (action, inp, out) in enumerate(selected, 1):
        existing = AgentStep.objects.filter(
            agent_run=ar, step_number=step_num,
        ).exists()
        if existing:
            continue

        duration = _rng.randint(200, 2000)
        AgentStep.objects.create(
            agent_run=ar,
            step_number=step_num,
            action=action,
            input_data=inp,
            output_data=out,
            success=True,
            duration_ms=duration,
        )
        created += 1

    stats["agent_steps"] += created
    return created


# ============================================================
# Helper: Create AgentMessage records
# ============================================================

def _create_agent_messages(ar: AgentRun, sc: dict, invoice, stats: dict):
    """Create LLM conversation messages for an agent run."""
    if AgentMessage.objects.filter(agent_run=ar).exists():
        return

    system_prompt = _SYSTEM_PROMPTS.get(ar.agent_type, "You are an AI agent.")
    inv_num = invoice.invoice_number
    vendor = invoice.raw_vendor_name or "Unknown"

    # System message
    messages = [
        ("system", system_prompt, len(system_prompt) // 4),
    ]

    # User message (the request)
    user_content = (
        f"Analyze invoice {inv_num} from vendor {vendor}. "
        f"Total: SAR {invoice.total_amount}. "
        f"Processing path: {sc['path']}."
    )
    messages.append(("user", user_content, len(user_content) // 4))

    # Tool-calling assistant message (if agent uses tools)
    tools = _AGENT_TOOL_MAP.get(ar.agent_type, [])
    if tools:
        tool_name = tools[0]
        tool_call_msg = (
            f"I need to look up additional information. "
            f"Calling {tool_name} for invoice {inv_num}."
        )
        messages.append(("assistant", tool_call_msg, len(tool_call_msg) // 4))

        # Tool response
        tool_resp = f"Tool {tool_name} returned results for {inv_num}. Data retrieved successfully."
        messages.append(("tool", tool_resp, len(tool_resp) // 4))

    # Final assistant response
    reasoning = ar.summarized_reasoning or sc["description"]
    final_msg = f"Analysis complete. {reasoning}"
    messages.append(("assistant", final_msg, len(final_msg) // 4))

    created = 0
    for msg_idx, (role, content, tokens) in enumerate(messages):
        AgentMessage.objects.create(
            agent_run=ar,
            role=role,
            content=content,
            token_count=tokens,
            message_index=msg_idx,
        )
        created += 1

    stats["agent_messages"] += created


# ============================================================
# Helper: Create ToolCall records
# ============================================================

def _create_tool_calls(
    ar: AgentRun,
    tool_defs: dict[str, ToolDefinition],
    invoice,
    po,
    case: APCase,
    stats: dict,
):
    """Create ToolCall records for an agent run."""
    tool_names = _AGENT_TOOL_MAP.get(ar.agent_type, [])
    if not tool_names:
        return

    if ToolCall.objects.filter(agent_run=ar).exists():
        return

    # Pick first tool (primary tool for this agent)
    tool_name = tool_names[0]
    tool_def = tool_defs.get(tool_name)
    duration = _rng.randint(50, 800)

    input_payload = _build_tool_input(tool_name, invoice, po, case)
    output_payload = _build_tool_output(tool_name, invoice, po, case)

    ToolCall.objects.create(
        agent_run=ar,
        tool_definition=tool_def,
        tool_name=tool_name,
        status=ToolCallStatus.SUCCESS,
        input_payload=input_payload,
        output_payload=output_payload,
        duration_ms=duration,
    )
    stats["tool_calls"] += 1

    # Some agents call a second tool
    if len(tool_names) > 1 and _rng.random() > 0.4:
        tool_name_2 = tool_names[1]
        tool_def_2 = tool_defs.get(tool_name_2)
        ToolCall.objects.create(
            agent_run=ar,
            tool_definition=tool_def_2,
            tool_name=tool_name_2,
            status=ToolCallStatus.SUCCESS,
            input_payload=_build_tool_input(tool_name_2, invoice, po, case),
            output_payload=_build_tool_output(tool_name_2, invoice, po, case),
            duration_ms=_rng.randint(50, 500),
        )
        stats["tool_calls"] += 1


def _build_tool_input(tool_name: str, invoice, po, case) -> dict:
    """Build realistic tool input payload."""
    if tool_name == "invoice_details":
        return {"invoice_id": invoice.pk, "invoice_number": invoice.invoice_number}
    if tool_name == "po_lookup":
        return {"po_number": po.po_number if po else "", "vendor_id": invoice.vendor_id}
    if tool_name == "grn_lookup":
        return {"po_number": po.po_number if po else "", "include_line_items": True}
    if tool_name == "vendor_search":
        return {"query": invoice.raw_vendor_name or "", "fuzzy": True}
    if tool_name == "exception_list":
        return {"case_id": case.pk, "include_resolved": False}
    if tool_name == "reconciliation_summary":
        return {"case_id": case.pk, "format": "detailed"}
    return {}


def _build_tool_output(tool_name: str, invoice, po, case) -> dict:
    """Build realistic tool output payload."""
    if tool_name == "invoice_details":
        return {
            "invoice_number": invoice.invoice_number,
            "vendor": invoice.raw_vendor_name,
            "total": str(invoice.total_amount),
            "line_items": invoice.line_items.count(),
            "status": invoice.status,
        }
    if tool_name == "po_lookup":
        if po:
            return {"found": True, "po_number": po.po_number, "vendor": str(po.vendor) if po.vendor else "", "total": str(po.total_amount)}
        return {"found": False, "message": "No matching PO found"}
    if tool_name == "grn_lookup":
        return {"grns_found": _rng.randint(0, 3), "status": "retrieved"}
    if tool_name == "vendor_search":
        return {"matches": 1, "best_match": invoice.raw_vendor_name, "confidence": 0.95}
    if tool_name == "exception_list":
        return {"total_exceptions": _rng.randint(0, 4), "open": _rng.randint(0, 2)}
    if tool_name == "reconciliation_summary":
        return {"match_status": case.status or "N/A", "path": case.processing_path}
    return {}


# ============================================================
# Helper: Create DecisionLog records
# ============================================================

def _create_decision_logs(
    agent_runs: list[AgentRun],
    case: APCase,
    sc: dict,
    recon_result,
    invoice,
    trace_id: str,
    admin: User,
    stats: dict,
):
    """Create decision log entries for a case."""
    if DecisionLog.objects.filter(case_id=case.pk).exists():
        return

    decisions = []

    # 1. Path/mode selection decision
    decisions.append({
        "decision_type": "MODE_RESOLUTION",
        "decision": f"Processing path set to {sc['path']}",
        "rationale": f"Based on document analysis: {'PO found' if sc['path'] != 'NON_PO' else 'No PO reference detected'}. "
                     f"Mode: {sc.get('mode', sc['path'])}.",
        "confidence": 0.95 if sc["path"] != "NON_PO" else 0.88,
        "deterministic_flag": True,
        "rule_name": "ReconciliationModeResolver",
        "rule_version": "1.0",
        "recommendation_type": "",
    })

    # 2. Match determination decision (if reconciled)
    if sc.get("match"):
        decisions.append({
            "decision_type": "MATCH_DETERMINATION",
            "decision": f"Match status: {sc['match']}",
            "rationale": f"Reconciliation result for {invoice.invoice_number}. "
                         f"Exceptions: {', '.join(sc.get('exceptions', [])) or 'None'}.",
            "confidence": 0.95 if sc["match"] == "MATCHED" else 0.72,
            "deterministic_flag": sc["match"] == "MATCHED",
            "rule_name": "ToleranceEngine",
            "rule_version": "1.0",
            "recommendation_type": "",
        })

    # 3. Routing/disposition decision
    if sc.get("review_required"):
        rec_type = "SEND_TO_AP_REVIEW"
        if sc.get("priority") == "CRITICAL":
            rec_type = "ESCALATE_TO_MANAGER"

        decisions.append({
            "decision_type": "ROUTING_DECISION",
            "decision": f"Route to review: {rec_type}",
            "rationale": f"Case {case.case_number} requires manual review. "
                         f"Priority: {sc.get('priority', 'MEDIUM')}. "
                         f"Exceptions: {len(sc.get('exceptions', []))}.",
            "confidence": 0.88,
            "deterministic_flag": False,
            "rule_name": "PolicyEngine",
            "rule_version": "1.0",
            "recommendation_type": rec_type,
        })
    elif sc["status"] == "CLOSED":
        decisions.append({
            "decision_type": "AUTO_CLOSE",
            "decision": "Auto-close: within tolerance band",
            "rationale": f"All checks passed for {case.case_number}. No exceptions detected.",
            "confidence": 0.98,
            "deterministic_flag": True,
            "rule_name": "PolicyEngine.should_auto_close",
            "rule_version": "1.0",
            "recommendation_type": "AUTO_CLOSE",
        })

    # Pick a relevant agent_run to attach decisions to
    last_run = agent_runs[-1] if agent_runs else None

    for d in decisions:
        span = _span_id()
        DecisionLog.objects.create(
            agent_run=last_run,
            decision_type=d["decision_type"],
            decision=d["decision"],
            rationale=d["rationale"],
            confidence=d["confidence"],
            deterministic_flag=d["deterministic_flag"],
            rule_name=d["rule_name"],
            rule_version=d["rule_version"],
            recommendation_type=d["recommendation_type"],
            trace_id=trace_id,
            span_id=span,
            invoice_id=invoice.pk,
            case_id=case.pk,
            reconciliation_result_id=recon_result.pk if recon_result else None,
            actor_user_id=admin.pk,
            actor_primary_role="ADMIN",
            permission_checked="reconciliation.run",
            config_snapshot_json={
                "strict_tolerance": {"qty": 0.02, "price": 0.01, "amount": 0.01},
                "auto_close_tolerance": {"qty": 0.05, "price": 0.03, "amount": 0.03},
            },
        )
        stats["decision_logs"] += 1


# ============================================================
# Helper: Create AgentEscalation
# ============================================================

def _create_escalation(
    agent_run: AgentRun,
    recon_result,
    sc: dict,
    stats: dict,
):
    """Create escalation record for escalated cases."""
    if AgentEscalation.objects.filter(agent_run=agent_run).exists():
        return

    severity = ExceptionSeverity.HIGH
    if sc.get("priority") == "CRITICAL":
        severity = ExceptionSeverity.CRITICAL

    AgentEscalation.objects.create(
        agent_run=agent_run,
        reconciliation_result=recon_result,
        severity=severity,
        reason=f"Escalated: {sc['description']}. "
               f"Exceptions: {', '.join(sc.get('exceptions', []))}. "
               f"Requires Finance Manager approval.",
        suggested_assignee_role="FINANCE_MANAGER",
    )
    stats["escalations"] += 1


# ============================================================
# Helper: Create ProcessingLog entries
# ============================================================

def _create_processing_logs(
    case: APCase,
    sc: dict,
    invoice,
    recon_result,
    agent_runs: list[AgentRun],
    trace_id: str,
    admin: User,
    base_time,
    stats: dict,
):
    """Create structured processing log entries tracing the case lifecycle."""
    if ProcessingLog.objects.filter(case_id=case.pk).exists():
        return

    entries = [
        {
            "level": "INFO",
            "source": "DocumentUploadService",
            "event": "invoice.uploaded",
            "message": f"Invoice {invoice.invoice_number} uploaded for processing.",
            "service_name": "DocumentUploadService",
            "endpoint_name": "/api/v1/documents/upload/",
            "duration_ms": _rng.randint(200, 800),
            "success": True,
            "offset_min": 0,
        },
        {
            "level": "INFO",
            "source": "ExtractionService",
            "event": "extraction.started",
            "message": f"OCR extraction started for invoice {invoice.invoice_number}.",
            "service_name": "ExtractionService",
            "task_name": "apps.extraction.tasks.process_document_task",
            "duration_ms": _rng.randint(3000, 12000),
            "success": True,
            "offset_min": 2,
        },
        {
            "level": "INFO",
            "source": "ExtractionService",
            "event": "extraction.completed",
            "message": f"Extraction completed. Confidence: {invoice.extraction_confidence:.0%}. Lines: {invoice.line_items.count()}.",
            "service_name": "ExtractionService",
            "task_name": "apps.extraction.tasks.process_document_task",
            "duration_ms": _rng.randint(100, 500),
            "success": True,
            "offset_min": 5,
        },
    ]

    if sc["path"] in ("TWO_WAY", "THREE_WAY"):
        entries.append({
            "level": "INFO",
            "source": "ReconciliationRunnerService",
            "event": "reconciliation.started",
            "message": f"Reconciliation started for invoice {invoice.invoice_number}. Mode: {sc['path']}.",
            "service_name": "ReconciliationRunnerService",
            "task_name": "apps.reconciliation.tasks.run_reconciliation_task",
            "duration_ms": _rng.randint(1000, 5000),
            "success": True,
            "offset_min": 8,
        })
        entries.append({
            "level": "INFO",
            "source": "ReconciliationRunnerService",
            "event": "reconciliation.completed",
            "message": f"Reconciliation completed. Match: {sc.get('match', 'N/A')}.",
            "service_name": "ReconciliationRunnerService",
            "duration_ms": _rng.randint(50, 300),
            "success": True,
            "offset_min": 10,
        })

    # Agent pipeline log entry
    if agent_runs:
        entries.append({
            "level": "INFO",
            "source": "AgentOrchestrator",
            "event": "agent_pipeline.started",
            "message": f"Agent pipeline started for case {case.case_number}. Agents: {len(agent_runs)}.",
            "service_name": "AgentOrchestrator",
            "duration_ms": sum(ar.duration_ms or 0 for ar in agent_runs),
            "success": all(ar.status == AgentRunStatus.COMPLETED for ar in agent_runs),
            "offset_min": 12,
        })
        entries.append({
            "level": "INFO",
            "source": "AgentOrchestrator",
            "event": "agent_pipeline.completed",
            "message": f"Agent pipeline completed for case {case.case_number}.",
            "service_name": "AgentOrchestrator",
            "duration_ms": _rng.randint(50, 200),
            "success": True,
            "offset_min": 15,
        })

    # Add warning/error for exception cases
    if sc.get("exceptions"):
        exc_str = ", ".join(sc["exceptions"])
        entries.append({
            "level": "WARNING",
            "source": "ExceptionAnalysisAgent",
            "event": "exceptions.detected",
            "message": f"Exceptions detected: {exc_str}.",
            "service_name": "ExceptionAnalysisAgent",
            "duration_ms": _rng.randint(50, 200),
            "success": True,
            "offset_min": 14,
        })

    for entry in entries:
        offset = entry.pop("offset_min", 0)
        log_time = base_time + timedelta(minutes=offset)
        ProcessingLog.objects.create(
            level=entry["level"],
            source=entry["source"],
            event=entry["event"],
            message=entry["message"],
            details={"scenario": sc["tag"], "path": sc["path"]},
            invoice_id=invoice.pk,
            case_id=case.pk,
            reconciliation_result_id=recon_result.pk if recon_result else None,
            agent_run_id=agent_runs[-1].pk if agent_runs else None,
            user=admin,
            trace_id=trace_id,
            span_id=_span_id(),
            task_name=entry.get("task_name", ""),
            service_name=entry.get("service_name", ""),
            endpoint_name=entry.get("endpoint_name", ""),
            duration_ms=entry.get("duration_ms"),
            success=entry.get("success", True),
            actor_primary_role="ADMIN",
            permission_checked="reconciliation.run",
            access_granted=True,
            created_at=log_time,
        )
        stats["processing_logs"] += 1


# ============================================================
# Helper: Create ManualReviewAction records
# ============================================================

def _create_review_actions(
    case: APCase,
    sc: dict,
    recon_result,
    admin: User,
    users: dict[str, User],
    stats: dict,
):
    """Create manual review action records for reviewed cases."""
    try:
        ra = ReviewAssignment.objects.get(reconciliation_result=recon_result)
    except ReviewAssignment.DoesNotExist:
        return

    if ManualReviewAction.objects.filter(assignment=ra).exists():
        return

    reviewer = ra.assigned_to or admin
    exceptions = sc.get("exceptions", [])

    # For cases with exceptions, add a CORRECT_FIELD action
    if exceptions and sc["status"] in ("IN_REVIEW", "REVIEW_COMPLETED", "CLOSED", "READY_FOR_APPROVAL"):
        exc = exceptions[0]
        field_map = {
            "AMOUNT_MISMATCH": ("total_amount", "15,200.00", "15,350.00"),
            "PRICE_MISMATCH": ("unit_price", "45.00", "47.50"),
            "QTY_MISMATCH": ("quantity", "100", "95"),
            "TAX_MISMATCH": ("vat_amount", "2,280.00", "2,302.50"),
            "RECEIPT_SHORTAGE": ("received_qty", "100", "90"),
        }
        correction = field_map.get(exc)
        if correction:
            ManualReviewAction.objects.create(
                assignment=ra,
                performed_by=reviewer,
                action_type=ReviewActionType.CORRECT_FIELD,
                field_name=correction[0],
                old_value=correction[1],
                new_value=correction[2],
                reason=f"Corrected {correction[0]} per {exc} review.",
            )
            stats["review_actions"] += 1

    # For escalated cases, add ESCALATE action
    if sc["status"] == "ESCALATED":
        ManualReviewAction.objects.create(
            assignment=ra,
            performed_by=reviewer,
            action_type=ReviewActionType.ESCALATE,
            reason=f"Escalated to Finance Manager. {sc['description']}",
        )
        stats["review_actions"] += 1

    # For rejected cases, add REJECT action
    if sc["status"] == "REJECTED":
        ManualReviewAction.objects.create(
            assignment=ra,
            performed_by=reviewer,
            action_type=ReviewActionType.REJECT,
            reason=f"Invoice rejected. {sc['description']}",
        )
        stats["review_actions"] += 1

    # Add a comment action for reviewed cases
    if sc["status"] in ("IN_REVIEW", "REVIEW_COMPLETED", "CLOSED"):
        ManualReviewAction.objects.create(
            assignment=ra,
            performed_by=reviewer,
            action_type=ReviewActionType.ADD_COMMENT,
            reason=f"Review note: {sc['description']}",
        )
        stats["review_actions"] += 1


# ============================================================
# Helper: Enrich AuditEvents with trace/RBAC fields
# ============================================================

def _enrich_audit_events(
    case: APCase,
    trace_id: str,
    admin: User,
    invoice,
    recon_result,
):
    """Add trace IDs, RBAC context, and cross-references to existing AuditEvents."""
    events = AuditEvent.objects.filter(
        entity_type="APCase",
        entity_id=str(case.pk),
    )

    review_assignment = None
    try:
        if recon_result:
            review_assignment = ReviewAssignment.objects.get(
                reconciliation_result=recon_result,
            )
    except ReviewAssignment.DoesNotExist:
        pass

    # Status transition mapping
    status_transitions = {
        AuditEventType.INVOICE_UPLOADED: ("", "UPLOADED"),
        AuditEventType.EXTRACTION_COMPLETED: ("UPLOADED", "EXTRACTED"),
        AuditEventType.RECONCILIATION_STARTED: ("EXTRACTED", "RECONCILING"),
        AuditEventType.RECONCILIATION_COMPLETED: ("RECONCILING", "RECONCILED"),
        AuditEventType.REVIEW_ASSIGNED: ("RECONCILED", "IN_REVIEW"),
        AuditEventType.REVIEW_APPROVED: ("IN_REVIEW", "APPROVED"),
        AuditEventType.REVIEW_REJECTED: ("IN_REVIEW", "REJECTED"),
    }

    for event in events:
        span = _span_id()
        transition = status_transitions.get(event.event_type, ("", ""))

        event.trace_id = trace_id
        event.span_id = span
        event.actor_email = admin.email
        event.actor_primary_role = admin.role or "ADMIN"
        event.actor_roles_snapshot_json = ["ADMIN"]
        event.permission_checked = "invoices.view"
        event.permission_source = "ADMIN_BYPASS"
        event.access_granted = True
        event.invoice_id = invoice.pk
        event.case_id = case.pk
        event.reconciliation_result_id = recon_result.pk if recon_result else None
        event.review_assignment_id = review_assignment.pk if review_assignment else None
        event.status_before = transition[0]
        event.status_after = transition[1]
        event.duration_ms = _rng.randint(50, 3000)

        event.save(update_fields=[
            "trace_id", "span_id", "actor_email", "actor_primary_role",
            "actor_roles_snapshot_json", "permission_checked", "permission_source",
            "access_granted", "invoice_id", "case_id", "reconciliation_result_id",
            "review_assignment_id", "status_before", "status_after", "duration_ms",
        ])
