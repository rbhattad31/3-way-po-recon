"""Recovery lane service — bounded post-extraction anomaly correction.

Triggered only for *named failure modes* (explicit decision codes), never for
generic low-confidence scores.  When triggered, it invokes
InvoiceUnderstandingAgent with an extraction-scoped context and returns
additive output (never replaces the original extraction result).

Design rules:
  - RecoveryLaneService.evaluate() is deterministic — takes decision codes and
    returns a RecoveryDecision without any I/O.
  - RecoveryLaneService.invoke() wraps the agent call and is fail-silent.
  - All outputs are additive.  The caller (tasks.py) decides what to surface.
  - No new DB models — results are embedded in raw_response["_recovery"].
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.conf import settings

logger = logging.getLogger(__name__)


# ── Codes that trigger recovery lane ─────────────────────────────────────────

RECOVERY_TRIGGER_CODES: frozenset[str] = frozenset({
    "INV_NUM_UNRECOVERABLE",
    "TOTAL_MISMATCH_HARD",
    "TAX_ALLOC_AMBIGUOUS",
    "VENDOR_MATCH_LOW",
    "LINE_TABLE_INCOMPLETE",
    "PROMPT_COMPOSITION_FALLBACK_USED",
})

# Maps each trigger code → list of bounded recovery action keywords the agent
# should focus on.  These are injected into ctx.extra so the agent prompt can
# reference them without hard-coding the mapping in the prompt template.
_CODE_TO_ACTIONS: Dict[str, List[str]] = {
    "INV_NUM_UNRECOVERABLE":        ["verify_invoice_number", "cross_check_ocr"],
    "TOTAL_MISMATCH_HARD":          ["verify_totals", "recheck_line_sums", "check_tax"],
    "TAX_ALLOC_AMBIGUOUS":          ["verify_tax_breakdown", "check_tax_type"],
    "VENDOR_MATCH_LOW":             ["verify_vendor_name", "vendor_lookup"],
    "LINE_TABLE_INCOMPLETE":        ["verify_line_items", "recount_lines"],
    "PROMPT_COMPOSITION_FALLBACK_USED": ["full_invoice_review"],
}


# ── Output dataclasses ────────────────────────────────────────────────────────

@dataclass
class RecoveryDecision:
    """Policy verdict — should recovery lane be invoked?"""
    should_invoke: bool
    trigger_codes: List[str] = field(default_factory=list)
    recovery_actions: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class RecoveryResult:
    """Outcome of an actual recovery lane invocation."""
    invoked: bool = False
    succeeded: bool = False
    trigger_codes: List[str] = field(default_factory=list)
    recovery_actions: List[str] = field(default_factory=list)
    agent_reasoning: str = ""
    agent_confidence: float = 0.0
    agent_recommendation: str = ""
    agent_evidence: Dict[str, Any] = field(default_factory=dict)
    agent_run_id: Optional[int] = None
    error: str = ""

    def to_serializable(self) -> dict:
        return {
            "invoked": self.invoked,
            "succeeded": self.succeeded,
            "trigger_codes": self.trigger_codes,
            "recovery_actions": self.recovery_actions,
            "agent_reasoning": self.agent_reasoning[:500] if self.agent_reasoning else "",
            "agent_confidence": self.agent_confidence,
            "agent_recommendation": self.agent_recommendation,
            "agent_evidence": self.agent_evidence,
            "agent_run_id": self.agent_run_id,
            "error": self.error[:200] if self.error else "",
        }


# ── Service ───────────────────────────────────────────────────────────────────

class RecoveryLaneService:
    """Deterministic policy + fail-silent agent invocation for extraction recovery."""

    @staticmethod
    def evaluate(decision_codes: List[str]) -> RecoveryDecision:
        """Determine whether the recovery lane should be invoked.

        Pure function — no side effects, no I/O.

        Args:
            decision_codes: List of decision codes derived from the extraction
                            pipeline (from decision_codes.derive_codes()).

        Returns:
            RecoveryDecision with should_invoke=True only when at least one
            named trigger code is present.
        """
        codes_set = set(decision_codes or [])
        triggered = [c for c in decision_codes if c in RECOVERY_TRIGGER_CODES]

        if not triggered:
            return RecoveryDecision(
                should_invoke=False,
                reason="No named failure modes detected — recovery not needed",
            )

        # Collect all unique recovery actions across triggered codes
        actions: List[str] = []
        seen_actions: set = set()
        for code in triggered:
            for action in _CODE_TO_ACTIONS.get(code, []):
                if action not in seen_actions:
                    seen_actions.add(action)
                    actions.append(action)

        return RecoveryDecision(
            should_invoke=True,
            trigger_codes=triggered,
            recovery_actions=actions,
            reason=f"Named failure modes triggered recovery: {', '.join(triggered)}",
        )

    @staticmethod
    def invoke(
        decision: RecoveryDecision,
        invoice_id: int,
        *,
        validation_result=None,
        field_conf_result=None,
        actor_user_id: Optional[int] = None,
        document_upload_id: Optional[int] = None,
        trace_id: str = "",
        tenant: Any = None,
    ) -> RecoveryResult:
        """Invoke InvoiceUnderstandingAgent for bounded recovery.

        Fail-silent — any exception returns a RecoveryResult with
        invoked=True, succeeded=False, and an error description.

        Args:
            decision:           RecoveryDecision from evaluate() — must have
                                should_invoke=True.
            invoice_id:         Invoice DB pk (used by invoice_details tool).
            validation_result:  Optional ValidationResult for warning context.
            field_conf_result:  Optional FieldConfidenceResult for weak field names.
            actor_user_id:      Optional user id for the AgentRun audit trail.

        Returns:
            RecoveryResult — always (never raises).
        """
        if not decision.should_invoke:
            return RecoveryResult(invoked=False)

        try:
            return RecoveryLaneService._invoke(
                decision, invoice_id,
                validation_result=validation_result,
                field_conf_result=field_conf_result,
                actor_user_id=actor_user_id,
                document_upload_id=document_upload_id,
                trace_id=trace_id,
                tenant=tenant,
            )
        except Exception as exc:
            logger.exception(
                "RecoveryLaneService.invoke failed for invoice %s: %s", invoice_id, exc
            )
            return RecoveryResult(
                invoked=True,
                succeeded=False,
                trigger_codes=decision.trigger_codes,
                recovery_actions=decision.recovery_actions,
                error=str(exc)[:300],
            )

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _invoke(
        decision: RecoveryDecision,
        invoice_id: int,
        *,
        validation_result,
        field_conf_result,
        actor_user_id: Optional[int],
        document_upload_id: Optional[int] = None,
        trace_id: str = "",
        tenant: Any = None,
    ) -> RecoveryResult:
        from apps.agents.services.agent_classes import InvoiceUnderstandingAgent
        from apps.agents.services.supervisor_agent import SupervisorAgent
        from apps.agents.services.base_agent import AgentContext

        # Build ctx.extra with enough context for the agent prompt to focus on
        # the specific failure modes — without coupling to EvidenceCaptureService.
        extra: Dict[str, Any] = {
            "recovery_trigger_codes": decision.trigger_codes,
            "recovery_actions": decision.recovery_actions,
            "invocation_reason": "RECOVERY_LANE",
            "user_query": (
                "Extraction is already complete. Validate extracted invoice data, "
                "verify vendor and PO context, then provide a routing recommendation."
            ),
            "extraction_done": True,
            "reconciliation_done": False,
        }

        # Surface validation warnings if available
        if validation_result is not None:
            warnings = getattr(validation_result, "issues", [])
            if warnings:
                extra["validation_warnings"] = "; ".join(
                    f"{w.field}: {w.message}" for w in warnings[:10]
                )
            crit = getattr(validation_result, "critical_failures", [])
            if crit:
                extra["critical_failures"] = crit

        # Surface weak-field names from field confidence
        if field_conf_result is not None:
            low_fields = getattr(field_conf_result, "low_confidence_fields", [])
            if low_fields:
                extra["low_confidence_fields"] = low_fields
            weakest = getattr(field_conf_result, "weakest_critical_field", "")
            if weakest:
                extra["weakest_critical_field"] = weakest

        # Resolve RBAC metadata from the actor user
        _actor_role = ""
        _actor_roles_snapshot: list = []
        if actor_user_id:
            try:
                from apps.accounts.models import User
                _user = User.objects.get(pk=actor_user_id)
                _actor_role = getattr(_user, "role", "") or ""
                _actor_roles_snapshot = list(
                    _user.user_roles.filter(is_active=True)
                    .values_list("role__code", flat=True)
                ) if hasattr(_user, "user_roles") else []
            except Exception:
                pass

        ctx = AgentContext(
            reconciliation_result=None,
            invoice_id=invoice_id,
            extra=extra,
            actor_user_id=actor_user_id,
            actor_primary_role=_actor_role,
            actor_roles_snapshot=_actor_roles_snapshot,
            permission_checked="invoices.upload",
            permission_source="extraction_pipeline",
            access_granted=True,
            document_upload_id=document_upload_id,
            trace_id=trace_id,
            tenant=tenant,
        )

        _primary_agent = str(
            getattr(settings, "EXTRACTION_ROUTING_PRIMARY_AGENT", "SUPERVISOR")
        ).strip().upper()
        _fallback_agent = "INVOICE_UNDERSTANDING"

        if _primary_agent == "SUPERVISOR":
            try:
                agent = SupervisorAgent(query_mode="CASE_ANALYSIS")
                agent_run = agent.run(ctx)
                _output = getattr(agent_run, "output_payload", None) or {}
                _has_recommendation = bool(
                    isinstance(_output, dict) and _output.get("recommendation_type")
                )
                _is_completed = str(getattr(agent_run, "status", "")).upper() == "COMPLETED"
                if not _is_completed or not _has_recommendation:
                    logger.warning(
                        "RecoveryLaneService: supervisor run %s not usable (status=%s, has_recommendation=%s), "
                        "fallback to understanding agent",
                        getattr(agent_run, "pk", "?"),
                        getattr(agent_run, "status", ""),
                        _has_recommendation,
                    )
                    agent = InvoiceUnderstandingAgent()
                    agent_run = agent.run(ctx)
            except Exception as sup_exc:
                logger.warning(
                    "RecoveryLaneService: supervisor invocation failed, fallback to understanding agent: %s",
                    sup_exc,
                )
                agent = InvoiceUnderstandingAgent()
                agent_run = agent.run(ctx)
        else:
            agent = InvoiceUnderstandingAgent()
            agent_run = agent.run(ctx)

        # Stamp the run as a recovery invocation via input_payload
        try:
            agent_run.input_payload = agent_run.input_payload or {}
            agent_run.input_payload["_recovery_meta"] = {
                "trigger_codes": decision.trigger_codes,
                "recovery_actions": decision.recovery_actions,
                "primary_agent": _primary_agent,
                "fallback_agent": _fallback_agent,
            }
            agent_run.invocation_reason = f"RECOVERY_LANE:{_primary_agent}"
            agent_run.save(update_fields=["input_payload", "invocation_reason"])
        except Exception as stamp_exc:
            logger.warning(
                "RecoveryLaneService: could not stamp agent_run %s: %s",
                getattr(agent_run, "pk", "?"), stamp_exc,
            )

        # Extract output from the agent run
        output = getattr(agent_run, "output_payload", None) or {}
        reasoning = output.get("reasoning", "") if isinstance(output, dict) else ""
        confidence = float(output.get("confidence", 0.0)) if isinstance(output, dict) else 0.0
        recommendation = output.get("recommendation_type", "") if isinstance(output, dict) else ""
        evidence = output.get("evidence", {}) if isinstance(output, dict) else {}

        # Recovery is "succeeded" if the agent produced reasoning or evidence
        succeeded = bool(reasoning or evidence)

        return RecoveryResult(
            invoked=True,
            succeeded=succeeded,
            trigger_codes=decision.trigger_codes,
            recovery_actions=decision.recovery_actions,
            agent_reasoning=reasoning,
            agent_confidence=confidence,
            agent_recommendation=recommendation,
            agent_evidence=evidence,
            agent_run_id=getattr(agent_run, "pk", None),
        )
