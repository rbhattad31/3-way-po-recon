"""ProcurementPlanner -- deterministic planning engine for procurement agent sequencing.

Phase 5 implementation: promotes _ProcurementPlannerStub (embedded in orchestrator)
into a standalone, importable class with full execution chains, duplicate-run
exclusion, and partial-failure policies.

Usage::

    from apps.procurement.runtime.procurement_planner import ProcurementPlanner

    plan = ProcurementPlanner.plan_for_run(run)
    for agent_type in plan.agents:
        orchestrator.run(run=run, agent_type=agent_type, agent_fn=...)

    # Or from a context object:
    plan = ProcurementPlanner.plan_for_context(ctx)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Execution chains -- ordered agent sequences per analysis type
# ---------------------------------------------------------------------------

EXECUTION_CHAINS: Dict[str, List[str]] = {
    # RECOMMENDATION: deterministic rules + optional LLM explanation +
    # compliance check + market intelligence enrichment
    "RECOMMENDATION": [
        "recommendation",
        "compliance",
        "market_intelligence",
    ],
    # BENCHMARK: single-pass should-cost resolution then web-search fallback
    "BENCHMARK": [
        "benchmark",
    ],
    # VALIDATION: structural + business-rule validation augmented by LLM
    "VALIDATION": [
        "validation_augmentation",
    ],
    # MARKET_INTELLIGENCE: standalone Perplexity / scraper run
    "MARKET_INTELLIGENCE": [
        "market_intelligence",
    ],
}

# Analysis types that abort the plan on the first agent failure vs. continue.
_ABORT_ON_FAILURE: frozenset[str] = frozenset({"BENCHMARK", "VALIDATION"})


# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------

@dataclass
class ProcurementPlan:
    """Ordered execution plan produced by ProcurementPlanner.

    Attributes:
        agents:                Ordered list of agent_type strings.
        analysis_type:         Resolved analysis type (upper-cased).
        partial_failure_policy: "continue" or "abort".  BENCHMARK and VALIDATION
                               abort on first error; RECOMMENDATION continues past
                               optional steps.
        metadata:              Diagnostic metadata about how the plan was built.
    """

    agents: List[str] = field(default_factory=list)
    analysis_type: str = ""
    partial_failure_policy: str = "continue"   # "continue" | "abort"
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------

    def __iter__(self) -> Iterator[str]:
        return iter(self.agents)

    def __len__(self) -> int:
        return len(self.agents)

    def is_empty(self) -> bool:
        return not self.agents


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class ProcurementPlanner:
    """Deterministic planning engine for procurement agent sequencing.

    Priority chain for building the agent list:
        1. Explicit list from ``extra_context["planned_agents"]``.
        2. Analysis-type => execution-chain mapping (EXECUTION_CHAINS).
        3. Safe fallback to ``["recommendation"]``.

    Additional features:
    - Deduplication of agent names (order preserved).
    - Duplicate-run exclusion: agents already RUNNING for the same AnalysisRun
      are removed from the plan to avoid concurrent double-execution.
    - Partial-failure policy: BENCHMARK / VALIDATION use "abort"; others use
      "continue" so optional enrichment steps do not block core analysis.
    """

    _LOG_PREFIX = "ProcurementPlanner"

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    @classmethod
    def plan_for_context(cls, ctx: Any) -> ProcurementPlan:
        """Build a plan from a ProcurementAgentContext (or any context-like obj).

        Args:
            ctx: Object with `analysis_type` + `extra_context` attributes.

        Returns:
            ProcurementPlan.
        """
        analysis_type = str(getattr(ctx, "analysis_type", "") or "").upper()
        extra = dict(getattr(ctx, "extra_context", {}) or {})
        return cls.plan(analysis_type=analysis_type, extra_context=extra)

    @classmethod
    def plan_for_run(
        cls,
        run: Any,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> ProcurementPlan:
        """Build a plan directly from an AnalysisRun model instance.

        Reads currently-RUNNING ProcurementAgentExecutionRecord rows for this run
        and excludes those agent_types from the plan (duplicate-run guard).

        Args:
            run:           AnalysisRun instance.
            extra_context: Optional additional context overrides.

        Returns:
            ProcurementPlan.
        """
        analysis_type = str(getattr(run, "run_type", "") or "").upper()
        extra = dict(extra_context or {})

        running_agents: List[str] = cls._fetch_running_agents(run)

        return cls.plan(
            analysis_type=analysis_type,
            extra_context=extra,
            running_agents=running_agents,
        )

    @classmethod
    def plan(
        cls,
        *,
        analysis_type: str = "",
        extra_context: Optional[Dict[str, Any]] = None,
        running_agents: Optional[List[str]] = None,
    ) -> ProcurementPlan:
        """Build an execution plan from raw inputs.

        Args:
            analysis_type:  Upper-cased analysis type string.
            extra_context:  Optional dict; ``planned_agents`` key takes priority.
            running_agents: Agent type strings currently running (excluded from plan).

        Returns:
            ProcurementPlan.
        """
        extra = dict(extra_context or {})
        running = set(running_agents or [])

        agents, source = cls._resolve_agents(analysis_type, extra)

        if running:
            pre_count = len(agents)
            skipped = [a for a in agents if a in running]
            agents = [a for a in agents if a not in running]
            if skipped:
                logger.info(
                    "%s: duplicate-run guard excluded %d agent(s): %s",
                    cls._LOG_PREFIX, len(skipped), skipped,
                )
            logger.debug(
                "%s: plan filtered %d -> %d agents (excluded running: %s)",
                cls._LOG_PREFIX, pre_count, len(agents), list(running),
            )

        policy = cls._resolve_partial_failure_policy(analysis_type)

        return ProcurementPlan(
            agents=agents,
            analysis_type=analysis_type,
            partial_failure_policy=policy,
            metadata={
                "source": source,
                "analysis_type": analysis_type,
                "agent_count": len(agents),
                "partial_failure_policy": policy,
                "excluded_running": sorted(running),
            },
        )

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @classmethod
    def execution_chain_for(cls, analysis_type: str) -> List[str]:
        """Return the canonical execution chain for an analysis type."""
        return list(EXECUTION_CHAINS.get(analysis_type.upper(), ["recommendation"]))

    @classmethod
    def all_analysis_types(cls) -> List[str]:
        """Return all analysis types that have a defined execution chain."""
        return list(EXECUTION_CHAINS.keys())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_agents(
        cls,
        analysis_type: str,
        extra: Dict[str, Any],
    ) -> tuple[List[str], str]:
        """Resolve ordered agent list and label of source used.

        Returns:
            (agents list, source label)
        """
        explicit = extra.get("planned_agents")
        if isinstance(explicit, list) and explicit:
            deduped = cls._dedup(str(v or "").strip().lower() for v in explicit)
            if deduped:
                return deduped, "explicit_context"

        chain = EXECUTION_CHAINS.get(analysis_type.upper())
        if chain:
            return list(chain), "type_mapping"

        logger.info(
            "%s: unknown analysis_type=%r -- fallback to [recommendation]",
            cls._LOG_PREFIX, analysis_type,
        )
        return ["recommendation"], "fallback"

    @staticmethod
    def _dedup(items: Iterator[str]) -> List[str]:
        seen: set[str] = set()
        result: List[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result

    @classmethod
    def _resolve_partial_failure_policy(cls, analysis_type: str) -> str:
        return "abort" if analysis_type.upper() in _ABORT_ON_FAILURE else "continue"

    @classmethod
    def _fetch_running_agents(cls, run: Any) -> List[str]:
        """Query DB for in-progress agents for this run (fail-open)."""
        try:
            from apps.core.enums import AnalysisRunStatus
            from apps.procurement.models import ProcurementAgentExecutionRecord
            qs = ProcurementAgentExecutionRecord.objects.filter(
                run=run,
                status=AnalysisRunStatus.RUNNING,
            ).values_list("agent_type", flat=True)
            return list(qs)
        except Exception:
            logger.debug(
                "%s: duplicate-run DB check failed open (non-fatal)",
                cls._LOG_PREFIX,
                exc_info=True,
            )
            return []
