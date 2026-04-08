"""Market Intelligence generation service.

Extracted from api_external_suggestions view so the same logic can be called from:
  - the AJAX view (on-demand Refresh button)
  - the Celery background task (auto-triggered on new request creation)
  - the seed_market_intelligence management command (back-fill existing requests)
"""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a senior HVAC market intelligence analyst specializing in commercial "
    "and retail HVAC systems for the GCC/Middle East region. "
    "You have deep knowledge of manufacturer product lines (Daikin, Carrier, Trane, "
    "York, Mitsubishi Electric, LG, Samsung, Gree, Midea, Voltas), pricing in AED, "
    "regional standards (ESMA, ASHRAE, Cooling India), and distributor availability. "
    "Respond ONLY with a single valid JSON object and nothing else."
)

_ICONS = {
    "MANUFACTURER":   "bi-building",
    "DISTRIBUTOR":    "bi-truck",
    "REGULATOR":      "bi-shield-check",
    "STANDARDS_BODY": "bi-patch-check",
    "OTHER":          "bi-link-45deg",
}

_USER_PROMPT_TPL = """Analyze the following HVAC procurement request and generate a comprehensive
market intelligence report with at least 5 product suggestions.

=== PROCUREMENT REQUEST ===
Title: {title}
Description: {description}
Country: {country}
City: {city}
Priority: {priority}
Currency: {currency}

=== REQUEST ATTRIBUTES ===
{attrs_block}

=== INTERNAL AI RECOMMENDATION ===
{rec_block}

Return a JSON object with this exact structure:
{{
  "rephrased_query": "<one sentence professional market query summarising this need>",
  "ai_summary": "<2-3 sentence executive summary of market context and key considerations>",
  "market_context": "<brief note on current market availability, lead times, or pricing trends in this region>",
  "suggestions": [
    {{
      "rank": 1,
      "product_name": "<full product/series name>",
      "manufacturer": "<brand name>",
      "model_code": "<specific model or series code>",
      "system_type": "<e.g. VRF, Chilled Water AHU, Split DX, Cassette, Rooftop>",
      "cooling_capacity": "<e.g. 8 TR - 12 TR>",
      "cop_eer": "<e.g. COP 3.8 / EER 13.0>",
      "price_range_aed": "<e.g. 45,000 - 70,000 AED supply & install>",
      "market_availability": "<availability note for this region>",
      "key_benefits": ["benefit 1", "benefit 2", "benefit 3"],
      "limitations": ["limitation 1", "limitation 2"],
      "fit_score": 88,
      "fit_rationale": "<one sentence why this fits or does not fit this request>",
      "standards_compliance": ["ASHRAE 90.1", "ESMA UAE"],
      "citation_url": "<manufacturer product page URL>",
      "citation_source": "<source name e.g. Daikin Middle East>",
      "category": "<MANUFACTURER or DISTRIBUTOR>"
    }}
  ]
}}
Provide 5 to 7 suggestions ranked by fit_score descending. Use only real product lines.
"""


