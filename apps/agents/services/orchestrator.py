"""Agent orchestrator — sequences agent execution based on the policy engine plan.

Flow:
  1. Load reconciliation result + exceptions
  2. Ask the policy engine for an agent plan
  3. Execute agents in sequence, passing context forward
  4. Record recommendations and decisions
  5. Return aggregated orchestration result

RBAC enforcement:
  - Every execution resolves an actor (user or system-agent)
  - Orchestration requires ``agents.orchestrate`` permission
  - Each agent requires its per-type permission
  - Auto-close and escalation are protected actions
  - All guardrail decisions are audited
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from django.db import IntegrityError
from django.utils import timezone

from apps.agents.models import AgentEscalation, AgentOrchestrationRun, AgentRecommendation, AgentRun
from apps.agents.services.agent_memory import AgentMemory
from apps.agents.services.agent_classes import AGENT_CLASS_REGISTRY
from apps.agents.services.base_agent import AgentContext, BaseAgent
from apps.agents.services.decision_log_service import DecisionLogService
from apps.agents.services.deterministic_resolver import DeterministicResolver
from apps.agents.services.guardrails_service import (
    ACTION_PERMISSIONS,
    AGENT_PERMISSIONS,
    ORCHESTRATE_PERMISSION,
    AgentGuardrailsService,
)
from apps.agents.services.policy_engine import PolicyEngine
from apps.agents.services.reasoning_planner import ReasoningPlanner
from apps.core.enums import AgentRunStatus, AgentType, ExceptionSeverity, MatchStatus, RecommendationType
from apps.core.decorators import observed_service
from apps.core.metrics import MetricsService

# Only these agents should emit formal recommendations to avoid duplicates.
# Other agents contribute analysis/reasoning via summarized_reasoning on the run.
_RECOMMENDING_AGENTS = {AgentType.REVIEW_ROUTING, AgentType.CASE_SUMMARY}

# Agents whose findings can be applied back to re-run deterministic matching.
_FEEDBACK_AGENTS = {AgentType.PO_RETRIEVAL}

from apps.reconciliation.models import ReconciliationResult

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationResult:
    """Aggregated outcome of the full agentic pipeline."""
    reconciliation_result_id: int = 0
    agents_executed: List[str] = field(default_factory=list)
    agent_runs: List[AgentRun] = field(default_factory=list)
    final_recommendation: Optional[str] = None
    final_confidence: float = 0.0
    final_reasoning: str = ""
    skipped: bool = False
    skip_reason: str = ""
    error: str = ""
    plan_source: str = ""
    plan_confidence: float = 0.0


class _AgentRunOutputProxy:
    """Lightweight read-only adapter that presents an AgentRun DB record
    with the same interface expected by AgentMemory.record_agent_output().
    """

    def __init__(self, agent_run: AgentRun) -> None:
        self.reasoning: str = agent_run.summarized_reasoning or ""
        payload = agent_run.output_payload or {}
        self.recommendation_type: Optional[str] = payload.get("recommendation_type")
        self.confidence: float = float(agent_run.confidence or 0.0)
        self.evidence: dict = payload.get("evidence") or {}


class AgentOrchestrator:
    """Orchestrates the agentic layer for a single ReconciliationResult."""

    def __init__(self):
        self.policy = ReasoningPlanner()
        self.decision_service = DecisionLogService()
        self.resolver = DeterministicResolver()

    @observed_service("agents.orchestrator.execute", audit_event="AGENT_PIPELINE_STARTED", entity_type="ReconciliationResult")
    def execute(self, result: ReconciliationResult, request_user=None) -> OrchestrationResult:
        """Run the full agentic pipeline for one reconciliation result.

        Args:
            result: The ReconciliationResult to process.
            request_user: The Django User who triggered the pipeline, or None
                          for system-initiated (Celery, auto-trigger).
        """
        orch_result = OrchestrationResult(reconciliation_result_id=result.pk)

        # --- RBAC: resolve actor and validate orchestration permission ---
        actor = AgentGuardrailsService.resolve_actor(request_user)
        rbac_snapshot = AgentGuardrailsService.build_rbac_snapshot(actor)

        if not AgentGuardrailsService.authorize_orchestration(actor):
            AgentGuardrailsService.log_guardrail_decision(
                user=actor,
                action="orchestrate_pipeline",
                permission_code=ORCHESTRATE_PERMISSION,
                granted=False,
                entity_type="ReconciliationResult",
                entity_id=result.pk,
            )
            orch_result.error = f"Permission denied: {ORCHESTRATE_PERMISSION}"
            return orch_result

        AgentGuardrailsService.log_guardrail_decision(
            user=actor,
            action="orchestrate_pipeline",
            permission_code=ORCHESTRATE_PERMISSION,
            granted=True,
            entity_type="ReconciliationResult",
            entity_id=result.pk,
        )

        # Set trace context with RBAC metadata for downstream audit events
        trace_ctx = AgentGuardrailsService.build_trace_context_for_agent(
            actor, permission_checked=ORCHESTRATE_PERMISSION, access_granted=True,
        )
        from apps.core.trace import TraceContext
        TraceContext.set_current(trace_ctx)

        # 1. Build the plan
        plan = self.policy.plan(result)
        orch_result.plan_source = plan.plan_source
        orch_result.plan_confidence = plan.plan_confidence

        # Duplicate-run protection: reject if a RUNNING orchestration exists.
        live = AgentOrchestrationRun.objects.filter(
            reconciliation_result=result,
            status=AgentOrchestrationRun.Status.RUNNING,
        ).first()
        if live:
            logger.warning(
                "Orchestration skipped for result %s: orchestration run #%s is still RUNNING.",
                result.pk, live.pk,
            )
            orch_result.skipped = True
            orch_result.skip_reason = (
                f"Duplicate orchestration prevented: run #{live.pk} is active."
            )
            return orch_result

        if plan.skip_agents:
            orch_result.skipped = True
            orch_result.skip_reason = plan.reason

            AgentOrchestrationRun.objects.create(
                reconciliation_result=result,
                status=AgentOrchestrationRun.Status.COMPLETED,
                plan_source=plan.plan_source if hasattr(plan, "plan_source") else "deterministic",
                planned_agents=[],
                executed_agents=[],
                skip_reason=plan.reason,
                actor_user_id=actor.pk,
                trace_id=trace_ctx.trace_id,
                started_at=timezone.now(),
                completed_at=timezone.now(),
                duration_ms=0,
            )

            # Auto-close by tolerance band: upgrade PARTIAL_MATCH → MATCHED
            if plan.auto_close:
                result.match_status = MatchStatus.MATCHED
                result.requires_review = False
                result.summary = (
                    f"Auto-closed: all line discrepancies within auto-close tolerance band. "
                    f"{plan.reason}"
                )
                result.save(update_fields=["match_status", "requires_review", "summary", "updated_at"])
                # Resolve tolerance-level exceptions
                result.exceptions.filter(
                    severity__in=["LOW", "MEDIUM"],
                ).update(resolved=True)
                logger.info("Auto-closed result %s by tolerance band (no AI agents)", result.pk)

            else:
                logger.info("Agents skipped for result %s: %s", result.pk, plan.reason)

            return orch_result

        if not plan.agents:
            orch_result.skipped = True
            orch_result.skip_reason = "No agents planned"
            return orch_result

        import time as _time
        _orch_start = _time.monotonic()
        orch_db_run = AgentOrchestrationRun.objects.create(
            reconciliation_result=result,
            status=AgentOrchestrationRun.Status.RUNNING,
            plan_source=plan.plan_source,
            plan_confidence=plan.plan_confidence,
            planned_agents=plan.agents,
            executed_agents=[],
            actor_user_id=actor.pk,
            trace_id=trace_ctx.trace_id,
            started_at=timezone.now(),
        )

        # 2. Partition agents: LLM-required vs deterministic-replaceable
        llm_agents = [a for a in plan.agents if a not in self.resolver.REPLACED_AGENTS]
        deterministic_tail = [a for a in plan.agents if a in self.resolver.REPLACED_AGENTS]

        # 3. Prepare shared context
        recon_mode = plan.reconciliation_mode or getattr(result, "reconciliation_mode", "") or ""
        exceptions = list(
            result.exceptions.values(
                "id", "exception_type", "severity", "message", "details", "resolved",
            )
        )
        from apps.agents.services.base_agent import BaseAgent as _BA
        exceptions = _BA._truncate_exceptions(exceptions)

        ctx = AgentContext(
            reconciliation_result=result,
            invoice_id=result.invoice_id,
            po_number=result.purchase_order.po_number if result.purchase_order else None,
            exceptions=exceptions,
            reconciliation_mode=recon_mode,
            extra={
                "vendor_name": (
                    result.invoice.vendor.name if result.invoice.vendor
                    else result.invoice.raw_vendor_name
                ),
                "total_amount": str(result.invoice.total_amount),
                "grn_available": result.grn_available,
                "grn_fully_received": result.grn_fully_received,
                "reconciliation_mode": recon_mode,
                "is_two_way": recon_mode == "TWO_WAY",
            },
            # RBAC context
            actor_user_id=actor.pk,
            actor_primary_role=rbac_snapshot.get("actor_primary_role", ""),
            actor_roles_snapshot=rbac_snapshot.get("actor_roles_snapshot", []),
            permission_checked=ORCHESTRATE_PERMISSION,
            permission_source=rbac_snapshot.get("permission_source", ""),
            access_granted=True,
            trace_id=trace_ctx.trace_id,
            span_id=trace_ctx.span_id,
        )

        # Store actor on instance for use by helper methods
        self._actor = actor

        # Attach structured memory to context for cross-agent data sharing.
        memory = AgentMemory()
        ctx.memory = memory

        # 4. Execute LLM agents in sequence
        last_output = None
        for agent_type in llm_agents:
            agent_cls = AGENT_CLASS_REGISTRY.get(agent_type)
            if not agent_cls:
                logger.warning("No agent class for type %s", agent_type)
                continue

            agent: BaseAgent = agent_cls()
            try:
                # --- RBAC: check per-agent permission ---
                if not AgentGuardrailsService.authorize_agent(actor, agent_type):
                    perm = AGENT_PERMISSIONS.get(agent_type, "?")
                    AgentGuardrailsService.log_guardrail_decision(
                        user=actor,
                        action=f"run_agent_{agent_type}",
                        permission_code=perm,
                        granted=False,
                        entity_type="ReconciliationResult",
                        entity_id=result.pk,
                    )
                    logger.warning(
                        "Agent %s denied for actor %s (missing %s)",
                        agent_type, actor.pk, perm,
                    )
                    continue

                # Pass review_assignment to ExceptionAnalysisAgent
                if agent_type == AgentType.EXCEPTION_ANALYSIS:
                    from apps.reviews.models import ReviewAssignment
                    _review_assignment = (
                        ReviewAssignment.objects
                        .filter(reconciliation_result=result)
                        .order_by("-created_at")
                        .first()
                    )
                    agent_run = agent.run(ctx, review_assignment=_review_assignment)
                else:
                    agent_run = agent.run(ctx)
                orch_result.agents_executed.append(agent_type)
                orch_result.agent_runs.append(agent_run)
                orch_db_run.executed_agents = orch_result.agents_executed
                orch_db_run.save(update_fields=["executed_agents"])
                last_output = agent_run

                # Stamp plan metadata onto the first agent run for dashboard tracking.
                if len(orch_result.agent_runs) == 1:
                    agent_run.input_payload = agent_run.input_payload or {}
                    agent_run.input_payload["plan_source"] = plan.plan_source
                    agent_run.input_payload["plan_confidence"] = plan.plan_confidence
                    agent_run.input_payload["planned_agents"] = plan.agents
                    agent_run.save(update_fields=["input_payload"])

                # Update structured memory from this agent's output.
                _output_proxy = _AgentRunOutputProxy(agent_run)
                memory.record_agent_output(agent_type, _output_proxy)

                # Record recommendation only for designated routing agents
                output_payload = agent_run.output_payload or {}
                rec_type = output_payload.get("recommendation_type")
                if rec_type and agent_type in _RECOMMENDING_AGENTS:
                    try:
                        rec = self.decision_service.log_recommendation(
                            agent_run=agent_run,
                            reconciliation_result=result,
                            recommendation_type=rec_type,
                            confidence=agent_run.confidence or 0.0,
                            reasoning=agent_run.summarized_reasoning or "",
                            evidence=output_payload.get("evidence"),
                        )
                    except IntegrityError:
                        logger.warning(
                            "Duplicate recommendation skipped: result=%s type=%s agent_run=%s",
                            result.pk, rec_type, agent_run.pk,
                        )
                    else:
                        # Backfill invoice FK on recommendation
                        rec.invoice_id = result.invoice_id
                        rec.save(update_fields=["invoice_id"])

                        # Audit: agent recommendation created
                        from apps.auditlog.services import AuditService
                        from apps.core.enums import AuditEventType
                        AuditService.log_event(
                            entity_type="Invoice",
                            entity_id=result.invoice_id,
                            event_type=AuditEventType.AGENT_RECOMMENDATION_CREATED,
                            description=f"Agent '{agent_type}' recommended {rec_type} (confidence: {agent_run.confidence or 0:.0%})",
                            agent=agent_type,
                            metadata={"recommendation_id": rec.pk, "recommendation_type": rec_type, "confidence": agent_run.confidence},
                        )

            except Exception as exc:
                logger.exception("Agent %s failed for result %s", agent_type, result.pk)
                orch_result.error = str(exc)[:1000]
                # Continue with remaining agents

            # --- Agent feedback loop: apply findings back to reconciliation ---
            if agent_type in _FEEDBACK_AGENTS and last_output:
                new_status = self._apply_agent_findings(
                    agent_type, last_output, result, ctx,
                )
                if new_status is not None:
                    # Refresh context for subsequent agents
                    ctx.po_number = (
                        result.purchase_order.po_number
                        if result.purchase_order else ctx.po_number
                    )
                    ctx.exceptions = list(
                        result.exceptions.values(
                            "id", "exception_type", "severity",
                            "message", "details", "resolved",
                        )
                    )
                    from apps.agents.services.base_agent import BaseAgent as _BA
                    ctx.exceptions = _BA._truncate_exceptions(ctx.exceptions)
                    ctx.memory.resolved_po_number = ctx.po_number
                    ctx.memory.facts["grn_available"] = bool(result.grn_available)
                    ctx.memory.facts["grn_fully_received"] = bool(result.grn_fully_received)

            # --- Reflection: dynamically insert agents based on findings ---
            if last_output:
                extra_agents = self._reflect(
                    agent_type,
                    last_output,
                    result,
                    llm_agents[llm_agents.index(agent_type) + 1:],
                    ctx,
                    already_executed=list(orch_result.agents_executed),
                )
                if extra_agents:
                    insert_pos = llm_agents.index(agent_type) + 1
                    for i, new_agent in enumerate(extra_agents):
                        llm_agents.insert(insert_pos + i, new_agent)
                    logger.info(
                        "Reflection inserted agents %s after %s for result %s",
                        extra_agents, agent_type, result.pk,
                    )

        # 5. Deterministic resolution for tail agents (replaces LLM for
        #    EXCEPTION_ANALYSIS, REVIEW_ROUTING, CASE_SUMMARY)
        if deterministic_tail:
            self._apply_deterministic_resolution(
                result, orch_result, deterministic_tail, last_output,
            )

        # 6. Determine final recommendation (from last agent with a recommendation)
        self._resolve_final_recommendation(orch_result, result)

        # 7. Auto-close or escalate
        self._apply_post_policies(orch_result, result)

        orch_db_run.status = (
            AgentOrchestrationRun.Status.PARTIAL
            if orch_result.error
            else AgentOrchestrationRun.Status.COMPLETED
        )
        orch_db_run.final_recommendation = orch_result.final_recommendation or ""
        orch_db_run.final_confidence = orch_result.final_confidence
        orch_db_run.completed_at = timezone.now()
        orch_db_run.duration_ms = int((_time.monotonic() - _orch_start) * 1000)
        orch_db_run.save(update_fields=[
            "status", "final_recommendation", "final_confidence",
            "completed_at", "duration_ms",
        ])

        logger.info(
            "Orchestration complete for result %s: agents=%s recommendation=%s confidence=%.2f",
            result.pk, orch_result.agents_executed,
            orch_result.final_recommendation, orch_result.final_confidence,
        )
        return orch_result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reflect(
        self,
        completed_agent_type,
        agent_run,
        result,
        remaining_agents,
        ctx,
        already_executed=None,
    ):
        """Inspect the just-completed agent run and return any agent types to
        insert immediately after the current position in the pipeline.

        Returns a list of agent_type strings (possibly empty). Never raises.
        """
        try:
            if ctx.memory is None:
                return []

            already_executed = set(already_executed or [])

            # Rule 1: PO was just found in a 3-way case -- check for GRN next.
            if (
                completed_agent_type == AgentType.PO_RETRIEVAL
                and ctx.memory.resolved_po_number is not None
                and getattr(result, "reconciliation_mode", "") != "TWO_WAY"
                and AgentType.GRN_RETRIEVAL not in remaining_agents
                and AgentType.GRN_RETRIEVAL not in already_executed
            ):
                return [AgentType.GRN_RETRIEVAL]

            # Rule 2: Very low confidence extraction -- investigate discrepancies too.
            if (
                completed_agent_type == AgentType.INVOICE_UNDERSTANDING
                and agent_run.confidence is not None
                and agent_run.confidence < 0.5
                and AgentType.RECONCILIATION_ASSIST not in remaining_agents
                and AgentType.RECONCILIATION_ASSIST not in already_executed
            ):
                return [AgentType.RECONCILIATION_ASSIST]

            return []
        except Exception:
            logger.exception(
                "_reflect() raised unexpectedly for agent %s result %s",
                completed_agent_type,
                getattr(result, "pk", "?"),
            )
            return []

    def _resolve_final_recommendation(
        self, orch: OrchestrationResult, result: ReconciliationResult
    ) -> None:
        """Pick the highest-confidence recommendation from all agent runs."""
        recs = AgentRecommendation.objects.filter(
            reconciliation_result=result,
            agent_run__in=orch.agent_runs,
        ).order_by("-confidence")

        best = recs.first()
        if best:
            orch.final_recommendation = best.recommendation_type
            orch.final_confidence = best.confidence or 0.0
            orch.final_reasoning = best.reasoning

    def _apply_post_policies(
        self, orch: OrchestrationResult, result: ReconciliationResult
    ) -> None:
        """Apply PolicyEngine post-run checks (auto-close, escalation)."""
        actor = getattr(self, "_actor", None)

        if self.policy.should_auto_close(orch.final_recommendation, orch.final_confidence):
            # RBAC: check auto-close permission
            if actor and not AgentGuardrailsService.authorize_action(actor, "auto_close_result"):
                AgentGuardrailsService.log_guardrail_decision(
                    user=actor,
                    action="auto_close_result",
                    permission_code=ACTION_PERMISSIONS.get("auto_close_result", ""),
                    granted=False,
                    entity_type="ReconciliationResult",
                    entity_id=result.pk,
                )
                logger.warning("Auto-close denied for result %s — actor lacks permission", result.pk)
            else:
                if actor:
                    AgentGuardrailsService.log_guardrail_decision(
                        user=actor,
                        action="auto_close_result",
                        permission_code=ACTION_PERMISSIONS.get("auto_close_result", ""),
                        granted=True,
                        entity_type="ReconciliationResult",
                        entity_id=result.pk,
                    )
                result.match_status = MatchStatus.MATCHED
                result.requires_review = False
                result.save(update_fields=["match_status", "requires_review", "updated_at"])
                logger.info("Auto-closed result %s (confidence=%.2f)", result.pk, orch.final_confidence)
            return

        if self.policy.should_escalate(orch.final_recommendation, orch.final_confidence):
            # RBAC: check escalation permission
            if actor and not AgentGuardrailsService.authorize_action(actor, "escalate_case"):
                AgentGuardrailsService.log_guardrail_decision(
                    user=actor,
                    action="escalate_case",
                    permission_code=ACTION_PERMISSIONS.get("escalate_case", ""),
                    granted=False,
                    entity_type="ReconciliationResult",
                    entity_id=result.pk,
                )
                logger.warning("Escalation denied for result %s — actor lacks permission", result.pk)
                return

            last_run = orch.agent_runs[-1] if orch.agent_runs else None
            if last_run:
                AgentEscalation.objects.create(
                    agent_run=last_run,
                    reconciliation_result=result,
                    severity=ExceptionSeverity.HIGH,
                    reason=orch.final_reasoning or "Low confidence — requires manager review",
                    suggested_assignee_role="FINANCE_MANAGER",
                )
            logger.info("Escalated result %s", result.pk)

    # ------------------------------------------------------------------
    # Deterministic resolution (replaces EXCEPTION_ANALYSIS / REVIEW_ROUTING / CASE_SUMMARY)
    # ------------------------------------------------------------------
    def _apply_deterministic_resolution(
        self,
        result: ReconciliationResult,
        orch: OrchestrationResult,
        deterministic_agents: list,
        last_llm_output: Optional[AgentRun],
    ) -> None:
        """Run the deterministic resolver for tail agents and create records."""
        # Re-fetch exceptions (may have changed from feedback loop)
        fresh_exceptions = list(
            result.exceptions.values(
                "id", "exception_type", "severity", "message", "details", "resolved",
            )
        )
        from apps.agents.services.base_agent import BaseAgent as _BA
        fresh_exceptions = _BA._truncate_exceptions(fresh_exceptions)

        # Extract prior recommendation from last LLM agent (if any)
        prior_rec = None
        prior_conf = 0.0
        if last_llm_output:
            payload = last_llm_output.output_payload or {}
            prior_rec = payload.get("recommendation_type")
            prior_conf = last_llm_output.confidence or 0.0

        resolution = self.resolver.resolve(
            result, fresh_exceptions,
            prior_recommendation=prior_rec,
            prior_confidence=prior_conf,
        )

        now = timezone.now()
        actor = getattr(self, "_actor", None)
        actor_rbac = AgentGuardrailsService.build_rbac_snapshot(actor) if actor else {}
        for det_agent_type in deterministic_agents:
            det_run = AgentRun.objects.create(
                agent_type=det_agent_type,
                reconciliation_result=result,
                status=AgentRunStatus.COMPLETED,
                input_payload={
                    "exceptions": [
                        {k: str(v) for k, v in e.items()} for e in fresh_exceptions
                    ],
                    "resolver": "deterministic",
                },
                output_payload={
                    "recommendation_type": resolution.recommendation_type,
                    "reasoning": resolution.reasoning,
                    "evidence": resolution.evidence,
                    "resolver": "deterministic",
                },
                summarized_reasoning=(
                    resolution.case_summary
                    if det_agent_type == AgentType.CASE_SUMMARY
                    else resolution.reasoning
                ),
                confidence=resolution.confidence,
                started_at=now,
                completed_at=now,
                duration_ms=0,
                llm_model_used="deterministic",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                # RBAC fields
                actor_user_id=actor.pk if actor else None,
                actor_primary_role=actor_rbac.get("actor_primary_role", ""),
                actor_roles_snapshot_json=actor_rbac.get("actor_roles_snapshot", []),
                permission_source=actor_rbac.get("permission_source", ""),
                access_granted=True,
            )
            orch.agents_executed.append(det_agent_type)
            orch.agent_runs.append(det_run)

            # Create recommendation for RECOMMENDING agents (same as LLM path)
            if det_agent_type in _RECOMMENDING_AGENTS:
                try:
                    rec = self.decision_service.log_recommendation(
                        agent_run=det_run,
                        reconciliation_result=result,
                        recommendation_type=resolution.recommendation_type,
                        confidence=resolution.confidence,
                        reasoning=resolution.reasoning,
                        evidence=resolution.evidence,
                    )
                except IntegrityError:
                    logger.warning(
                        "Duplicate recommendation skipped: result=%s type=%s agent_run=%s",
                        result.pk, resolution.recommendation_type, det_run.pk,
                    )
                else:
                    rec.invoice_id = result.invoice_id
                    rec.save(update_fields=["invoice_id"])

                    from apps.auditlog.services import AuditService
                    from apps.core.enums import AuditEventType
                    AuditService.log_event(
                        entity_type="Invoice",
                        entity_id=result.invoice_id,
                        event_type=AuditEventType.AGENT_RECOMMENDATION_CREATED,
                        description=(
                            f"Deterministic resolver ('{det_agent_type}') recommended "
                            f"{resolution.recommendation_type} "
                            f"(confidence: {resolution.confidence:.0%})"
                        ),
                        agent=det_agent_type,
                        metadata={
                            "recommendation_type": resolution.recommendation_type,
                            "confidence": resolution.confidence,
                            "resolver": "deterministic",
                        },
                    )

        # Persist the case summary on the result
        result.summary = resolution.case_summary
        result.save(update_fields=["summary", "updated_at"])

        logger.info(
            "Deterministic resolution applied for result %s: %s (confidence=%.2f, "
            "agents=%s)",
            result.pk, resolution.recommendation_type, resolution.confidence,
            deterministic_agents,
        )

    # ------------------------------------------------------------------
    # Agent findings → re-reconciliation feedback loop
    # ------------------------------------------------------------------
    def _apply_agent_findings(
        self,
        agent_type: str,
        agent_run: AgentRun,
        result: ReconciliationResult,
        ctx: AgentContext,
    ) -> Optional[MatchStatus]:
        """Check if the agent found actionable data (e.g. a PO) and re-reconcile.

        Returns the new match status if re-reconciliation happened, else None.
        """
        output_payload = agent_run.output_payload or {}
        evidence = output_payload.get("evidence", {})

        if agent_type == AgentType.PO_RETRIEVAL:
            return self._apply_po_finding(agent_run, result, evidence)

        return None

    def _apply_po_finding(
        self,
        agent_run: AgentRun,
        result: ReconciliationResult,
        evidence: dict,
    ) -> Optional[MatchStatus]:
        """If the PO Retrieval Agent found a PO, link it and re-reconcile."""
        found_po_number = (
            evidence.get("found_po")
            or evidence.get("po_number")
            or evidence.get("matched_po")
        )
        if not found_po_number:
            logger.info(
                "PO Retrieval Agent for result %s did not find a PO (evidence=%s)",
                result.pk, evidence,
            )
            return None

        from apps.documents.models import PurchaseOrder
        po = PurchaseOrder.objects.filter(po_number=found_po_number).first()
        if not po:
            # Try normalized lookup
            from apps.core.utils import normalize_po_number
            norm = normalize_po_number(found_po_number)
            po = PurchaseOrder.objects.filter(normalized_po_number=norm).first()

        if not po:
            logger.warning(
                "PO Retrieval Agent reported PO '%s' but it doesn't exist in DB",
                found_po_number,
            )
            return None

        from apps.reconciliation.services.agent_feedback_service import AgentFeedbackService
        feedback = AgentFeedbackService()
        new_status = feedback.apply_found_po(
            result=result,
            po=po,
            agent_run_id=agent_run.pk,
        )
        logger.info(
            "Agent feedback: PO %s applied to result %s → new status %s",
            po.po_number, result.pk, new_status,
        )
        return new_status
