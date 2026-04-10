"""AgentEvalAdapter -- bridges agent run data into core_eval.

Creates EvalRun + EvalMetric + EvalFieldOutcome per agent run, and a
pipeline-level EvalRun per orchestration.  All methods are fail-silent.

Per-agent EvalRun:
    app_module  = "agents"
    entity_type = "AgentRun"
    entity_id   = str(agent_run.pk)
    run_key     = f"agent_run::{agent_run.pk}"

Pipeline-level EvalRun:
    app_module  = "agents"
    entity_type = "AgentOrchestrationRun"
    entity_id   = str(orch_run.pk)
    run_key     = f"orchestration::{orch_run.pk}"

Field outcomes track key decisions/findings per agent type so that
human review corrections can populate ground truth later.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

APP_MODULE = "agents"
ENTITY_TYPE_AGENT_RUN = "AgentRun"
ENTITY_TYPE_ORCH_RUN = "AgentOrchestrationRun"

# -- Per-agent field outcome definitions --
# Maps agent_type -> list of (field_name, extractor_fn) where extractor_fn
# receives (agent_run, output_payload, evidence) and returns (value_str, confidence).
# This lets us track whether each agent's key findings are correct.

def _extract_po_found(run, payload, evidence):
    """PO Retrieval: did the agent find a PO?"""
    found = evidence.get("found_po") or evidence.get("po_number") or ""
    conf = run.confidence or 0.0
    return str(found), conf

def _extract_grn_found(run, payload, evidence):
    """GRN Retrieval: did the agent find a GRN?"""
    found = evidence.get("found_grn") or evidence.get("grn_number") or ""
    conf = run.confidence or 0.0
    return str(found), conf

def _extract_recommendation(run, payload, evidence):
    """Any recommending agent: what did it recommend?"""
    rec = payload.get("recommendation_type") or ""
    conf = run.confidence or 0.0
    return str(rec), conf

def _extract_risk_level(run, payload, evidence):
    """Exception Analysis: risk level assessment."""
    risk = evidence.get("risk_level") or ""
    conf = run.confidence or 0.0
    return str(risk), conf

def _extract_match_assessment(run, payload, evidence):
    """Reconciliation Assist: match discrepancy assessment."""
    summary = evidence.get("assessment") or evidence.get("summary") or ""
    if isinstance(summary, dict):
        import json
        summary = json.dumps(summary)
    conf = run.confidence or 0.0
    return str(summary)[:500], conf

def _extract_review_queue(run, payload, evidence):
    """Review Routing: which queue to route to."""
    queue = evidence.get("review_queue") or evidence.get("queue") or payload.get("recommendation_type") or ""
    conf = run.confidence or 0.0
    return str(queue), conf

def _extract_posting_status(run, payload, evidence):
    """System Posting Prep: overall posting readiness."""
    status = evidence.get("posting_status") or ""
    conf = run.confidence or 0.0
    return str(status), conf

def _extract_vendor_mapped(run, payload, evidence):
    """System Posting Prep: vendor mapping result."""
    mapped = evidence.get("vendor_mapped")
    conf = run.confidence or 0.0
    return str(mapped) if mapped is not None else "", conf


# Agent type -> list of (field_name, extractor)
_AGENT_FIELD_EXTRACTORS: Dict[str, List[tuple]] = {
    "PO_RETRIEVAL": [
        ("found_po", _extract_po_found),
        ("recommendation", _extract_recommendation),
    ],
    "GRN_RETRIEVAL": [
        ("found_grn", _extract_grn_found),
        ("recommendation", _extract_recommendation),
    ],
    "EXCEPTION_ANALYSIS": [
        ("recommendation", _extract_recommendation),
        ("risk_level", _extract_risk_level),
    ],
    "RECONCILIATION_ASSIST": [
        ("recommendation", _extract_recommendation),
        ("match_assessment", _extract_match_assessment),
    ],
    "REVIEW_ROUTING": [
        ("recommendation", _extract_recommendation),
        ("review_queue", _extract_review_queue),
    ],
    "CASE_SUMMARY": [
        ("recommendation", _extract_recommendation),
    ],
    "SYSTEM_REVIEW_ROUTING": [
        ("recommendation", _extract_recommendation),
        ("review_queue", _extract_review_queue),
    ],
    "SYSTEM_CASE_SUMMARY": [
        ("recommendation", _extract_recommendation),
    ],
    "SYSTEM_POSTING_PREPARATION": [
        ("posting_status", _extract_posting_status),
        ("vendor_mapped", _extract_vendor_mapped),
    ],
    "SYSTEM_CASE_INTAKE": [
        ("recommendation", _extract_recommendation),
    ],
    "SYSTEM_BULK_EXTRACTION_INTAKE": [
        ("recommendation", _extract_recommendation),
    ],
}


class AgentEvalAdapter:
    """Maps agent run outputs into core_eval EvalRun + metrics + field outcomes.

    Public API:
        sync_for_agent_run(agent_run)          -- per-agent eval
        sync_for_orchestration(orch_run, orch_result, result)  -- pipeline eval
    """

    # ------------------------------------------------------------------
    # Per-agent run
    # ------------------------------------------------------------------
    @classmethod
    def sync_for_agent_run(cls, agent_run) -> None:
        """Create EvalRun + metrics + field outcomes for a single agent run.

        Safe to call multiple times (upsert).  Fail-silent.
        """
        try:
            cls._sync_for_agent_run_inner(agent_run)
        except Exception:
            logger.exception(
                "AgentEvalAdapter.sync_for_agent_run failed "
                "for agent_run=%s (non-fatal)",
                getattr(agent_run, "pk", "?"),
            )

    @classmethod
    def _sync_for_agent_run_inner(cls, agent_run) -> None:
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core_eval.services.eval_field_outcome_service import EvalFieldOutcomeService
        from apps.core_eval.models import EvalRun

        run_pk = str(agent_run.pk)
        _tenant = getattr(agent_run, "tenant", None)
        agent_type = agent_run.agent_type or ""

        # -- Input snapshot --
        input_snap = {
            "agent_type": agent_type,
            "reconciliation_result_id": getattr(agent_run, "reconciliation_result_id", None),
            "invoice_id": getattr(agent_run.reconciliation_result, "invoice_id", None)
                if agent_run.reconciliation_result else None,
            "status": agent_run.status or "",
        }

        eval_run, _created = EvalRunService.create_or_update(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_AGENT_RUN,
            entity_id=run_pk,
            run_key=f"agent_run::{run_pk}",
            status=EvalRun.Status.COMPLETED if agent_run.status == "COMPLETED" else EvalRun.Status.FAILED,
            trace_id=agent_run.trace_id or "",
            input_snapshot_json=input_snap,
            tenant=_tenant,
        )

        # Timing
        now = timezone.now()
        _dirty = False
        if not eval_run.started_at and agent_run.started_at:
            eval_run.started_at = agent_run.started_at
            _dirty = True
        if not eval_run.completed_at and agent_run.completed_at:
            eval_run.completed_at = agent_run.completed_at
            _dirty = True
        if eval_run.duration_ms is None and agent_run.duration_ms is not None:
            eval_run.duration_ms = agent_run.duration_ms
            _dirty = True
        if _dirty:
            eval_run.save(update_fields=["started_at", "completed_at", "duration_ms", "updated_at"])

        # -- Metrics --
        def _m(name, value, **kw):
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name=name,
                value=value,
                value_type="float",
                tenant=_tenant,
                **kw,
            )

        _m("agent_confidence", float(agent_run.confidence or 0))
        _m("agent_status_completed", 1.0 if agent_run.status == "COMPLETED" else 0.0)
        _m("agent_duration_ms", float(agent_run.duration_ms or 0), unit="ms")

        output_payload = agent_run.output_payload or {}
        tools_used = output_payload.get("tools_used") or []
        _m("tools_used_count", float(len(tools_used)), unit="count")

        # Token usage
        if agent_run.prompt_tokens:
            _m("prompt_tokens", float(agent_run.prompt_tokens), unit="count")
        if agent_run.completion_tokens:
            _m("completion_tokens", float(agent_run.completion_tokens), unit="count")
        if agent_run.total_tokens:
            _m("total_tokens", float(agent_run.total_tokens), unit="count")

        # Recommendation
        rec_type = output_payload.get("recommendation_type") or ""
        if rec_type:
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="recommendation_type",
                value=rec_type,
                value_type="text",
                tenant=_tenant,
            )

        # Decisions count
        decisions = output_payload.get("decisions") or []
        _m("decisions_count", float(len(decisions)), unit="count")

        # -- Field outcomes: track key findings per agent type --
        extractors = _AGENT_FIELD_EXTRACTORS.get(agent_type, [])
        if extractors:
            evidence = output_payload.get("evidence") or {}
            outcomes = []
            for field_name, extractor_fn in extractors:
                try:
                    predicted, conf = extractor_fn(agent_run, output_payload, evidence)
                except Exception:
                    predicted, conf = "", 0.0

                status = "CORRECT" if predicted else "MISSING"
                if status == "MISSING":
                    conf = 0.0

                outcomes.append({
                    "field_name": field_name,
                    "status": status,
                    "predicted_value": predicted,
                    "ground_truth_value": "",
                    "confidence": conf,
                    "detail_json": {
                        "source": "agent",
                        "agent_type": agent_type,
                    },
                })

            if outcomes:
                EvalFieldOutcomeService.replace_for_run(
                    eval_run=eval_run, outcomes=outcomes, tenant=_tenant,
                )

    # ------------------------------------------------------------------
    # Pipeline-level orchestration
    # ------------------------------------------------------------------
    @classmethod
    def sync_for_orchestration(cls, orch_run, orch_result, result) -> None:
        """Create a pipeline-level EvalRun for the full orchestration.

        Captures final recommendation, confidence, agent count, etc.
        Fail-silent.
        """
        try:
            cls._sync_for_orchestration_inner(orch_run, orch_result, result)
        except Exception:
            logger.exception(
                "AgentEvalAdapter.sync_for_orchestration failed "
                "for orch_run=%s (non-fatal)",
                getattr(orch_run, "pk", "?"),
            )

    @classmethod
    def _sync_for_orchestration_inner(cls, orch_run, orch_result, result) -> None:
        from apps.core_eval.services.eval_run_service import EvalRunService
        from apps.core_eval.services.eval_metric_service import EvalMetricService
        from apps.core_eval.models import EvalRun

        run_pk = str(orch_run.pk)
        _tenant = getattr(orch_run, "tenant", None)

        input_snap = {
            "reconciliation_result_id": result.pk if result else None,
            "invoice_id": getattr(result, "invoice_id", None) if result else None,
            "match_status": getattr(result, "match_status", "") if result else "",
            "planned_agents": orch_result.planned_agents if hasattr(orch_result, "planned_agents") else [],
            "plan_source": getattr(orch_result, "plan_source", ""),
        }

        eval_run, _created = EvalRunService.create_or_update(
            app_module=APP_MODULE,
            entity_type=ENTITY_TYPE_ORCH_RUN,
            entity_id=run_pk,
            run_key=f"orchestration::{run_pk}",
            status=EvalRun.Status.COMPLETED if orch_run.status == "COMPLETED" else EvalRun.Status.FAILED,
            trace_id=getattr(orch_run, "trace_id", "") or "",
            input_snapshot_json=input_snap,
            tenant=_tenant,
        )

        # Timing
        if orch_run.completed_at and orch_run.created_at:
            eval_run.started_at = orch_run.created_at
            eval_run.completed_at = orch_run.completed_at
            if orch_run.duration_ms is not None:
                eval_run.duration_ms = orch_run.duration_ms
            eval_run.save(update_fields=["started_at", "completed_at", "duration_ms", "updated_at"])

        # -- Metrics --
        def _m(name, value, **kw):
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name=name,
                value=value,
                value_type="float",
                tenant=_tenant,
                **kw,
            )

        _m("agents_executed_count", float(len(orch_result.agents_executed)), unit="count")
        _m("final_confidence", float(orch_result.final_confidence or 0))
        _m("pipeline_status_completed",
           1.0 if orch_run.status == "COMPLETED" else 0.0)

        final_rec = orch_result.final_recommendation or ""
        if final_rec:
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="final_recommendation",
                value=final_rec,
                value_type="text",
                tenant=_tenant,
            )

        _m("has_recommendation", 1.0 if final_rec else 0.0)
        _m("auto_close_candidate", 1.0 if final_rec == "AUTO_CLOSE" else 0.0)
        _m("escalation_triggered", 1.0 if final_rec == "ESCALATE_TO_MANAGER" else 0.0)

        if orch_run.duration_ms is not None:
            _m("pipeline_duration_ms", float(orch_run.duration_ms), unit="ms")

        # Store executed agent types as JSON metric
        if orch_result.agents_executed:
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="agents_executed",
                value=list(orch_result.agents_executed),
                value_type="json",
                tenant=_tenant,
            )

        # -- Plan source tracking: enables LLM vs deterministic comparison --
        _plan_source = getattr(orch_run, "plan_source", "") or getattr(orch_result, "plan_source", "") or ""
        if _plan_source:
            EvalMetricService.upsert(
                eval_run=eval_run,
                metric_name="plan_source",
                value=_plan_source,
                value_type="text",
                tenant=_tenant,
            )
            _m("plan_source_is_llm", 1.0 if _plan_source == "llm" else 0.0)

        _plan_confidence = getattr(orch_run, "plan_confidence", None)
        if _plan_confidence is None:
            _plan_confidence = getattr(orch_result, "plan_confidence", None)
        if _plan_confidence is not None:
            _m("plan_confidence", float(_plan_confidence))

        _planned = getattr(orch_run, "planned_agents", None) or []
        _executed = orch_result.agents_executed or []
        if _planned:
            _m("planned_agents_count", float(len(_planned)), unit="count")
            # Plan adherence: fraction of planned agents that actually executed
            if _executed:
                _overlap = len(set(_planned) & set(_executed))
                _adherence = _overlap / len(_planned) if _planned else 0.0
                _m("plan_adherence", _adherence)
