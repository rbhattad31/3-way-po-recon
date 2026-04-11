"""ProcurementAgentMemory -- structured in-process memory for procurement agent pipelines.

Aligned conceptually to AgentMemory (apps.agents.services.agent_memory) but
specialised for procurement domain findings.

Design notes:
- Plain dataclass, no DB persistence (findings are written to AnalysisRun result objects).
- Created once per ProcurementAgentOrchestrator.run() call.
- Updated by the orchestrator after each agent invocation via record_agent_output().
- Later agents can read earlier agents' findings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ProcurementAgentMemory:
    """Structured memory bag shared across procurement agent pipeline agents."""

    # -----------------------------------------------------------------------
    # Recommendation domain
    # -----------------------------------------------------------------------

    # Best solution/product recommendation seen so far
    recommended_solution: Optional[str] = None

    # Category or class of the recommended solution (e.g. "Split AC 2TR Inverter")
    recommended_category: Optional[str] = None

    # -----------------------------------------------------------------------
    # Benchmark domain
    # -----------------------------------------------------------------------

    # Market benchmark findings keyed by line item description or category
    # e.g. {"AHU_5TR": {"min": 120000, "avg": 145000, "max": 175000, "source": "ai_estimate"}}
    benchmark_findings: Dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Compliance domain
    # -----------------------------------------------------------------------

    # High-level compliance findings from ComplianceAgent or ComplianceService
    compliance_findings: Dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Validation domain
    # -----------------------------------------------------------------------

    # Flags raised by validation agents (item_code -> issue text)
    validation_flags: Dict[str, str] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Market signal domain
    # -----------------------------------------------------------------------

    # Free-form market context or signals for downstream enrichment
    market_signals: List[str] = field(default_factory=list)

    # -----------------------------------------------------------------------
    # Cross-agent summary store (agent_type -> short summary; capped at 500 chars)
    # -----------------------------------------------------------------------
    agent_summaries: Dict[str, str] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Cross-agent facts store (free-form key/value populated by any agent)
    # -----------------------------------------------------------------------
    facts: Dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Current best recommendation and confidence (updated after each agent)
    # -----------------------------------------------------------------------
    current_recommendation: Optional[str] = None
    current_confidence: float = 0.0

    # -----------------------------------------------------------------------
    # Mutation helpers
    # -----------------------------------------------------------------------

    def record_agent_output(self, agent_type: str, output: Any) -> None:
        """Update memory from an agent output object or dict.

        Accepts either:
        - An object with .reasoning, .recommendation_type, .confidence, .evidence attrs
        - A plain dict with the same keys

        Args:
            agent_type: String identifier for the agent (e.g. "recommendation", "benchmark").
            output: Agent output object exposing reasoning, recommendation_type,
                    confidence, and evidence.
        """
        # Support both object-style and dict-style outputs
        if isinstance(output, dict):
            reasoning: str = output.get("reasoning_summary") or output.get("reasoning") or ""
            rec_type: Optional[str] = (
                output.get("recommendation_type")
                or output.get("recommended_option")
            )
            confidence: float = float(output.get("confidence") or output.get("confidence_score") or 0.0)
            evidence: Dict[str, Any] = output.get("evidence") or {}
        else:
            reasoning = getattr(output, "reasoning", "") or ""
            rec_type = getattr(output, "recommendation_type", None) or getattr(output, "recommended_option", None)
            confidence = float(getattr(output, "confidence", 0.0) or 0.0)
            evidence = getattr(output, "evidence", {}) or {}

        # Store reasoning summary (capped at 500 chars)
        self.agent_summaries[agent_type] = reasoning[:500]

        # Promote recommendation if this agent is more confident
        if rec_type and confidence > self.current_confidence:
            self.current_recommendation = str(rec_type)
            self.current_confidence = confidence
            if not self.recommended_solution:
                self.recommended_solution = str(rec_type)

        # Absorb benchmark findings from evidence
        bm = evidence.get("benchmark_findings")
        if bm and isinstance(bm, dict):
            self.benchmark_findings.update(bm)

        # Absorb compliance findings from evidence
        cf = evidence.get("compliance_findings") or evidence.get("compliance_result")
        if cf and isinstance(cf, dict):
            self.compliance_findings.update(cf)

        # Absorb validation flags from evidence
        vf = evidence.get("validation_flags")
        if vf and isinstance(vf, dict):
            self.validation_flags.update(vf)

        # Absorb market signals
        ms = evidence.get("market_signals")
        if ms and isinstance(ms, list):
            self.market_signals.extend(ms)

    def to_snapshot(self) -> Dict[str, Any]:
        """Return a serializable snapshot of current memory state."""
        return {
            "recommended_solution": self.recommended_solution,
            "recommended_category": self.recommended_category,
            "current_recommendation": self.current_recommendation,
            "current_confidence": self.current_confidence,
            "agent_summaries": self.agent_summaries,
            "benchmark_finding_keys": list(self.benchmark_findings.keys()),
            "validation_flag_count": len(self.validation_flags),
            "compliance_findings_present": bool(self.compliance_findings),
            "market_signal_count": len(self.market_signals),
            "fact_keys": list(self.facts.keys()),
        }
