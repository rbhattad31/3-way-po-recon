"""RecommendationAgent — AI-powered product/solution recommendation."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from django.conf import settings
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from apps.procurement.models import ProcurementRequest

logger = logging.getLogger(__name__)


class RecommendationResponse(BaseModel):
    """Structured recommendation response returned by the LLM."""

    model_config = ConfigDict(extra="ignore")

    recommended_option: str = ""
    reasoning_summary: str = ""
    reasoning_details: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    constraints: List[str] = Field(default_factory=list)
    confident: bool = False
    estimated_cost: float | None = None
    recommended_vendor: str | None = None
    quotation_reference: str | None = None


class RecommendationAgent:
    """Structured Azure OpenAI-backed agent for procurement recommendations."""

    SYSTEM_PROMPT = (
        "You are a senior procurement solution architect. "
        "Use the procurement request, validation findings, and quotation evidence to produce "
        "the best-fit product or solution recommendation.\n\n"
        "Rules:\n"
        "- Prefer options that directly satisfy the validated requirements.\n"
        "- If quotation data is available, recommend the best matching quoted option and cite the vendor/model when possible.\n"
        "- If extracted quotation data is present but not yet user-confirmed, you may still use it as evidence and mention that it came from extracted quotation context.\n"
        "- Do not fabricate specifications that are not supported by the input.\n"
        "- If information is incomplete, still give the best practical recommendation and clearly mention the gaps.\n"
        "- Confidence must be between 0.0 and 1.0.\n"
        "- Keep the summary concise and decision-oriented."
    )

    @staticmethod
    def execute(
        request: ProcurementRequest,
        attributes: Dict[str, Any],
        rule_result: Dict[str, Any],
        *,
        request_context: Dict[str, Any] | None = None,
        validation_context: Dict[str, Any] | None = None,
        quotation_context: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """Run AI recommendation and return a structured dict."""
        payload = {
            "request": {
                "request_id": str(request.request_id),
                "title": request.title,
                "description": request.description,
                "domain_code": request.domain_code,
                "schema_code": request.schema_code,
                "request_type": request.request_type,
                "geography_country": request.geography_country,
                "geography_city": request.geography_city,
                "currency": request.currency,
            },
            "attributes": attributes,
            "rule_result": rule_result,
            "request_context": request_context or {},
            "validation_context": validation_context or {},
            "quotation_context": quotation_context or [],
        }
        return RecommendationAgent.execute_from_payload(payload)

    @staticmethod
    def execute_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Invoke the LLM using a pre-built payload."""
        try:
            llm = RecommendationAgent._build_llm().with_structured_output(
                RecommendationResponse,
                method="function_calling",
            )
            response = llm.invoke([
                ("system", RecommendationAgent.SYSTEM_PROMPT),
                ("human", RecommendationAgent._build_user_message(payload)),
            ])

            if isinstance(response, RecommendationResponse):
                result = response.model_dump()
            elif isinstance(response, dict):
                result = RecommendationResponse(**response).model_dump()
            else:
                result = RecommendationResponse().model_dump()

            result["confidence"] = RecommendationAgent._normalize_confidence(result.get("confidence"))
            result.setdefault("constraints", [])
            result.setdefault("reasoning_details", {})
            result["reasoning_details"].setdefault("source", getattr(settings, "LLM_PROVIDER", "azure_openai"))
            result["reasoning_details"].setdefault("workflow", "langgraph_recommendation")
            result["reasoning_details"].setdefault(
                "evidence_summary",
                {
                    "attribute_count": len(payload.get("attributes") or {}),
                    "quotation_count": len(payload.get("quotation_context") or []),
                    "has_validation": bool(payload.get("validation_context")),
                },
            )
            if result.get("recommended_option"):
                result["confident"] = bool(result.get("confident") or result["confidence"] >= 0.55)
            return result
        except Exception as exc:
            logger.exception("RecommendationAgent LLM call failed")
            return {
                "recommended_option": "",
                "reasoning_summary": f"AI analysis failed: {exc}",
                "reasoning_details": {
                    "source": getattr(settings, "LLM_PROVIDER", "azure_openai"),
                    "workflow": "langgraph_recommendation",
                    "error": str(exc),
                },
                "confident": False,
                "confidence": 0.0,
                "constraints": [],
            }

    @staticmethod
    def _build_llm():
        provider = getattr(settings, "LLM_PROVIDER", "azure_openai")
        temperature = getattr(settings, "LLM_TEMPERATURE", 0.1)
        max_tokens = getattr(settings, "LLM_MAX_TOKENS", 4096)

        if provider == "azure_openai":
            endpoint = getattr(settings, "AZURE_OPENAI_ENDPOINT", "")
            api_key = getattr(settings, "AZURE_OPENAI_API_KEY", "")
            deployment = getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "") or getattr(settings, "LLM_MODEL_NAME", "gpt-4o")
            if not endpoint or not api_key or not deployment:
                raise ValueError(
                    "Azure OpenAI is not fully configured (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT)."
                )
            return AzureChatOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=getattr(settings, "AZURE_OPENAI_API_VERSION", "2024-02-01"),
                azure_deployment=deployment,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        api_key = getattr(settings, "OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured.")
        return ChatOpenAI(
            api_key=api_key,
            model=getattr(settings, "OPENAI_MODEL_NAME", getattr(settings, "LLM_MODEL_NAME", "gpt-4o")),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @staticmethod
    def _build_user_message(payload: Dict[str, Any]) -> str:
        return (
            "Create a product recommendation from the following procurement context.\n\n"
            "Return the best fit recommendation, key reasoning, confidence, constraints, and estimated cost if pricing evidence exists.\n\n"
            f"{json.dumps(payload, indent=2, default=str)}"
        )

    @staticmethod
    def _normalize_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))
