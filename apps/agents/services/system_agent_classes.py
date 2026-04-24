"""Concrete deterministic system agent implementations.

Each class wraps an existing platform capability in the standard agent
framework, producing ``AgentRun`` and ``DecisionLog`` records without
LLM calls.

Agents
------
- ``SystemReviewRoutingAgent``       -- wraps DeterministicResolver routing
- ``SystemCaseSummaryAgent``         -- wraps DeterministicResolver summary
- ``SystemBulkExtractionIntakeAgent``-- wraps BulkExtractionService at job level
- ``SystemCaseIntakeAgent``          -- wraps case creation / stage init
- ``SystemPostingPreparationAgent``  -- wraps PostingPipeline preparation
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from apps.agents.services.base_agent import AgentContext, AgentOutput
from apps.agents.services.deterministic_system_agent import (
    DeterministicSystemAgent,
)
from apps.core.enums import AgentType

logger = logging.getLogger(__name__)


# =====================================================================
# STEP 2 -- Deterministic tail replacements
# =====================================================================


class SystemReviewRoutingAgent(DeterministicSystemAgent):
    """Deterministic review-routing agent.

    Wraps the existing ``DeterministicResolver`` rule matrix to produce
    a routing recommendation.  The business logic is identical to the
    current deterministic tail; only the agent lifecycle changes.
    """

    agent_type = AgentType.SYSTEM_REVIEW_ROUTING

    def execute_deterministic(self, ctx: AgentContext) -> AgentOutput:
        from apps.agents.services.deterministic_resolver import (
            DeterministicResolver,
        )

        resolver = DeterministicResolver()

        # Extract prior recommendation from context (passed via extra)
        prior_rec = ctx.extra.get("prior_recommendation")
        prior_conf = float(ctx.extra.get("prior_confidence", 0.0))

        result = ctx.reconciliation_result

        # Re-fetch fresh exceptions
        exceptions = self._fetch_exceptions(ctx)

        resolution = resolver.resolve(
            result,
            exceptions,
            prior_recommendation=prior_rec,
            prior_confidence=prior_conf,
        )

        decisions = [
            {
                "decision": f"Route to {resolution.recommendation_type}",
                "rationale": resolution.reasoning,
                "confidence": resolution.confidence,
                "evidence": resolution.evidence,
                "rule_name": "deterministic_resolver",
            },
        ]

        return AgentOutput(
            reasoning=resolution.reasoning,
            recommendation_type=resolution.recommendation_type,
            confidence=resolution.confidence,
            evidence=resolution.evidence,
            decisions=decisions,
        )

    def _fetch_exceptions(self, ctx: AgentContext) -> List[Dict[str, Any]]:
        """Fetch fresh exceptions from the reconciliation result."""
        if ctx.reconciliation_result is None:
            return ctx.exceptions or []
        try:
            fresh = list(
                ctx.reconciliation_result.exceptions.values(
                    "id", "exception_type", "severity",
                    "message", "details", "resolved",
                )
            )
            from apps.agents.services.base_agent import BaseAgent
            return BaseAgent._truncate_exceptions(fresh)
        except Exception:
            return ctx.exceptions or []


class SystemCaseSummaryAgent(DeterministicSystemAgent):
    """Deterministic case-summary agent.

    Wraps the existing ``DeterministicResolver`` to produce a case summary
    and the associated recommendation.
    """

    agent_type = AgentType.SYSTEM_CASE_SUMMARY

    def execute_deterministic(self, ctx: AgentContext) -> AgentOutput:
        from apps.agents.services.deterministic_resolver import (
            DeterministicResolver,
        )

        resolver = DeterministicResolver()

        prior_rec = ctx.extra.get("prior_recommendation")
        prior_conf = float(ctx.extra.get("prior_confidence", 0.0))

        result = ctx.reconciliation_result
        exceptions = self._fetch_exceptions(ctx)

        resolution = resolver.resolve(
            result,
            exceptions,
            prior_recommendation=prior_rec,
            prior_confidence=prior_conf,
        )

        decisions = [
            {
                "decision": "Case summary generated",
                "rationale": (
                    f"Summary built from {len(exceptions)} exception(s), "
                    f"match status={getattr(result, 'match_status', 'N/A')}"
                ),
                "confidence": resolution.confidence,
                "evidence": {
                    "exception_count": len(exceptions),
                    "match_status": getattr(result, "match_status", ""),
                    "recommendation_type": resolution.recommendation_type,
                },
            },
        ]

        return AgentOutput(
            reasoning=resolution.case_summary,
            recommendation_type=resolution.recommendation_type,
            confidence=resolution.confidence,
            evidence=resolution.evidence,
            decisions=decisions,
        )

    def _fetch_exceptions(self, ctx: AgentContext) -> List[Dict[str, Any]]:
        if ctx.reconciliation_result is None:
            return ctx.exceptions or []
        try:
            fresh = list(
                ctx.reconciliation_result.exceptions.values(
                    "id", "exception_type", "severity",
                    "message", "details", "resolved",
                )
            )
            from apps.agents.services.base_agent import BaseAgent
            return BaseAgent._truncate_exceptions(fresh)
        except Exception:
            return ctx.exceptions or []


# =====================================================================
# STEP 3 -- Platform capability wrappers
# =====================================================================


class SystemBulkExtractionIntakeAgent(DeterministicSystemAgent):
    """System agent representing bulk extraction intake at job level.

    This agent does NOT re-execute the extraction pipeline.  It wraps
    the job-level orchestration outcome (scan, register, dispatch) in an
    AgentRun record for governance visibility.

    Invoked after ``BulkExtractionService.run_job()`` completes, passing
    job statistics through ``ctx.extra``.
    """

    agent_type = AgentType.SYSTEM_BULK_EXTRACTION_INTAKE

    def execute_deterministic(self, ctx: AgentContext) -> AgentOutput:
        extra = ctx.extra or {}
        job_id = extra.get("job_id")
        total_items = int(extra.get("total_items", 0))
        processed = int(extra.get("processed", 0))
        failed = int(extra.get("failed", 0))
        skipped = int(extra.get("skipped", 0))
        duplicates = int(extra.get("duplicates", 0))
        job_status = extra.get("job_status", "UNKNOWN")

        success = job_status in ("COMPLETED", "COMPLETED_WITH_ERRORS") and processed > 0

        decisions: List[Dict[str, Any]] = []

        if duplicates > 0:
            decisions.append({
                "decision": f"Duplicate items detected: {duplicates}",
                "rationale": (
                    f"{duplicates} item(s) were duplicates and skipped "
                    f"during bulk intake"
                ),
                "confidence": 1.0,
                "evidence": {"duplicates": duplicates, "job_id": job_id},
            })

        if skipped > 0:
            decisions.append({
                "decision": f"Items skipped: {skipped}",
                "rationale": (
                    f"{skipped} item(s) skipped (credit blocked, invalid, "
                    f"or duplicate)"
                ),
                "confidence": 1.0,
                "evidence": {"skipped": skipped, "job_id": job_id},
            })

        if failed > 0:
            decisions.append({
                "decision": f"Items failed: {failed}",
                "rationale": f"{failed} item(s) failed during extraction",
                "confidence": 1.0,
                "evidence": {"failed": failed, "job_id": job_id},
            })

        decisions.append({
            "decision": f"Bulk job dispatched: {processed}/{total_items} items processed",
            "rationale": (
                f"Bulk extraction job {job_id} completed with status "
                f"{job_status}. {processed} of {total_items} item(s) "
                f"processed successfully."
            ),
            "confidence": 1.0 if success else 0.5,
            "evidence": {
                "job_id": job_id,
                "total_items": total_items,
                "processed": processed,
                "failed": failed,
                "skipped": skipped,
                "job_status": job_status,
            },
        })

        confidence = (
            processed / max(total_items, 1)
            if total_items > 0
            else (1.0 if success else 0.0)
        )
        confidence = max(0.0, min(1.0, confidence))

        reasoning = (
            f"Bulk extraction intake job {job_id}: "
            f"{processed}/{total_items} items processed, "
            f"{failed} failed, {skipped} skipped, "
            f"{duplicates} duplicate(s). Status: {job_status}."
        )

        return AgentOutput(
            reasoning=reasoning,
            confidence=confidence,
            evidence={
                "job_id": job_id,
                "total_items": total_items,
                "processed": processed,
                "failed": failed,
                "skipped": skipped,
                "duplicates": duplicates,
                "job_status": job_status,
            },
            decisions=decisions,
        )


class SystemCaseIntakeAgent(DeterministicSystemAgent):
    """System agent representing case creation and stage initialization.

    Wraps the deterministic creation of an APCase and its initial stages
    in an auditable AgentRun record.

    Invoked after case creation, passing case metadata through ``ctx.extra``.
    """

    agent_type = AgentType.SYSTEM_CASE_INTAKE

    def execute_deterministic(self, ctx: AgentContext) -> AgentOutput:
        extra = ctx.extra or {}
        case_id = extra.get("case_id")
        case_number = extra.get("case_number", "")
        processing_path = extra.get("processing_path", "")
        priority = extra.get("priority", 0)
        stage_count = int(extra.get("stage_count", 0))
        trigger = extra.get("trigger", "system")

        decisions: List[Dict[str, Any]] = []

        # Priority derivation
        decisions.append({
            "decision": f"Case priority set to {priority}",
            "rationale": (
                f"Priority derived from invoice attributes and "
                f"reconciliation status"
            ),
            "confidence": 1.0,
            "evidence": {
                "case_id": case_id,
                "priority": priority,
                "trigger": trigger,
            },
        })

        # Processing path
        if processing_path:
            decisions.append({
                "decision": f"Processing path: {processing_path}",
                "rationale": (
                    f"Case initialized with {processing_path} processing "
                    f"path based on reconciliation mode and PO availability"
                ),
                "confidence": 1.0,
                "evidence": {
                    "processing_path": processing_path,
                    "case_id": case_id,
                },
            })

        # Stage init
        if stage_count > 0:
            decisions.append({
                "decision": f"Case shell created with {stage_count} stages",
                "rationale": (
                    f"Initialized {stage_count} processing stage(s) for "
                    f"case {case_number}"
                ),
                "confidence": 1.0,
                "evidence": {
                    "case_id": case_id,
                    "case_number": case_number,
                    "stage_count": stage_count,
                },
            })

        reasoning = (
            f"Case {case_number} created via {trigger}. "
            f"Path: {processing_path or 'pending'}. "
            f"Priority: {priority}. "
            f"Stages initialized: {stage_count}."
        )

        return AgentOutput(
            reasoning=reasoning,
            confidence=1.0,
            evidence={
                "case_id": case_id,
                "case_number": case_number,
                "processing_path": processing_path,
                "priority": priority,
                "stage_count": stage_count,
                "trigger": trigger,
                "invoice_id": ctx.invoice_id,
            },
            decisions=decisions,
        )


class SystemPostingPreparationAgent(DeterministicSystemAgent):
    """System agent representing posting preparation / mapping orchestration.

    Wraps the deterministic posting pipeline preparation outcome in an
    auditable AgentRun record.

    Invoked after ``PostingPipeline.run()`` or ``PostingOrchestrator.prepare_posting()``
    completes, passing results through ``ctx.extra``.
    """

    agent_type = AgentType.SYSTEM_POSTING_PREPARATION

    def execute_deterministic(self, ctx: AgentContext) -> AgentOutput:
        extra = ctx.extra or {}
        posting_run_id = extra.get("posting_run_id")
        posting_status = extra.get("posting_status", "UNKNOWN")
        confidence_score = float(extra.get("confidence", 0.0))
        is_touchless = bool(extra.get("is_touchless", False))
        review_queues = extra.get("review_queues") or []
        vendor_mapped = extra.get("vendor_mapped", False)
        item_mapping_rate = float(extra.get("item_mapping_rate", 0.0))
        validation_errors = int(extra.get("validation_errors", 0))
        validation_warnings = int(extra.get("validation_warnings", 0))

        decisions: List[Dict[str, Any]] = []

        # Vendor mapping
        decisions.append({
            "decision": (
                "Vendor mapping successful"
                if vendor_mapped
                else "Vendor mapping failed or pending review"
            ),
            "rationale": (
                f"Vendor resolved={'yes' if vendor_mapped else 'no'} "
                f"via ERP reference lookup"
            ),
            "confidence": 1.0 if vendor_mapped else 0.5,
            "evidence": {
                "vendor_mapped": vendor_mapped,
                "posting_run_id": posting_run_id,
            },
        })

        # Validation
        if validation_errors > 0:
            decisions.append({
                "decision": f"Validation: {validation_errors} error(s) found",
                "rationale": (
                    f"Posting validation found {validation_errors} error(s) "
                    f"and {validation_warnings} warning(s)"
                ),
                "confidence": 1.0,
                "evidence": {
                    "validation_errors": validation_errors,
                    "validation_warnings": validation_warnings,
                },
            })

        # Review routing
        if review_queues:
            decisions.append({
                "decision": f"Posting review required: {', '.join(review_queues)}",
                "rationale": (
                    f"Posting routed to {len(review_queues)} review queue(s) "
                    f"based on mapping confidence and validation results"
                ),
                "confidence": confidence_score,
                "evidence": {
                    "review_queues": review_queues,
                    "is_touchless": is_touchless,
                },
            })
        elif is_touchless:
            decisions.append({
                "decision": "Posting ready for touchless submission",
                "rationale": (
                    "All mappings resolved with high confidence, no review "
                    "required"
                ),
                "confidence": confidence_score,
                "evidence": {"is_touchless": True},
            })

        # Overall readiness
        decisions.append({
            "decision": (
                f"Posting preparation: {posting_status}"
            ),
            "rationale": (
                f"Posting pipeline completed with status {posting_status}. "
                f"Confidence: {confidence_score:.0%}. "
                f"Item mapping rate: {item_mapping_rate:.0%}."
            ),
            "confidence": confidence_score,
            "evidence": {
                "posting_run_id": posting_run_id,
                "posting_status": posting_status,
                "confidence": confidence_score,
                "is_touchless": is_touchless,
                "item_mapping_rate": item_mapping_rate,
            },
        })

        reasoning = (
            f"Posting preparation for invoice {ctx.invoice_id}: "
            f"status={posting_status}, confidence={confidence_score:.0%}, "
            f"touchless={is_touchless}, "
            f"vendor_mapped={vendor_mapped}, "
            f"item_rate={item_mapping_rate:.0%}, "
            f"errors={validation_errors}, warnings={validation_warnings}."
        )

        return AgentOutput(
            reasoning=reasoning,
            confidence=confidence_score,
            evidence={
                "posting_run_id": posting_run_id,
                "posting_status": posting_status,
                "is_touchless": is_touchless,
                "vendor_mapped": vendor_mapped,
                "item_mapping_rate": item_mapping_rate,
                "validation_errors": validation_errors,
                "validation_warnings": validation_warnings,
                "review_queues": review_queues,
                "invoice_id": ctx.invoice_id,
            },
            decisions=decisions,
        )


class SystemExportFieldMappingAgent(DeterministicSystemAgent):
    """System agent representing deterministic export field mapping.

    Wraps export mapping outcomes for governance and audit visibility.
    Expected context payload (ctx.extra):
      - scope: "single" | "bulk"
      - invoices_count: int
      - header_unresolved_count: int
      - line_unresolved_count: int
      - ai_fallback_enabled: bool
      - ai_fallback_used: bool
      - ai_fields_applied: int
    """

    agent_type = AgentType.SYSTEM_EXPORT_FIELD_MAPPING

    def execute_deterministic(self, ctx: AgentContext) -> AgentOutput:
        extra = ctx.extra or {}
        scope = str(extra.get("scope", "single") or "single")
        invoices_count = int(extra.get("invoices_count", 1) or 1)
        header_unresolved_count = int(extra.get("header_unresolved_count", 0) or 0)
        line_unresolved_count = int(extra.get("line_unresolved_count", 0) or 0)
        ai_fallback_enabled = bool(extra.get("ai_fallback_enabled", False))
        ai_fallback_used = bool(extra.get("ai_fallback_used", False))
        ai_fields_applied = int(extra.get("ai_fields_applied", 0) or 0)

        total_unresolved = header_unresolved_count + line_unresolved_count
        resolved_without_ai = total_unresolved == 0

        decisions: List[Dict[str, Any]] = [
            {
                "decision": "Export field mapping completed",
                "rationale": (
                    f"scope={scope}, invoices={invoices_count}, "
                    f"unresolved_header={header_unresolved_count}, "
                    f"unresolved_line={line_unresolved_count}"
                ),
                "confidence": 1.0 if resolved_without_ai else 0.8,
                "evidence": {
                    "scope": scope,
                    "invoices_count": invoices_count,
                    "header_unresolved_count": header_unresolved_count,
                    "line_unresolved_count": line_unresolved_count,
                },
            },
            {
                "decision": (
                    "AI fallback used for unresolved fields"
                    if ai_fallback_used
                    else "AI fallback not used"
                ),
                "rationale": (
                    f"enabled={ai_fallback_enabled}, used={ai_fallback_used}, "
                    f"applied_fields={ai_fields_applied}"
                ),
                "confidence": 1.0,
                "evidence": {
                    "ai_fallback_enabled": ai_fallback_enabled,
                    "ai_fallback_used": ai_fallback_used,
                    "ai_fields_applied": ai_fields_applied,
                },
            },
        ]

        reasoning = (
            f"Export mapping completed for {invoices_count} invoice(s) in {scope} mode. "
            f"Unresolved fields: header={header_unresolved_count}, line={line_unresolved_count}. "
            f"AI fallback enabled={ai_fallback_enabled}, used={ai_fallback_used}, "
            f"applied_fields={ai_fields_applied}."
        )

        confidence = 1.0 if resolved_without_ai else (0.85 if ai_fallback_used else 0.75)
        confidence = max(0.0, min(1.0, confidence))

        return AgentOutput(
            reasoning=reasoning,
            confidence=confidence,
            evidence={
                "scope": scope,
                "invoices_count": invoices_count,
                "header_unresolved_count": header_unresolved_count,
                "line_unresolved_count": line_unresolved_count,
                "ai_fallback_enabled": ai_fallback_enabled,
                "ai_fallback_used": ai_fallback_used,
                "ai_fields_applied": ai_fields_applied,
                "invoice_id": ctx.invoice_id,
            },
            decisions=decisions,
        )
