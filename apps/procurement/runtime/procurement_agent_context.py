"""ProcurementAgentContext -- procurement-specific context bag for agent runs.

Aligned conceptually to AgentContext (apps.agents.services.base_agent) but
tailored to procurement domain objects. No reconciliation references.

Design notes:
- Plain dataclass, no DB persistence.
- Created by ProcurementAgentOrchestrator before invoking any agent.
- Passed into every procurement agent execute() call.
- Serializable via asdict() for storage in AnalysisRun.input_snapshot_json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from apps.procurement.runtime.procurement_agent_memory import ProcurementAgentMemory


@dataclass
class ProcurementAgentContext:
    """Immutable (by convention) context bag for a single procurement agent run.

    Fields that carry None indicate "not available in this invocation" -- agents
    must handle None gracefully.
    """

    # -----------------------------------------------------------------------
    # Core business identifiers
    # -----------------------------------------------------------------------
    procurement_request_id: int = 0          # ProcurementRequest.pk
    analysis_run_id: int = 0                 # AnalysisRun.pk
    analysis_type: str = ""                  # AnalysisRunType value

    # -----------------------------------------------------------------------
    # Domain / schema context
    # -----------------------------------------------------------------------
    domain_code: str = ""                    # e.g. "HVAC", "IT", "FACILITIES"
    schema_code: str = ""                    # Attribute schema identifier

    # -----------------------------------------------------------------------
    # Normalised request attributes (key -> value)
    # -----------------------------------------------------------------------
    attributes: Dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Quotation summary data for quote-backed agents
    # -----------------------------------------------------------------------
    quotation_summaries: List[Dict[str, Any]] = field(default_factory=list)
    # e.g. [{"vendor": "...", "total": 12000, "lines": 5, "quotation_id": 7}]

    # -----------------------------------------------------------------------
    # Validation context -- findings from the deterministic phase
    # -----------------------------------------------------------------------
    validation_context: Dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # Constraints / assumptions propagated from deterministic step
    # -----------------------------------------------------------------------
    constraints: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)

    # -----------------------------------------------------------------------
    # Rule result from deterministic phase (may be partially complete)
    # -----------------------------------------------------------------------
    rule_result: Dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------------
    # RBAC / actor context (populated by orchestrator when available)
    # -----------------------------------------------------------------------
    actor_user_id: Optional[int] = None
    actor_primary_role: str = ""
    actor_roles_snapshot: List[str] = field(default_factory=list)
    permission_checked: str = ""
    permission_source: str = ""
    access_granted: bool = False

    # -----------------------------------------------------------------------
    # Trace identifiers (from TraceContext if available)
    # -----------------------------------------------------------------------
    trace_id: str = ""
    span_id: str = ""

    # -----------------------------------------------------------------------
    # Shared in-process memory (populated by orchestrator, shared across agents)
    # -----------------------------------------------------------------------
    memory: Optional[ProcurementAgentMemory] = None

    # -----------------------------------------------------------------------
    # Langfuse trace handle (not serialized -- runtime only)
    # -----------------------------------------------------------------------
    _langfuse_trace: Any = None

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def to_snapshot(self) -> Dict[str, Any]:
        """Return a serializable snapshot suitable for AnalysisRun.input_snapshot_json.

        Excludes non-serializable fields (_langfuse_trace, memory object).
        """
        return {
            "procurement_request_id": self.procurement_request_id,
            "analysis_run_id": self.analysis_run_id,
            "analysis_type": self.analysis_type,
            "domain_code": self.domain_code,
            "schema_code": self.schema_code,
            "attribute_count": len(self.attributes),
            "quotation_count": len(self.quotation_summaries),
            "has_validation_context": bool(self.validation_context),
            "constraint_count": len(self.constraints),
            "actor_user_id": self.actor_user_id,
            "actor_primary_role": self.actor_primary_role,
            "access_granted": self.access_granted,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }
