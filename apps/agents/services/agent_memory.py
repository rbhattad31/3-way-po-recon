"""Structured in-process memory shared across agents within a single orchestration run.

AgentMemory is a plain dataclass (no DB persistence). It is created once per
orchestration call, attached to AgentContext, and updated after each agent run
so that later agents can read findings from earlier ones.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentMemory:
    """Structured memory bag passed through a multi-agent pipeline run."""

    # PO number resolved by a PO retrieval agent (may differ from invoice text).
    resolved_po_number: Optional[str] = None

    # GRN numbers confirmed by a GRN retrieval agent.
    resolved_grn_numbers: List[str] = field(default_factory=list)

    # Extraction quality concerns surfaced by the extraction QA agent.
    extraction_issues: List[str] = field(default_factory=list)

    # Reasoning summaries keyed by agent_type string (max 500 chars each).
    agent_summaries: Dict[str, str] = field(default_factory=dict)

    # Highest-confidence recommendation seen so far across all agents.
    current_recommendation: Optional[str] = None
    current_confidence: float = 0.0

    # Free-form key/value store for agent-specific facts.
    facts: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutating helpers
    # ------------------------------------------------------------------

    def record_agent_output(self, agent_type: str, output) -> None:
        """Update memory from an agent output object.

        Args:
            agent_type: String value of the AgentType enum for the agent that ran.
            output: An object exposing:
                - .reasoning  (str)  -- text summary of the agent's reasoning
                - .recommendation_type (str or None)
                - .confidence (float)
                - .evidence (dict)
        """
        # Store reasoning summary (capped at 500 chars).
        reasoning = output.reasoning or ""
        self.agent_summaries[agent_type] = reasoning[:500]

        # Promote recommendation if this agent is more confident.
        rec_type = output.recommendation_type
        if rec_type is not None and output.confidence > self.current_confidence:
            self.current_recommendation = rec_type
            self.current_confidence = output.confidence

        # Capture resolved PO number from evidence if available.
        evidence = output.evidence or {}
        found_po = evidence.get("found_po")
        if found_po and isinstance(found_po, str) and found_po.strip():
            self.resolved_po_number = found_po.strip()
