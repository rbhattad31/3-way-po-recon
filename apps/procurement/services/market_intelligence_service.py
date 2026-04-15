"""MarketIntelligenceService -- agent-first market intelligence wrapper."""
from __future__ import annotations

from apps.core.enums import AgentType
from apps.procurement.agents.Perplexity_Market_Research_Analyst_Agent import (
    PerplexityMarketResearchAnalystAgent,
)
from apps.procurement.services.agent_run_tracking import run_procurement_component_with_tracking


class MarketIntelligenceService:
    """Facade over market-intelligence agents for compatibility imports."""

    @staticmethod
    def generate_auto(proc_request, generated_by=None, run=None, request_user=None):
        actor_user = generated_by or request_user
        return run_procurement_component_with_tracking(
            agent_type=AgentType.PROCUREMENT_MARKET_INTELLIGENCE,
            invocation_reason="PerplexityMarketResearchAnalystAgent.run",
            tenant=getattr(proc_request, "tenant", None),
            actor_user=actor_user,
            input_payload={
                "source": "market_intelligence",
                "procurement_request_id": str(getattr(proc_request, "request_id", "")),
                "procurement_request_pk": getattr(proc_request, "pk", None),
                "analysis_run_id": getattr(run, "pk", None) if run else None,
            },
            execute_fn=lambda: PerplexityMarketResearchAnalystAgent().run(
                proc_request,
                generated_by=actor_user,
            ),
        )

    @staticmethod
    def generate_with_perplexity(proc_request, generated_by=None, run=None, request_user=None):
        return MarketIntelligenceService.generate_auto(
            proc_request,
            generated_by=generated_by,
            run=run,
            request_user=request_user,
        )

    @staticmethod
    def get_rec_context(proc_request):
        return PerplexityMarketResearchAnalystAgent.get_rec_context(proc_request)
