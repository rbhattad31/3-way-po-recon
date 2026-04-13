"""Compatibility RecommendationAgent wrapper.

This shim keeps legacy imports working after moving recommendation logic into
HVACRecommendationAgent. It is used by RecommendationGraphService and tests
that patch the historical module path.
"""
from __future__ import annotations

from typing import Any, Dict

from apps.procurement.agents.hvac_recommendation_agent import HVACRecommendationAgent


class RecommendationAgent:
    """Backward-compatible wrapper around HVACRecommendationAgent."""

    @staticmethod
    def execute_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        attributes = payload.get("attributes") or {}
        rule_result = payload.get("rule_result") or {}
        validation_context = payload.get("validation_context") or {}

        # Rule-matched path: deterministic engine already selected an option,
        # so ask the HVAC agent for trade-off explanation only.
        if rule_result.get("recommended_option"):
            result = HVACRecommendationAgent.explain(
                attrs=attributes,
                rule_result=rule_result,
            )
        else:
            # No-rule-match path: ask HVAC agent to perform full recommendation.
            no_match_context = dict(rule_result.get("reasoning_details") or {})
            if validation_context:
                no_match_context["validation_context"] = validation_context

            request_context = payload.get("request") or {}
            procurement_request_pk = (
                request_context.get("pk")
                or request_context.get("request_pk")
            )

            result = HVACRecommendationAgent.recommend(
                attrs=attributes,
                no_match_context=no_match_context,
                procurement_request_pk=procurement_request_pk,
            )

        # Ensure downstream graph always receives a dict payload.
        return result or {}