class MarketIntelligenceService:
    """Service for generating and persisting AI market intelligence suggestions.

    All methods are classmethods so the service is stateless and can be used
    from views, Celery tasks, and management commands without instantiation.
    """

    # ------------------------------------------------------------------
    # Data gathering helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_attrs_block(proc_request) -> str:
        """Return a formatted string of all request attributes."""
        from apps.procurement.models import ProcurementRequestAttribute

        attributes = list(
            ProcurementRequestAttribute.objects
            .filter(request=proc_request)
            .values("attribute_code", "attribute_label", "value_text", "value_number")
        )
        lines = []
        for a in attributes:
            val = a["value_text"] or (str(a["value_number"]) if a["value_number"] else "")
            if val:
                lines.append(f"  - {a['attribute_label']}: {val}")
        return "\n".join(lines) if lines else "  (no attributes recorded)"

    @staticmethod
    def get_rec_context(proc_request) -> tuple[str, str, str]:
        """Return (rec_block, system_code, system_name) from the latest recommendation."""
        from apps.procurement.models import RecommendationResult

        recommendation = (
            RecommendationResult.objects
            .filter(run__request=proc_request)
            .order_by("-created_at")
            .first()
        )
        if not recommendation:
            return "(none yet)", "", ""

        payload = recommendation.output_payload_json or {}
        system_code = payload.get("system_type_code", "")
        details = recommendation.reasoning_details_json or payload.get("reasoning_details", {})
        system_name = (
            details.get("system_type", {}).get("name", "")
            if isinstance(details, dict) else ""
        ) or system_code.replace("_", " ").title()
        rec_block = (
            f"Recommended System: {system_name} ({system_code})\n"
            f"Confidence: {int((recommendation.confidence_score or 0) * 100)}%\n"
            f"Compliance: {recommendation.compliance_status or 'N/A'}"
        )
        return rec_block, system_code, system_name

    # ------------------------------------------------------------------
    # Core generation method
    # ------------------------------------------------------------------

    @classmethod
    def generate(cls, proc_request, generated_by=None) -> dict:
        """Call the LLM, normalise results, save to DB, and return the result dict.

        Args:
            proc_request: ProcurementRequest instance.
            generated_by:  User instance or None (for background/seed generation).

        Returns:
            dict with keys: system_code, system_name, rephrased_query, ai_summary,
                            market_context, suggestions (list).

        Raises:
            Exception: propagated from LLM call on hard failure.
                       Callers should catch and handle gracefully.
        """
        from apps.agents.services.llm_client import LLMClient, LLMMessage

        attrs_block = cls.get_attrs_block(proc_request)
        rec_block, system_code, system_name = cls.get_rec_context(proc_request)

        user_prompt = _USER_PROMPT_TPL.format(
            title=proc_request.title,
            description=proc_request.description or "(not provided)",
            country=proc_request.geography_country or "UAE",
            city=proc_request.geography_city or "",
            priority=proc_request.priority,
            currency=proc_request.currency or "AED",
            attrs_block=attrs_block,
            rec_block=rec_block,
        )

        llm = LLMClient(temperature=0.2, max_tokens=3000)
        messages = [
            LLMMessage(role="system", content=_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ]
        resp = llm.chat(messages, response_format={"type": "json_object"})
        data = json.loads((resp.content or "").strip())

        # Normalise suggestions
        suggestions = data.get("suggestions", [])
        for s in suggestions:
            cat = s.get("category", "MANUFACTURER").upper()
            s["icon_class"] = _ICONS.get(cat, "bi-building")
            try:
                s["fit_score"] = max(0, min(100, int(s.get("fit_score", 0))))
            except (TypeError, ValueError):
                s["fit_score"] = 0

        # Persist to DB
        from apps.procurement.models import MarketIntelligenceSuggestion
        try:
            MarketIntelligenceSuggestion.objects.create(
                request=proc_request,
                generated_by=generated_by,
                rephrased_query=data.get("rephrased_query", ""),
                ai_summary=data.get("ai_summary", ""),
                market_context=data.get("market_context", ""),
                system_code=system_code,
                system_name=system_name,
                suggestions_json=suggestions,
                suggestion_count=len(suggestions),
            )
        except Exception as save_exc:
            logger.warning(
                "MarketIntelligenceService.generate: DB save failed for request pk=%s: %s",
                proc_request.pk, save_exc,
            )

        return {
            "system_code": system_code,
            "system_name": system_name,
            "rephrased_query": data.get("rephrased_query", ""),
            "ai_summary": data.get("ai_summary", ""),
            "market_context": data.get("market_context", ""),
            "suggestions": suggestions,
        }

    @classmethod
    def generate_with_perplexity(cls, proc_request, generated_by=None) -> dict:
        """Call Perplexity sonar-pro live web search, normalise results, save to DB, return result dict.

        Args:
            proc_request: ProcurementRequest instance.
            generated_by:  User instance or None (for background/seed generation).

        Returns:
            dict with keys: system_code, system_name, rephrased_query, ai_summary,
                            market_context, suggestions (list).

        Raises:
            Exception: propagated from Perplexity API call on hard failure.
        """
        try:
            import requests
        except ImportError:
            raise ImportError("'requests' library required for Perplexity API calls")

        from django.conf import settings

        api_key = getattr(settings, "PERPLEXITY_API_KEY", "")
        model = getattr(settings, "PERPLEXITY_MODEL", "sonar-pro")

        if not api_key:
            raise ValueError("PERPLEXITY_API_KEY not configured in settings")

        attrs_block = cls.get_attrs_block(proc_request)
        rec_block, system_code, system_name = cls.get_rec_context(proc_request)

        user_prompt = _USER_PROMPT_TPL.format(
            title=proc_request.title,
            description=proc_request.description or "(not provided)",
            country=proc_request.geography_country or "UAE",
            city=proc_request.geography_city or "",
            priority=proc_request.priority,
            currency=proc_request.currency or "AED",
            attrs_block=attrs_block,
            rec_block=rec_block,
        )

        # Call Perplexity API
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 4096,
            "return_images": False,
            "return_related_questions": False,
            "search_recency_filter": "month",
        }

        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        resp_data = resp.json()
        raw_text = resp_data["choices"][0]["message"]["content"]

        # Parse JSON from response
        import re
        text = raw_text.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        data = json.loads(text)

        # Normalise suggestions
        suggestions = data.get("suggestions", [])
        for s in suggestions:
            cat = s.get("category", "MANUFACTURER").upper()
            s["icon_class"] = _ICONS.get(cat, "bi-building")
            try:
                s["fit_score"] = max(0, min(100, int(s.get("fit_score", 0))))
            except (TypeError, ValueError):
                s["fit_score"] = 0

        # Persist to DB
        from apps.procurement.models import MarketIntelligenceSuggestion
        try:
            MarketIntelligenceSuggestion.objects.create(
                request=proc_request,
                generated_by=generated_by,
                rephrased_query=data.get("rephrased_query", ""),
                ai_summary=data.get("ai_summary", ""),
                market_context=data.get("market_context", ""),
                system_code=system_code,
                system_name=system_name,
                suggestions_json=suggestions,
                suggestion_count=len(suggestions),
            )
        except Exception as save_exc:
            logger.warning(
                "MarketIntelligenceService.generate_with_perplexity: DB save failed for request pk=%s: %s",
                proc_request.pk, save_exc,
            )

        return {
            "system_code": system_code,
            "system_name": system_name,
            "rephrased_query": data.get("rephrased_query", ""),
            "ai_summary": data.get("ai_summary", ""),
            "market_context": data.get("market_context", ""),
            "suggestions": suggestions,
        }

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
