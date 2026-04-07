"""ProcurementAgentOrchestrator -- thin bridge between AnalysisRun and procurement agents.

This module is the Phase 1 architectural bridge that routes AI-augmented
procurement work through a standardised execution path. It does NOT replace
existing deterministic services. It wraps AI agent invocations so they are
consistent with the rest of the agentic platform.

Flow (Phase 1):
  AnalysisRun (existing)
    -> ProcurementAgentOrchestrator.run(run, agent_fn, agent_type, ...)
       -> build ProcurementAgentContext
       -> build/reuse ProcurementAgentMemory
       -> write ProcurementAgentExecutionRecord (start)
       -> audit event (start)
       -> Langfuse trace span (start)
       -> call agent_fn(context) -- agent does actual AI work
       -> update memory from output
       -> write ProcurementAgentExecutionRecord (complete)
       -> audit event (complete)
       -> Langfuse span (end)
       -> return ProcurementOrchestrationResult

Future hooks (NOT implemented in Phase 1):
  - Plug into shared AgentOrchestrator / ReasoningPlanner
  - Register procurement tools in shared ToolRegistry
  - Run DecisionLog entries via shared DecisionLogService
  - RBAC guardrails via AgentGuardrailsService
  These are intentional stubs -- see EXTENSION POINTS section below.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from django.utils import timezone

from apps.core.decorators import observed_service
from apps.core.enums import AnalysisRunStatus
from apps.core.trace import TraceContext
from apps.procurement.runtime.procurement_agent_context import ProcurementAgentContext
from apps.procurement.runtime.procurement_agent_memory import ProcurementAgentMemory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProcurementOrchestrationResult:
    """Structured result from an orchestrator-managed agent invocation."""
    agent_type: str = ""
    status: str = "pending"          # "completed" | "failed" | "skipped"
    output: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    reasoning_summary: str = ""
    error: str = ""
    duration_ms: int = 0
    execution_record_id: Optional[int] = None  # ProcurementAgentExecutionRecord.pk if created


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class ProcurementAgentOrchestrator:
    """Thin bridge orchestrator for procurement AI agents.

    Usage in a service:
        from apps.procurement.runtime.procurement_agent_orchestrator import (
            ProcurementAgentOrchestrator,
        )
        orchestrator = ProcurementAgentOrchestrator()
        orch_result = orchestrator.run(
            run=run,
            agent_type="recommendation",
            agent_fn=lambda ctx: RecommendationAgent.execute(...),
            request_user=None,
        )

    The agent_fn receives a ProcurementAgentContext and must return either:
    - A dict  with keys: reasoning_summary, confidence, output (arbitrary)
    - An object with .reasoning_summary / .confidence / .evidence attrs
    """

    @observed_service(
        "procurement.orchestrator.run",
        audit_event="PROCUREMENT_AGENT_RUN_STARTED",
        entity_type="AnalysisRun",
    )
    def run(
        self,
        *,
        run: Any,                              # AnalysisRun instance
        agent_type: str,
        agent_fn: Callable[[ProcurementAgentContext], Any],
        memory: Optional[ProcurementAgentMemory] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        request_user: Any = None,
    ) -> ProcurementOrchestrationResult:
        """Execute one procurement agent through the standard bridge path.

        Args:
            run:          AnalysisRun instance (must be in RUNNING status).
            agent_type:   Short string label for the agent (e.g. "recommendation").
            agent_fn:     Callable(ProcurementAgentContext) -> agent output dict/object.
            memory:       Shared ProcurementAgentMemory (created if None).
            extra_context: Additional key-value pairs merged into the context.
            request_user: Django User or None (system-triggered runs).

        Returns:
            ProcurementOrchestrationResult with status, output, confidence, etc.
        """
        start_time = time.monotonic()
        result = ProcurementOrchestrationResult(agent_type=agent_type)

        # Resolve trace context
        trace_ctx = TraceContext.get_current()
        trace_id = (getattr(run, "trace_id", "") or "") or (trace_ctx.trace_id if trace_ctx else "")
        span_id = trace_ctx.span_id if trace_ctx else ""

        # Build / reuse memory
        if memory is None:
            memory = ProcurementAgentMemory()

        # Build context
        ctx = self._build_context(
            run=run,
            memory=memory,
            trace_id=trace_id,
            span_id=span_id,
            request_user=request_user,
            extra_context=extra_context or {},
        )

        # Start audit + Langfuse span
        lf_span = self._start_trace_span(
            run=run, agent_type=agent_type, trace_id=trace_id, ctx=ctx,
        )
        self._audit_start(run=run, agent_type=agent_type, ctx=ctx, user=request_user)

        # Create execution record (best-effort)
        exec_record_id = self._create_execution_record(run=run, agent_type=agent_type, ctx=ctx)
        result.execution_record_id = exec_record_id

        try:
            # ==============================================================
            # Call the agent
            # ==============================================================
            raw_output = agent_fn(ctx)

            # Normalise output to a plain dict
            output_dict = self._normalise_output(raw_output)

            # Update shared memory
            memory.record_agent_output(agent_type, output_dict)

            # Fill result
            result.status = "completed"
            result.output = output_dict
            result.confidence = float(
                output_dict.get("confidence") or output_dict.get("confidence_score") or 0.0
            )
            result.reasoning_summary = (
                output_dict.get("reasoning_summary") or output_dict.get("reasoning") or ""
            )[:2000]

            # Complete execution record
            self._complete_execution_record(
                exec_record_id=exec_record_id,
                run=run,
                result=result,
            )
            self._audit_complete(run=run, agent_type=agent_type, result=result, user=request_user)

        except Exception as exc:
            logger.exception(
                "ProcurementAgentOrchestrator: agent %s failed for run %s: %s",
                agent_type, getattr(run, "pk", "?"), exc,
            )
            result.status = "failed"
            result.error = str(exc)[:500]

            self._fail_execution_record(
                exec_record_id=exec_record_id, run=run, error=result.error,
            )
            self._audit_failure(run=run, agent_type=agent_type, error=result.error, user=request_user)

        finally:
            result.duration_ms = int((time.monotonic() - start_time) * 1000)
            self._end_trace_span(lf_span=lf_span, result=result)

        return result

    # -----------------------------------------------------------------------
    # Context builder
    # -----------------------------------------------------------------------

    def _build_context(
        self,
        *,
        run: Any,
        memory: ProcurementAgentMemory,
        trace_id: str,
        span_id: str,
        request_user: Any,
        extra_context: Dict[str, Any],
    ) -> ProcurementAgentContext:
        """Build ProcurementAgentContext from an AnalysisRun."""
        procurement_request = run.request

        # Gather attributes dict (best-effort, avoids crashes if not available)
        attributes: Dict[str, Any] = {}
        try:
            from apps.procurement.services.request_service import AttributeService
            attributes = AttributeService.get_attributes_dict(procurement_request)
        except Exception:
            logger.debug("ProcurementAgentOrchestrator: could not load attributes for request %s", procurement_request.pk)

        # Gather quotation summaries (best-effort)
        quotation_summaries: List[Dict[str, Any]] = []
        try:
            for q in procurement_request.quotations.filter(is_active=True)[:10]:
                quotation_summaries.append({
                    "quotation_id": q.pk,
                    "vendor_name": q.vendor_name,
                    "quotation_number": q.quotation_number,
                    "total_amount": float(q.total_amount) if q.total_amount else None,
                    "currency": q.currency,
                    "extraction_status": q.extraction_status,
                })
        except Exception:
            logger.debug("ProcurementAgentOrchestrator: could not load quotations for request %s", procurement_request.pk)

        # RBAC context (best-effort, non-blocking)
        actor_user_id: Optional[int] = None
        actor_primary_role: str = ""
        actor_roles_snapshot: List[str] = []
        access_granted: bool = True
        permission_source: str = "system"

        if request_user and request_user.is_authenticated:
            actor_user_id = request_user.pk
            try:
                from apps.accounts.rbac_services import RBACService
                actor_primary_role = getattr(request_user, "role", "") or ""
                actor_roles_snapshot = list(
                    RBACService.get_active_role_codes(request_user)
                )
            except Exception:
                logger.debug("ProcurementAgentOrchestrator: could not load RBAC for user %s", request_user.pk)
            permission_source = "user_request"

        return ProcurementAgentContext(
            procurement_request_id=procurement_request.pk,
            analysis_run_id=run.pk,
            analysis_type=run.run_type,
            domain_code=procurement_request.domain_code,
            schema_code=procurement_request.schema_code,
            attributes=attributes,
            quotation_summaries=quotation_summaries,
            validation_context=extra_context.get("validation_context") or {},
            constraints=extra_context.get("constraints") or [],
            assumptions=extra_context.get("assumptions") or [],
            rule_result=extra_context.get("rule_result") or {},
            actor_user_id=actor_user_id,
            actor_primary_role=actor_primary_role,
            actor_roles_snapshot=actor_roles_snapshot,
            permission_checked="procurement.run_analysis",
            permission_source=permission_source,
            access_granted=access_granted,
            trace_id=trace_id,
            span_id=span_id,
            memory=memory,
        )

    # -----------------------------------------------------------------------
    # Output normalisation
    # -----------------------------------------------------------------------

    @staticmethod
    def _normalise_output(raw_output: Any) -> Dict[str, Any]:
        """Normalise agent output to a plain dict."""
        if raw_output is None:
            return {}
        if isinstance(raw_output, dict):
            return raw_output
        # Pydantic model, dataclass, or custom object
        if hasattr(raw_output, "model_dump"):
            return raw_output.model_dump()
        if hasattr(raw_output, "__dataclass_fields__"):
            from dataclasses import asdict
            return asdict(raw_output)
        # Last resort: attribute extraction
        return {
            "reasoning_summary": getattr(raw_output, "reasoning_summary", None) or getattr(raw_output, "reasoning", ""),
            "confidence": getattr(raw_output, "confidence", 0.0),
            "recommendation_type": getattr(raw_output, "recommendation_type", None),
        }

    # -----------------------------------------------------------------------
    # Execution record management (additive DB record on AnalysisRun)
    # -----------------------------------------------------------------------

    @staticmethod
    def _create_execution_record(
        *,
        run: Any,
        agent_type: str,
        ctx: ProcurementAgentContext,
    ) -> Optional[int]:
        """Create a ProcurementAgentExecutionRecord and return its pk."""
        try:
            from apps.procurement.models import ProcurementAgentExecutionRecord
            from apps.core.enums import AnalysisRunStatus
            record = ProcurementAgentExecutionRecord.objects.create(
                run=run,
                agent_type=agent_type,
                status=AnalysisRunStatus.RUNNING,
                input_snapshot=ctx.to_snapshot(),
                trace_id=ctx.trace_id,
                span_id=ctx.span_id,
                actor_user_id=ctx.actor_user_id,
                actor_primary_role=ctx.actor_primary_role,
            )
            return record.pk
        except Exception as exc:
            logger.debug("Could not create ProcurementAgentExecutionRecord: %s", exc)
            return None

    @staticmethod
    def _complete_execution_record(
        *,
        exec_record_id: Optional[int],
        run: Any,
        result: ProcurementOrchestrationResult,
    ) -> None:
        if not exec_record_id:
            return
        try:
            from apps.procurement.models import ProcurementAgentExecutionRecord
            from apps.core.enums import AnalysisRunStatus
            ProcurementAgentExecutionRecord.objects.filter(pk=exec_record_id).update(
                status=AnalysisRunStatus.COMPLETED,
                confidence_score=result.confidence,
                reasoning_summary=result.reasoning_summary[:2000],
                output_snapshot=result.output,
                completed_at=timezone.now(),
            )
        except Exception as exc:
            logger.debug("Could not complete ProcurementAgentExecutionRecord %s: %s", exec_record_id, exc)

    @staticmethod
    def _fail_execution_record(
        *,
        exec_record_id: Optional[int],
        run: Any,
        error: str,
    ) -> None:
        if not exec_record_id:
            return
        try:
            from apps.procurement.models import ProcurementAgentExecutionRecord
            from apps.core.enums import AnalysisRunStatus
            ProcurementAgentExecutionRecord.objects.filter(pk=exec_record_id).update(
                status=AnalysisRunStatus.FAILED,
                error_message=error,
                completed_at=timezone.now(),
            )
        except Exception as exc:
            logger.debug("Could not fail ProcurementAgentExecutionRecord %s: %s", exec_record_id, exc)

    # -----------------------------------------------------------------------
    # Audit hooks (reuse shared AuditService)
    # -----------------------------------------------------------------------

    @staticmethod
    def _audit_start(*, run: Any, agent_type: str, ctx: ProcurementAgentContext, user: Any) -> None:
        try:
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="AnalysisRun",
                entity_id=run.pk,
                event_type="PROCUREMENT_AGENT_RUN_STARTED",
                description=f"Procurement agent '{agent_type}' started for run {run.run_id}",
                user=user,
                agent=agent_type,
                metadata=ctx.to_snapshot(),
                status_before=AnalysisRunStatus.RUNNING,
                status_after=AnalysisRunStatus.RUNNING,
            )
        except Exception as exc:
            logger.debug("ProcurementAgentOrchestrator: audit_start failed (non-blocking): %s", exc)

    @staticmethod
    def _audit_complete(
        *,
        run: Any,
        agent_type: str,
        result: ProcurementOrchestrationResult,
        user: Any,
    ) -> None:
        try:
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="AnalysisRun",
                entity_id=run.pk,
                event_type="PROCUREMENT_AGENT_RUN_COMPLETED",
                description=f"Procurement agent '{agent_type}' completed (confidence={result.confidence:.2f})",
                user=user,
                agent=agent_type,
                metadata={"confidence": result.confidence, "duration_ms": result.duration_ms},
                status_before=AnalysisRunStatus.RUNNING,
                status_after=AnalysisRunStatus.RUNNING,
            )
        except Exception as exc:
            logger.debug("ProcurementAgentOrchestrator: audit_complete failed (non-blocking): %s", exc)

    @staticmethod
    def _audit_failure(
        *,
        run: Any,
        agent_type: str,
        error: str,
        user: Any,
    ) -> None:
        try:
            from apps.auditlog.services import AuditService
            AuditService.log_event(
                entity_type="AnalysisRun",
                entity_id=run.pk,
                event_type="PROCUREMENT_AGENT_RUN_FAILED",
                description=f"Procurement agent '{agent_type}' failed: {error[:200]}",
                user=user,
                agent=agent_type,
                metadata={"error": error},
                error_code="PROCUREMENT_AGENT_FAILED",
            )
        except Exception as exc:
            logger.debug("ProcurementAgentOrchestrator: audit_failure failed (non-blocking): %s", exc)

    # -----------------------------------------------------------------------
    # Langfuse tracing (fail-silent, same style as rest of platform)
    # -----------------------------------------------------------------------

    @staticmethod
    def _start_trace_span(
        *,
        run: Any,
        agent_type: str,
        trace_id: str,
        ctx: ProcurementAgentContext,
    ) -> Any:
        try:
            from apps.core.langfuse_client import start_span, start_trace
            lf_trace = start_trace(
                trace_id or f"procurement-{run.pk}",
                f"procurement_agent_{agent_type}",
                metadata={
                    "run_id": str(getattr(run, "run_id", run.pk)),
                    "run_type": getattr(run, "run_type", ""),
                    "agent_type": agent_type,
                    "domain_code": ctx.domain_code,
                },
            )
            return lf_trace
        except Exception:
            return None

    @staticmethod
    def _end_trace_span(*, lf_span: Any, result: ProcurementOrchestrationResult) -> None:
        try:
            if lf_span:
                from apps.core.langfuse_client import end_span, score_trace
                end_span(
                    lf_span,
                    output={
                        "status": result.status,
                        "confidence": result.confidence,
                        "error": result.error or None,
                    },
                    level="ERROR" if result.status == "failed" else "DEFAULT",
                )
                score_trace(
                    getattr(lf_span, "trace_id", ""),
                    "procurement_agent_confidence",
                    result.confidence,
                    comment=f"agent_type={result.agent_type} status={result.status}",
                    span=lf_span,
                )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# EXTENSION POINTS -- Future Phase 2+ hooks (stubs only, not implemented)
# ---------------------------------------------------------------------------

class _ProcurementPlannerStub:
    """Placeholder for future ReasoningPlanner integration.

    In Phase 2, replace this with a real planner that:
    - Calls the shared ReasoningPlanner / PolicyEngine
    - Decides which procurement agents to run and in what order
    - Supports multi-agent chaining (recommendation + compliance + benchmark)

    For Phase 1, the orchestrator always runs a single agent_fn per .run() call.
    The caller (service) decides what to call.
    """

    @staticmethod
    def plan(ctx: ProcurementAgentContext) -> List[str]:  # noqa: F821
        """Return ordered list of agent_type strings to execute.

        Phase 1: not implemented -- services call orchestrator.run() directly.
        Phase 2: replace with LLM-driven or policy-driven multi-agent plan.
        """
        raise NotImplementedError("ProcurementPlanner is a Phase 2 feature")


class _ProcurementToolRegistryStub:
    """Placeholder for future ToolRegistry integration.

    In Phase 2, register procurement tools here and route agent tool calls
    through the shared ToolRegistry (apps.tools.registry.base.ToolRegistry).

    Available extension points identified in Phase 1:
    - market_benchmark_lookup  : resolve benchmark prices for a line item
    - vendor_catalog_lookup    : search vendor product catalog
    - standards_compliance_lookup : check domain regulatory standards
    - erp_reference_lookup     : use shared ERPResolutionService facade
    """

    REGISTERED_TOOLS: List[str] = [
        "market_benchmark_lookup",
        "vendor_catalog_lookup",
        "standards_compliance_lookup",
        "erp_reference_lookup",
    ]

    @staticmethod
    def execute(tool_name: str, params: Dict[str, Any]) -> Any:
        """Phase 1 stub -- tools not yet wired.

        To enable a tool:
        1. Create a class in apps/tools/registry/tools.py extending BaseTool
        2. Decorate with @register_tool
        3. Set required_permission
        4. Replace this stub call with ToolRegistry.execute(tool_name, params)
        """
        raise NotImplementedError(
            f"Tool '{tool_name}' is defined as a Phase 2 extension point. "
            "Implement in apps/tools/registry/tools.py and wire to ToolRegistry."
        )
