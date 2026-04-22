"""Dynamic negotiation assistant service for benchmarking requests."""
from __future__ import annotations

import logging
from typing import Any, Dict

from apps.core.decorators import observed_service
from apps.benchmarking.models import BenchmarkLineItem
from apps.benchmarking.services.benchmark_service import BenchmarkEngine
from apps.benchmarking.agents.Negotiation_Talking_Points_Agent_BM import BenchmarkNegotiationTalkingPointsAgentBM

logger = logging.getLogger(__name__)


class BenchmarkNegotiationAssistantService:
    """Builds live benchmark context and returns dynamic LLM negotiation guidance."""

    @classmethod
    @observed_service("BenchmarkNegotiationAssistantService")
    def answer_prompt(
        cls,
        *,
        bench_request,
        user_prompt: str,
    ) -> Dict[str, Any]:
        prompt = str(user_prompt or "").strip()
        if not prompt:
            return {"success": False, "error": "Question is required."}

        line_items = list(
            BenchmarkLineItem.objects.filter(
                quotation__request=bench_request,
                quotation__is_active=True,
                is_active=True,
            ).select_related("quotation")
        )
        if not line_items:
            return {
                "success": False,
                "error": "No benchmark line items found. Run analysis first.",
            }

        vendor_cards = BenchmarkEngine._build_vendor_cards(bench_request=bench_request)

        ai_output = cls._latest_stage_output(bench_request_pk=bench_request.pk, stage_name="AI_Insights_Analyzer")
        compliance_output = cls._latest_stage_output(bench_request_pk=bench_request.pk, stage_name="Compliance")
        vendor_output = cls._latest_stage_output(bench_request_pk=bench_request.pk, stage_name="Vendor_Recommendation")

        if not vendor_output:
            try:
                from apps.benchmarking.agents.Vendor_Recommendation_Agent_BM import BenchmarkVendorRecommendationAgent

                vendor_output = BenchmarkVendorRecommendationAgent.recommend(vendor_cards=vendor_cards)
            except Exception:
                logger.exception("Vendor recommendation fallback failed")
                vendor_output = {}

        response = BenchmarkNegotiationTalkingPointsAgentBM.respond(
            bench_request=bench_request,
            line_items=line_items,
            vendor_cards=vendor_cards,
            ai_output=ai_output or {},
            compliance_output=compliance_output or {},
            vendor_output=vendor_output or {},
            user_prompt=prompt,
        )

        return {
            "success": True,
            "answer": str(response.get("answer") or "").strip(),
            "confidence": float(response.get("confidence") or 0.0),
            "recommended_vendor": str(response.get("recommended_vendor") or "").strip(),
            "next_best_question": str(response.get("next_best_question") or "").strip(),
            "risk_flags": [
                str(item).strip()
                for item in (response.get("risk_flags") or [])
                if str(item).strip()
            ][:8],
            "source": str(response.get("source") or "").strip() or "llm",
        }

    @classmethod
    def _latest_stage_output(cls, *, bench_request_pk: int, stage_name: str) -> Dict[str, Any]:
        try:
            from apps.agents.models import AgentRun

            run = (
                AgentRun.objects.filter(
                    input_payload__benchmark_request_pk=bench_request_pk,
                    input_payload__agent_stage=stage_name,
                )
                .order_by("-started_at", "-pk")
                .first()
            )
            if not run:
                return {}
            payload = run.output_payload or {}
            return payload if isinstance(payload, dict) else {}
        except Exception:
            logger.debug("Unable to resolve latest stage output for %s", stage_name, exc_info=True)
            return {}
