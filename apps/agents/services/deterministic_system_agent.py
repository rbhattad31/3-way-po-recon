"""Base class for deterministic system agents.

System agents wrap platform-level deterministic capabilities (review routing,
case summary, bulk intake, case intake, posting preparation) in the standard
agent framework -- producing ``AgentRun``, ``DecisionLog``, and Langfuse
traces -- without LLM calls, tool-calling loops, or artificial chat messages.

Subclasses implement ``execute_deterministic(ctx)`` which returns an
``AgentOutput`` with deterministic confidence and evidence.
"""
from __future__ import annotations

import logging
import time
from abc import abstractmethod
from typing import Any, Dict, List, Optional

from django.utils import timezone

from apps.agents.models import AgentDefinition, AgentRun, DecisionLog
from apps.agents.services.base_agent import AgentContext, AgentOutput, BaseAgent
from apps.core.enums import AgentRunStatus, AuditEventType
from apps.core.evaluation_constants import (
    SYSTEM_AGENT_DECISION_COUNT,
    SYSTEM_AGENT_SUCCESS,
)

logger = logging.getLogger(__name__)


class DeterministicSystemAgent(BaseAgent):
    """Base for deterministic system agents that skip the ReAct loop.

    Concrete subclasses MUST set ``agent_type`` and implement
    ``execute_deterministic(ctx) -> AgentOutput``.

    The abstract properties ``system_prompt``, ``build_user_message``,
    ``allowed_tools``, and ``interpret_response`` from ``BaseAgent`` are
    given no-op defaults because they are irrelevant for deterministic
    execution.  The ``run()`` method is fully overridden.
    """

    # Deterministic agents never use LLM
    enforce_json_response: bool = False

    def __init__(self):
        # Skip BaseAgent.__init__ which creates an LLMClient -- deterministic
        # agents never call the LLM and should not require API key env vars.
        self.llm = None

    # ------------------------------------------------------------------ #
    # BaseAgent abstract interface -- stub implementations                #
    # ------------------------------------------------------------------ #

    @property
    def system_prompt(self) -> str:  # pragma: no cover
        return ""

    def build_user_message(self, ctx: AgentContext) -> str:  # pragma: no cover
        return ""

    @property
    def allowed_tools(self) -> List[str]:
        return []

    def interpret_response(self, content: str, ctx: AgentContext) -> AgentOutput:  # pragma: no cover
        return AgentOutput()

    # ------------------------------------------------------------------ #
    # Deterministic entry point (subclasses implement this)               #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def execute_deterministic(self, ctx: AgentContext) -> AgentOutput:
        """Run the deterministic logic and return structured output.

        The returned ``AgentOutput`` should have:
        - ``reasoning``: human-readable explanation of what was done
        - ``confidence``: deterministic confidence (typically 0.90-1.0)
        - ``evidence``: structured evidence dict
        - ``decisions``: list of decision dicts for ``DecisionLog``
        - ``recommendation_type``: only if the agent genuinely recommends
        """
        ...

    # ------------------------------------------------------------------ #
    # Overridden run() -- deterministic lifecycle                         #
    # ------------------------------------------------------------------ #

    def run(self, ctx: AgentContext) -> AgentRun:
        """Execute deterministic logic and persist a standard AgentRun."""
        agent_def = AgentDefinition.objects.filter(
            agent_type=self.agent_type, enabled=True,
        ).first()

        now = timezone.now()
        agent_run = AgentRun.objects.create(
            agent_definition=agent_def,
            agent_type=self.agent_type,
            reconciliation_result=ctx.reconciliation_result,
            document_upload_id=ctx.document_upload_id,
            status=AgentRunStatus.RUNNING,
            input_payload=self._build_input_payload(ctx),
            started_at=now,
            llm_model_used="deterministic",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            # RBAC metadata
            actor_user_id=ctx.actor_user_id,
            actor_primary_role=ctx.actor_primary_role,
            actor_roles_snapshot_json=ctx.actor_roles_snapshot or None,
            permission_checked=ctx.permission_checked,
            permission_source=ctx.permission_source,
            access_granted=ctx.access_granted,
            trace_id=ctx.trace_id,
            span_id=ctx.span_id,
            tenant=ctx.tenant,
        )

        # Langfuse span
        _lf_span = None
        _lf_trace = getattr(ctx, "_langfuse_trace", None)
        if _lf_trace is not None:
            try:
                from apps.core.langfuse_client import start_span
                _lf_span = start_span(
                    _lf_trace,
                    name=str(self.agent_type),
                    metadata={
                        "agent_type": str(self.agent_type),
                        "agent_run_id": agent_run.pk,
                        "execution_mode": "deterministic",
                        "invoice_id": ctx.invoice_id,
                        "result_id": (
                            ctx.reconciliation_result.pk
                            if ctx.reconciliation_result else None
                        ),
                    },
                )
            except Exception:
                _lf_span = None

        start = time.monotonic()
        try:
            output = self.execute_deterministic(ctx)
            self._finalise_deterministic_run(agent_run, output, start, ctx, agent_def)

            # Langfuse close
            if _lf_span is not None:
                try:
                    from apps.core.langfuse_client import (
                        end_span,
                        score_observation,
                    )
                    end_span(_lf_span, output={
                        "recommendation": output.recommendation_type,
                        "confidence": output.confidence,
                        "decision_count": len(output.decisions),
                    })
                    score_observation(
                        _lf_span,
                        SYSTEM_AGENT_SUCCESS,
                        1.0,
                        comment=f"{self.agent_type} completed",
                    )
                    score_observation(
                        _lf_span,
                        SYSTEM_AGENT_DECISION_COUNT,
                        float(len(output.decisions)),
                        comment=f"{len(output.decisions)} decisions logged",
                    )
                except Exception:
                    pass

            # Audit event
            self._emit_audit_event(agent_run, output, ctx, success=True)

        except Exception as exc:
            logger.exception(
                "System agent %s failed for result %s",
                self.agent_type,
                ctx.reconciliation_result.pk if ctx.reconciliation_result else None,
            )
            agent_run.status = AgentRunStatus.FAILED
            agent_run.error_message = str(exc)[:2000]
            agent_run.duration_ms = int((time.monotonic() - start) * 1000)
            agent_run.completed_at = timezone.now()
            agent_run.save()

            if _lf_span is not None:
                try:
                    from apps.core.langfuse_client import end_span, score_observation
                    end_span(
                        _lf_span,
                        output={"status": "FAILED", "error": str(exc)[:200]},
                        level="ERROR",
                    )
                    score_observation(_lf_span, SYSTEM_AGENT_SUCCESS, 0.0)
                except Exception:
                    pass

            self._emit_audit_event(agent_run, None, ctx, success=False, error=str(exc))

        return agent_run

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _build_input_payload(self, ctx: AgentContext) -> Dict[str, Any]:
        """Construct input_payload for the AgentRun record."""
        payload: Dict[str, Any] = {
            "agent_type": str(self.agent_type),
            "execution_mode": "deterministic",
            "invoice_id": ctx.invoice_id,
        }
        if ctx.reconciliation_result:
            payload["reconciliation_result_id"] = ctx.reconciliation_result.pk
        if ctx.po_number:
            payload["po_number"] = ctx.po_number
        if ctx.reconciliation_mode:
            payload["reconciliation_mode"] = ctx.reconciliation_mode
        if ctx.exceptions:
            payload["exception_count"] = len(ctx.exceptions)
        if ctx.extra:
            # Only include serialisable top-level keys
            safe_extra = {}
            for k, v in ctx.extra.items():
                try:
                    import json
                    json.dumps(v)
                    safe_extra[k] = v
                except (TypeError, ValueError):
                    safe_extra[k] = str(v)[:200]
            payload["extra"] = safe_extra
        return payload

    def _finalise_deterministic_run(
        self,
        agent_run: AgentRun,
        output: AgentOutput,
        start: float,
        ctx: AgentContext,
        agent_def: Optional[AgentDefinition] = None,
    ) -> None:
        """Persist completed agent run and decision logs."""
        agent_run.status = AgentRunStatus.COMPLETED
        agent_run.completed_at = timezone.now()
        agent_run.duration_ms = int((time.monotonic() - start) * 1000)
        agent_run.confidence = output.confidence
        agent_run.output_payload = {
            "reasoning": output.reasoning,
            "recommendation_type": output.recommendation_type,
            "confidence": output.confidence,
            "evidence": output.evidence,
            "resolver": "deterministic",
        }
        agent_run.summarized_reasoning = self._sanitise_text(
            output.reasoning
        )[:2000]
        agent_run.save()

        # Resolve invoice_id: from result FK if available, else from ctx
        _invoice_id = None
        if agent_run.reconciliation_result_id:
            try:
                _invoice_id = agent_run.reconciliation_result.invoice_id
            except Exception:
                pass
        if _invoice_id is None and ctx.invoice_id:
            _invoice_id = ctx.invoice_id if ctx.invoice_id != 0 else None

        # Persist decision logs
        for d in output.decisions:
            evidence_refs = d.get("evidence") or d.get("evidence_refs") or {}
            if not evidence_refs:
                evidence_refs = {"_provenance": "deterministic_system_agent"}
            DecisionLog.objects.create(
                agent_run=agent_run,
                decision=str(d.get("decision", ""))[:500],
                rationale=d.get("rationale", ""),
                confidence=d.get("confidence"),
                deterministic_flag=True,
                evidence_refs=evidence_refs,
                rule_name=d.get("rule_name", ""),
                trace_id=getattr(agent_run, "trace_id", "") or "",
                span_id=getattr(agent_run, "span_id", "") or "",
                invoice_id=_invoice_id,
                recommendation_type=output.recommendation_type or "",
                tenant=agent_run.tenant,
            )

    def _emit_audit_event(
        self,
        agent_run: AgentRun,
        output: Optional[AgentOutput],
        ctx: AgentContext,
        success: bool,
        error: str = "",
    ) -> None:
        """Log an audit event for the system agent run."""
        try:
            from apps.auditlog.services import AuditService
            event_type = (
                AuditEventType.SYSTEM_AGENT_RUN_COMPLETED
                if success
                else AuditEventType.SYSTEM_AGENT_RUN_FAILED
            )
            invoice_id = None
            if agent_run.reconciliation_result_id:
                try:
                    invoice_id = agent_run.reconciliation_result.invoice_id
                except Exception:
                    pass
            if invoice_id is None and ctx.invoice_id:
                invoice_id = ctx.invoice_id if ctx.invoice_id != 0 else None

            metadata = {
                "agent_type": str(self.agent_type),
                "agent_run_id": agent_run.pk,
                "execution_mode": "deterministic",
                "duration_ms": agent_run.duration_ms,
            }
            if output:
                metadata["confidence"] = output.confidence
                metadata["recommendation_type"] = output.recommendation_type
                metadata["decision_count"] = len(output.decisions)
            if error:
                metadata["error"] = error[:500]

            AuditService.log_event(
                entity_type="AgentRun",
                entity_id=agent_run.pk,
                event_type=event_type,
                description=(
                    f"System agent '{self.agent_type}' "
                    f"{'completed' if success else 'failed'}"
                ),
                agent=str(self.agent_type),
                metadata=metadata,
                invoice_id=invoice_id,
            )
        except Exception:
            logger.warning(
                "Failed to emit audit event for system agent %s",
                self.agent_type,
                exc_info=True,
            )
