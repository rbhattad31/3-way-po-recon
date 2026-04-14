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

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from django.utils import timezone
from django.core.exceptions import PermissionDenied

from apps.core.decorators import observed_service
from apps.core.enums import AnalysisRunStatus
from apps.core.trace import TraceContext
from apps.agents.services.base_agent import BaseAgent
from apps.agents.services.guardrails_service import AgentGuardrailsService
from apps.procurement.runtime.procurement_agent_context import ProcurementAgentContext
from apps.procurement.runtime.procurement_agent_memory import ProcurementAgentMemory
from apps.procurement.runtime.procurement_planner import ProcurementPlanner

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

        # ------------------------------------------------------------------
        # Centralized authorization (fail-closed)
        # ------------------------------------------------------------------
        actor = AgentGuardrailsService.resolve_actor(request_user)
        orchestrate_allowed = (
            AgentGuardrailsService.authorize_orchestration(actor)
            or actor.has_permission("procurement.orchestrate")
            or actor.has_permission("procurement.run_analysis")
        )
        if self._is_admin_bypass(actor):
            orchestrate_allowed = True
        # Compatibility path for fresh/dev/test databases before RBAC seed runs.
        if not orchestrate_allowed and self._allow_unseeded_rbac_fallback(actor):
            orchestrate_allowed = True
        AgentGuardrailsService.log_guardrail_decision(
            user=actor,
            action="procurement_orchestrate",
            permission_code="agents.orchestrate|procurement.orchestrate|procurement.run_analysis",
            granted=orchestrate_allowed,
            entity_type="AnalysisRun",
            entity_id=getattr(run, "pk", None),
            metadata={"agent_type": agent_type},
        )
        if not orchestrate_allowed:
            raise PermissionDenied("Permission denied: procurement orchestration is not authorized.")

        permission_agent_type = self._resolve_guardrail_agent_type(agent_type)
        per_agent_allowed = AgentGuardrailsService.authorize_agent(actor, permission_agent_type)
        if self._is_admin_bypass(actor):
            per_agent_allowed = True
        if not per_agent_allowed and self._allow_unseeded_rbac_fallback(actor):
            per_agent_allowed = True
        AgentGuardrailsService.log_guardrail_decision(
            user=actor,
            action="procurement_agent_execute",
            permission_code=f"agents.run_{permission_agent_type.lower()}",
            granted=per_agent_allowed,
            entity_type="AnalysisRun",
            entity_id=getattr(run, "pk", None),
            metadata={"agent_type": agent_type, "mapped_agent_type": permission_agent_type},
        )
        if not per_agent_allowed:
            raise PermissionDenied(f"Permission denied: execution of agent '{permission_agent_type}' is not authorized.")

        scope_allowed = self._authorize_procurement_scope(
            actor=actor,
            run=run,
            extra_context=extra_context or {},
        )
        if not scope_allowed:
            raise PermissionDenied("Permission denied: request is outside actor data scope.")

        # Resolve trace context
        trace_ctx = TraceContext.get_current()
        trace_id = (getattr(run, "trace_id", "") or "") or (trace_ctx.trace_id if trace_ctx else "")
        span_id = trace_ctx.span_id if trace_ctx else ""

        # Build / reuse memory
        if memory is None:
            memory = ProcurementAgentMemory()

        # Duplicate-run guard: prevent concurrent execution of same agent for the same AnalysisRun.
        try:
            from apps.procurement.models import ProcurementAgentExecutionRecord

            duplicate_running = ProcurementAgentExecutionRecord.objects.filter(
                run=run,
                agent_type=agent_type,
                status=AnalysisRunStatus.RUNNING,
            ).exists()
            if duplicate_running:
                result.status = "skipped"
                result.error = (
                    f"Duplicate run guard: agent '{agent_type}' is already running "
                    f"for AnalysisRun {getattr(run, 'pk', '?')}."
                )
                return result
        except Exception:
            logger.debug("Duplicate-run guard failed open (non-fatal)", exc_info=True)

        # Build context
        ctx = self._build_context(
            run=run,
            memory=memory,
            trace_id=trace_id,
            span_id=span_id,
            request_user=actor,
            extra_context=extra_context or {},
        )

        # Start audit + Langfuse span
        lf_span = self._start_trace_span(
            run=run, agent_type=agent_type, trace_id=trace_id, ctx=ctx,
        )
        self._audit_start(run=run, agent_type=agent_type, ctx=ctx, user=actor)

        # Create execution record (best-effort)
        exec_record_id = self._create_execution_record(run=run, agent_type=agent_type, ctx=ctx)
        result.execution_record_id = exec_record_id
        agent_run_id = self._create_agent_run_mirror(
            run=run,
            requested_agent_type=agent_type,
            mapped_agent_type=permission_agent_type,
            ctx=ctx,
            actor=actor,
        )

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
            self._complete_agent_run_mirror(agent_run_id=agent_run_id, result=result)
            self._audit_complete(run=run, agent_type=agent_type, result=result, user=actor)

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
            self._fail_agent_run_mirror(agent_run_id=agent_run_id, error=result.error)
            self._audit_failure(run=run, agent_type=agent_type, error=result.error, user=actor)

        finally:
            result.duration_ms = int((time.monotonic() - start_time) * 1000)
            self._update_agent_run_duration(agent_run_id=agent_run_id, duration_ms=result.duration_ms)
            self._end_trace_span(lf_span=lf_span, result=result)

        return result

    def run_planned(
        self,
        *,
        run: Any,
        agent_fn_map: Dict[str, Callable[[ProcurementAgentContext], Any]],
        memory: Optional[ProcurementAgentMemory] = None,
        extra_context: Optional[Dict[str, Any]] = None,
        request_user: Any = None,
    ) -> List[ProcurementOrchestrationResult]:
        """Execute a planner-produced sequence of procurement agents.

        This is a deterministic planner-enabled path that can be used by services
        and tasks to run multi-agent flows in a single orchestration call.
        """
        if memory is None:
            memory = ProcurementAgentMemory()

        trace_ctx = TraceContext.get_current()
        trace_id = (getattr(run, "trace_id", "") or "") or (trace_ctx.trace_id if trace_ctx else "")
        span_id = trace_ctx.span_id if trace_ctx else ""
        actor = AgentGuardrailsService.resolve_actor(request_user)

        plan_ctx = self._build_context(
            run=run,
            memory=memory,
            trace_id=trace_id,
            span_id=span_id,
            request_user=actor,
            extra_context=extra_context or {},
        )

        # Phase 5: delegate to real standalone ProcurementPlanner
        _plan = ProcurementPlanner.plan_for_context(plan_ctx)
        planned_agents = _plan.agents
        outputs: List[ProcurementOrchestrationResult] = []
        for planned_agent in planned_agents:
            agent_fn = agent_fn_map.get(planned_agent)
            if not agent_fn:
                outputs.append(
                    ProcurementOrchestrationResult(
                        agent_type=planned_agent,
                        status="skipped",
                        error=f"No agent function mapped for '{planned_agent}'.",
                    )
                )
                continue

            outputs.append(
                self.run(
                    run=run,
                    agent_type=planned_agent,
                    agent_fn=agent_fn,
                    memory=memory,
                    extra_context=extra_context or {},
                    request_user=request_user,
                )
            )

        return outputs

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

    @staticmethod
    def _resolve_guardrail_agent_type(agent_type: str) -> str:
        """Map procurement runtime labels to centralized guardrail agent names."""
        lowered = (agent_type or "").lower()
        if lowered.startswith("cost_item_"):
            return "PROCUREMENT_BENCHMARK"
        if "recommendation" in lowered:
            return "PROCUREMENT_RECOMMENDATION"
        if "validation" in lowered:
            return "PROCUREMENT_VALIDATION"
        if "compliance" in lowered:
            return "PROCUREMENT_COMPLIANCE"
        if "market" in lowered:
            return "PROCUREMENT_MARKET_INTELLIGENCE"
        return "PROCUREMENT_RECOMMENDATION"

    @staticmethod
    def _authorize_procurement_scope(*, actor: Any, run: Any, extra_context: Dict[str, Any]) -> bool:
        """Enforce tenant and scope_json dimensions for procurement runs.

        Procurement currently does not persist business_unit/vendor_id directly on
        ProcurementRequest, so this method uses:
        - tenant from request.tenant
        - optional business_unit/vendor_id from extra_context when available
        """
        request_obj = getattr(run, "request", None)
        request_tenant_id = getattr(request_obj, "tenant_id", None)
        actor_company_id = getattr(actor, "company_id", None)

        # Tenant isolation check for non-platform-admin users.
        if not getattr(actor, "is_platform_admin", False):
            if request_tenant_id and actor_company_id and request_tenant_id != actor_company_id:
                AgentGuardrailsService.log_guardrail_decision(
                    user=actor,
                    action="procurement_data_scope_check",
                    permission_code="agents.data_scope",
                    granted=False,
                    entity_type="AnalysisRun",
                    entity_id=getattr(run, "pk", None),
                    metadata={
                        "reason": "tenant_mismatch",
                        "request_tenant_id": request_tenant_id,
                        "actor_company_id": actor_company_id,
                    },
                )
                return False

        actor_scope = AgentGuardrailsService.get_actor_scope(actor)
        requested_business_unit = extra_context.get("business_unit")
        requested_vendor_id = extra_context.get("vendor_id")

        allowed_business_units = actor_scope.get("allowed_business_units")
        allowed_vendor_ids = actor_scope.get("allowed_vendor_ids")

        if allowed_business_units is not None and requested_business_unit is not None:
            if requested_business_unit not in allowed_business_units:
                AgentGuardrailsService.log_guardrail_decision(
                    user=actor,
                    action="procurement_data_scope_check",
                    permission_code="agents.data_scope",
                    granted=False,
                    entity_type="AnalysisRun",
                    entity_id=getattr(run, "pk", None),
                    metadata={
                        "reason": "business_unit_out_of_scope",
                        "requested_business_unit": requested_business_unit,
                        "allowed_business_units": allowed_business_units,
                    },
                )
                return False

        if allowed_vendor_ids is not None and requested_vendor_id is not None:
            if requested_vendor_id not in allowed_vendor_ids:
                AgentGuardrailsService.log_guardrail_decision(
                    user=actor,
                    action="procurement_data_scope_check",
                    permission_code="agents.data_scope",
                    granted=False,
                    entity_type="AnalysisRun",
                    entity_id=getattr(run, "pk", None),
                    metadata={
                        "reason": "vendor_out_of_scope",
                        "requested_vendor_id": requested_vendor_id,
                        "allowed_vendor_ids": allowed_vendor_ids,
                    },
                )
                return False

        AgentGuardrailsService.log_guardrail_decision(
            user=actor,
            action="procurement_data_scope_check",
            permission_code="agents.data_scope",
            granted=True,
            entity_type="AnalysisRun",
            entity_id=getattr(run, "pk", None),
            metadata={
                "request_tenant_id": request_tenant_id,
                "actor_company_id": actor_company_id,
                "requested_business_unit": requested_business_unit,
                "requested_vendor_id": requested_vendor_id,
            },
        )
        return True

    @staticmethod
    def _allow_unseeded_rbac_fallback(actor: Any) -> bool:
        """Return True only when RBAC catalog appears unseeded.

        This keeps fail-closed behavior in real environments while preventing
        false denials in test/bootstrap databases where permission rows are not
        loaded yet.
        """
        try:
            from apps.accounts.rbac_models import Permission

            # If RBAC has at least one permission row, enforce strict checks.
            if Permission.objects.exists():
                return False
        except Exception:
            return False

        # No seeded permissions: allow only authenticated/synthetic system actors.
        return bool(getattr(actor, "is_authenticated", False) or getattr(actor, "email", "") == "system-agent@internal")

    @staticmethod
    def _is_admin_bypass(actor: Any) -> bool:
        """Return True for platform/admin/system-agent actors with bypass semantics.

        SYSTEM_AGENT is a least-privilege identity designed to run procurement
        pipelines autonomously from Celery tasks.  It already has scoped permissions
        seeded by seed_rbac, but we bypass the per-agent check here to avoid
        needing a DB round-trip just to confirm the role that is always granted.
        """
        if getattr(actor, "is_platform_admin", False):
            return True
        role_code = str(getattr(actor, "role", "") or "").upper()
        if role_code in {"ADMIN", "SUPER_ADMIN", "SYSTEM_AGENT"}:
            return True
        # Also match by email for safety (guards against stale .role field)
        if getattr(actor, "email", "") == "system-agent@internal":
            return True
        return False

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
    def _create_agent_run_mirror(
        *,
        run: Any,
        requested_agent_type: str,
        mapped_agent_type: str,
        ctx: ProcurementAgentContext,
        actor: Any,
    ) -> Optional[int]:
        """Create a tenant-scoped AgentRun mirror for `/agents/runs/` observability."""
        try:
            from apps.agents.models import AgentRun
            from apps.core.enums import AgentRunStatus

            agent_run = AgentRun.objects.create(
                tenant=getattr(run, "tenant", None) or getattr(getattr(run, "request", None), "tenant", None),
                agent_type=mapped_agent_type,
                status=AgentRunStatus.RUNNING,
                confidence=0.0,
                llm_model_used="unknown",
                input_payload={
                    "source": "procurement_orchestrator",
                    "analysis_run_id": str(getattr(run, "run_id", "")),
                    "analysis_run_pk": getattr(run, "pk", None),
                    "procurement_request_id": str(getattr(getattr(run, "request", None), "request_id", "")),
                    "requested_agent_type": requested_agent_type,
                },
                trace_id=ctx.trace_id,
                span_id=ctx.span_id,
                invocation_reason=f"Procurement orchestrator: {requested_agent_type}",
                actor_user_id=ctx.actor_user_id,
                actor_primary_role=(ctx.actor_primary_role or "SYSTEM_AGENT"),
                access_granted=True,
                started_at=timezone.now(),
            )
            return agent_run.pk
        except Exception as exc:
            logger.debug("Could not create AgentRun mirror for procurement orchestrator: %s", exc)
            return None

    @staticmethod
    def _complete_agent_run_mirror(*, agent_run_id: Optional[int], result: ProcurementOrchestrationResult) -> None:
        if not agent_run_id:
            return
        try:
            from apps.agents.models import AgentRun
            from apps.core.enums import AgentRunStatus

            llm_model_used = ProcurementAgentOrchestrator._extract_llm_model(result.output)
            prompt_tokens, completion_tokens, total_tokens = ProcurementAgentOrchestrator._extract_token_usage(result.output)

            update_kwargs: Dict[str, Any] = {
                "status": AgentRunStatus.COMPLETED,
                "confidence": result.confidence if result.confidence is not None else 0.0,
                "output_payload": ProcurementAgentOrchestrator._json_safe(result.output),
                "summarized_reasoning": BaseAgent._sanitise_text(result.reasoning_summary)[:2000],
                "completed_at": timezone.now(),
            }
            if llm_model_used:
                update_kwargs["llm_model_used"] = llm_model_used
            else:
                update_kwargs["llm_model_used"] = "unknown"
            if prompt_tokens is not None:
                update_kwargs["prompt_tokens"] = prompt_tokens
            if completion_tokens is not None:
                update_kwargs["completion_tokens"] = completion_tokens
            if total_tokens is not None:
                update_kwargs["total_tokens"] = total_tokens

            AgentRun.objects.filter(pk=agent_run_id).update(**update_kwargs)

            agent_run = AgentRun.objects.filter(pk=agent_run_id).first()
            if agent_run:
                BaseAgent._calculate_actual_cost(agent_run)
                agent_run.save(update_fields=["actual_cost_usd", "updated_at"])
        except Exception as exc:
            logger.debug("Could not complete AgentRun mirror %s: %s", agent_run_id, exc)

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            return json.loads(json.dumps(value, default=str))
        except Exception:
            return value

    @staticmethod
    def _extract_llm_model(output: Optional[Dict[str, Any]]) -> str:
        """Best-effort model extraction from agent output payload."""
        if not isinstance(output, dict):
            return ""

        for key in ("llm_model_used", "model_used", "llm_model", "model_name", "model"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

        usage = output.get("llm_usage")
        if isinstance(usage, dict):
            for key in ("model", "model_name"):
                value = usage.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return ""

    @staticmethod
    def _extract_token_usage(output: Optional[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        """Best-effort token usage extraction from agent output payload."""
        if not isinstance(output, dict):
            return None, None, None

        def _to_int(value: Any) -> Optional[int]:
            if value is None:
                return None
            try:
                parsed = int(value)
                return parsed if parsed >= 0 else None
            except (TypeError, ValueError):
                return None

        prompt_tokens = _to_int(output.get("prompt_tokens"))
        completion_tokens = _to_int(output.get("completion_tokens"))
        total_tokens = _to_int(output.get("total_tokens"))

        usage = output.get("llm_usage")
        if isinstance(usage, dict):
            if prompt_tokens is None:
                prompt_tokens = _to_int(usage.get("prompt_tokens"))
            if completion_tokens is None:
                completion_tokens = _to_int(usage.get("completion_tokens"))
            if total_tokens is None:
                total_tokens = _to_int(usage.get("total_tokens"))

        usage_alt = output.get("usage")
        if isinstance(usage_alt, dict):
            if prompt_tokens is None:
                prompt_tokens = _to_int(usage_alt.get("prompt_tokens"))
            if completion_tokens is None:
                completion_tokens = _to_int(usage_alt.get("completion_tokens"))
            if total_tokens is None:
                total_tokens = _to_int(usage_alt.get("total_tokens"))

        if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

        return prompt_tokens, completion_tokens, total_tokens

    @staticmethod
    def _fail_agent_run_mirror(*, agent_run_id: Optional[int], error: str) -> None:
        if not agent_run_id:
            return
        try:
            from apps.agents.models import AgentRun
            from apps.core.enums import AgentRunStatus

            AgentRun.objects.filter(pk=agent_run_id).update(
                status=AgentRunStatus.FAILED,
                confidence=0.0,
                llm_model_used="unknown",
                error_message=error,
                completed_at=timezone.now(),
            )
        except Exception as exc:
            logger.debug("Could not fail AgentRun mirror %s: %s", agent_run_id, exc)

    @staticmethod
    def _update_agent_run_duration(*, agent_run_id: Optional[int], duration_ms: int) -> None:
        if not agent_run_id:
            return
        try:
            from apps.agents.models import AgentRun

            AgentRun.objects.filter(pk=agent_run_id).update(duration_ms=duration_ms)
        except Exception as exc:
            logger.debug("Could not update AgentRun mirror duration %s: %s", agent_run_id, exc)

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
                reasoning_summary=BaseAgent._sanitise_text(result.reasoning_summary)[:2000],
                output_snapshot=ProcurementAgentOrchestrator._json_safe(result.output),
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
    """Deterministic baseline planner for procurement agent sequencing.

    This replaces the hard NotImplementedError path with a safe fallback plan
    until full shared ReasoningPlanner/PolicyEngine integration is completed.
    """

    PLAN_BY_ANALYSIS_TYPE: Dict[str, List[str]] = {
        "RECOMMENDATION": ["recommendation", "compliance", "market_intelligence"],
        "BENCHMARK": ["cost_analysis"],
        "VALIDATION": ["validation_augmentation"],
    }

    @staticmethod
    def plan(ctx: ProcurementAgentContext) -> List[str]:  # noqa: F821
        """Return ordered list of agent_type strings to execute.

        Selection order:
        1) Explicit sequence from context (if provided)
        2) Deterministic mapping by analysis type
        3) Safe fallback to recommendation
        """
        explicit = (ctx.extra_context or {}).get("planned_agents")
        if isinstance(explicit, list) and explicit:
            deduped: List[str] = []
            for value in explicit:
                name = str(value or "").strip().lower()
                if name and name not in deduped:
                    deduped.append(name)
            if deduped:
                return deduped

        analysis_type = str(getattr(ctx, "analysis_type", "") or "").upper()
        mapped = _ProcurementPlannerStub.PLAN_BY_ANALYSIS_TYPE.get(analysis_type)
        if mapped:
            return list(mapped)

        return ["recommendation"]


class _ProcurementToolRegistryStub:
    """Placeholder for future ToolRegistry integration.

    In Phase 2, register procurement tools here and route agent tool calls
    through the shared ToolRegistry (apps.tools.registry.base.ToolRegistry).

    Available extension points identified in Phase 1:
    - market_price_lookup      : resolve market prices for a line item
    - vendor_catalog_lookup    : search vendor product catalog
    - standards_compliance_lookup : check domain regulatory standards
    - erp_reference_lookup     : use shared ERPResolutionService facade
    """

    REGISTERED_TOOLS: List[str] = [
        "market_price_lookup",
        "vendor_catalog_lookup",
        "standards_compliance_lookup",
        "erp_reference_lookup",
    ]

    @staticmethod
    def execute(tool_name: str, params: Dict[str, Any]) -> Any:
        """Execute registered procurement tool with standard permission gating."""
        from apps.tools.registry.base import ToolRegistry

        tool = ToolRegistry.get(tool_name)
        if tool is None:
            raise NotImplementedError(f"Tool '{tool_name}' is not registered in ToolRegistry.")

        actor = AgentGuardrailsService.resolve_actor(params.get("request_user"))
        if not AgentGuardrailsService.authorize_tool(actor, tool_name):
            AgentGuardrailsService.log_guardrail_decision(
                user=actor,
                action="procurement_tool_execute",
                permission_code=f"tool:{tool_name}",
                granted=False,
                entity_type="ToolCall",
                entity_id=None,
                metadata={"tool_name": tool_name},
            )
            raise PermissionDenied(f"Permission denied: tool '{tool_name}' is not authorized.")

        AgentGuardrailsService.log_guardrail_decision(
            user=actor,
            action="procurement_tool_execute",
            permission_code=f"tool:{tool_name}",
            granted=True,
            entity_type="ToolCall",
            entity_id=None,
            metadata={"tool_name": tool_name},
        )

        safe_params = dict(params)
        safe_params.pop("request_user", None)
        result = tool.execute(**safe_params)
        if not result.success:
            raise RuntimeError(result.error or f"Tool '{tool_name}' execution failed")
        return result.data
