"""MarketIntelligenceService -- agent-first market intelligence wrapper."""
from __future__ import annotations

from apps.procurement.agents.Perplexity_Market_Research_Analyst_Agent import (
    PerplexityMarketResearchAnalystAgent,
)


class MarketIntelligenceService:
    """Facade over market-intelligence agents for compatibility imports."""

    @staticmethod
    def generate_auto(proc_request, generated_by=None, run=None, request_user=None):
        agent = PerplexityMarketResearchAnalystAgent()
        return agent.run(proc_request, generated_by=generated_by or request_user)

    @staticmethod
    def generate_with_perplexity(proc_request, generated_by=None, run=None, request_user=None):
        agent = PerplexityMarketResearchAnalystAgent()
        return agent.run(proc_request, generated_by=generated_by or request_user)

    @staticmethod
    def get_rec_context(proc_request):
        return PerplexityMarketResearchAnalystAgent.get_rec_context(proc_request)
