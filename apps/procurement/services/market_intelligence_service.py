"""Market Intelligence service - compatibility wrapper.

All logic lives in PerplexityMarketResearchAnalystAgent:
    apps/procurement/agents/Perplexity_Market_Research_Analyst_Agent.py

This module exists so views, Celery tasks, and management commands
continue to import MarketIntelligenceService without modification.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MarketIntelligenceService:
    """Compatibility facade -- delegates all generation to PerplexityMarketResearchAnalystAgent.

    All logic lives in:
        apps/procurement/agents/Perplexity_Market_Research_Analyst_Agent.py

    This wrapper exists so views, Celery tasks, and management commands
    continue to import MarketIntelligenceService without modification.
    """

    _agent = None
    _fallback_agent = None

    @classmethod
    def _get_agent(cls):
        from apps.procurement.agents.Perplexity_Market_Research_Analyst_Agent import (
            PerplexityMarketResearchAnalystAgent,
        )
        if cls._agent is None:
            cls._agent = PerplexityMarketResearchAnalystAgent()
        return cls._agent

    @classmethod
    def _get_fallback_agent(cls):
        from apps.procurement.agents.Fallback_Webscraper_Agent import FallbackWebscraperAgent
        if cls._fallback_agent is None:
            cls._fallback_agent = FallbackWebscraperAgent()
        return cls._fallback_agent

    @staticmethod
    def get_attrs_block(proc_request) -> str:
        from apps.procurement.agents.Perplexity_Market_Research_Analyst_Agent import (
            PerplexityMarketResearchAnalystAgent,
        )
        return PerplexityMarketResearchAnalystAgent.get_attrs_block(proc_request)

    @staticmethod
    def get_rec_context(proc_request) -> tuple:
        from apps.procurement.agents.Perplexity_Market_Research_Analyst_Agent import (
            PerplexityMarketResearchAnalystAgent,
        )
        return PerplexityMarketResearchAnalystAgent.get_rec_context(proc_request)

    @classmethod
    def generate_with_perplexity(cls, proc_request, generated_by=None) -> dict:
        """Delegate to PerplexityMarketResearchAnalystAgent.run()."""
        return cls._get_agent().run(proc_request, generated_by=generated_by)

    @classmethod
    def generate_auto(
        cls,
        proc_request,
        generated_by=None,
        *,
        run: Optional[Any] = None,
        request_user: Any = None,
    ) -> dict:
        """Generate market intelligence.

        If an AnalysisRun is provided, execution is routed through
        ProcurementAgentOrchestrator for centralized guardrails, audit, trace,
        and AgentRun mirror persistence. On orchestrator failure, the service
        safely falls back to direct execution.
        """
        if run is not None:
            try:
                from apps.procurement.runtime import ProcurementAgentMemory, ProcurementAgentOrchestrator

                orchestrator = ProcurementAgentOrchestrator()
                memory = ProcurementAgentMemory()

                def _agent_fn(ctx):  # noqa: ANN001
                    return cls._generate_auto_core(proc_request, generated_by=generated_by)

                orch_result = orchestrator.run(
                    run=run,
                    agent_type="market_intelligence",
                    agent_fn=_agent_fn,
                    memory=memory,
                    extra_context={
                        "request_id": getattr(proc_request, "request_id", ""),
                        "request_pk": getattr(proc_request, "pk", None),
                    },
                    request_user=request_user or generated_by,
                )

                if orch_result.status == "completed" and orch_result.output:
                    return orch_result.output

                logger.warning(
                    "MarketIntelligenceService.generate_auto: orchestrator path did not complete "
                    "for request pk=%s, using direct fallback path. status=%s error=%s",
                    getattr(proc_request, "pk", None),
                    orch_result.status,
                    orch_result.error,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "MarketIntelligenceService.generate_auto: orchestrator routing failed "
                    "for request pk=%s, using direct fallback path. error=%s",
                    getattr(proc_request, "pk", None),
                    exc,
                )

        return cls._generate_auto_core(proc_request, generated_by=generated_by)

    @classmethod
    def _generate_auto_core(cls, proc_request, generated_by=None) -> dict:
        """Generate market intelligence.

        Primary path  : Perplexity live web search (if PERPLEXITY_API_KEY is set).
        Fallback path : FallbackWebscraperAgent (Azure OpenAI site selection +
                        Playwright headless scraping).

        The fallback is triggered automatically when ANY of these happen:
          1. PERPLEXITY_API_KEY is not configured.
          2. Perplexity raises any exception (network error, bad JSON, 4xx/5xx).
          3. Perplexity returns a result with zero suggestions (empty / blank info).

        Args:
            proc_request: ProcurementRequest instance.
            generated_by:  User instance or None.

        Returns:
            dict with keys: system_code, system_name, rephrased_query,
            ai_summary, market_context, suggestions, perplexity_citations.

        Raises:
            ValueError / Exception: only if BOTH paths fail.
        """
        from django.conf import settings

        perplexity_key = getattr(settings, "PERPLEXITY_API_KEY", "")
        perplexity_error: Exception | None = None

        # --- primary: Perplexity ---
        if perplexity_key:
            try:
                result = cls.generate_with_perplexity(proc_request, generated_by=generated_by)
                if result.get("suggestions"):
                    # Real suggestions returned -- use them
                    return result
                # Perplexity returned successfully but with zero suggestions
                perplexity_error = ValueError(
                    "Perplexity returned no product suggestions (empty response)."
                )
                logger.warning(
                    "MarketIntelligenceService.generate_auto: Perplexity returned "
                    "0 suggestions for pk=%s -- switching to web-scraping fallback.",
                    proc_request.pk,
                )
            except Exception as exc:  # noqa: BLE001
                perplexity_error = exc
                logger.warning(
                    "MarketIntelligenceService.generate_auto: Perplexity failed "
                    "(pk=%s) -- switching to web-scraping fallback. error=%s",
                    proc_request.pk, exc,
                )
        else:
            logger.info(
                "MarketIntelligenceService.generate_auto: no PERPLEXITY_API_KEY "
                "-- using web-scraping fallback directly (pk=%s)",
                proc_request.pk,
            )

        # --- fallback: FallbackWebscraperAgent ---
        try:
            return cls._get_fallback_agent().run(proc_request, generated_by=generated_by)
        except Exception as fallback_exc:  # noqa: BLE001
            logger.error(
                "MarketIntelligenceService.generate_auto: fallback also failed "
                "(pk=%s): %s",
                proc_request.pk, fallback_exc,
            )
            if perplexity_error:
                raise ValueError(
                    f"Both Perplexity and the web-scraping fallback failed. "
                    f"Perplexity: {perplexity_error}. Fallback: {fallback_exc}"
                ) from fallback_exc
            raise

    @classmethod
    def has_existing(cls, proc_request) -> bool:
        """Return True if at least one MarketIntelligenceSuggestion exists for this request."""
        from apps.procurement.models import MarketIntelligenceSuggestion
        return MarketIntelligenceSuggestion.objects.filter(request=proc_request).exists()

    @classmethod
    def get_latest(cls, proc_request):
        """Return the most recent MarketIntelligenceSuggestion for this request, or None."""
        from apps.procurement.models import MarketIntelligenceSuggestion
        return (
            MarketIntelligenceSuggestion.objects
            .filter(request=proc_request)
            .order_by("-created_at")
            .first()
        )
