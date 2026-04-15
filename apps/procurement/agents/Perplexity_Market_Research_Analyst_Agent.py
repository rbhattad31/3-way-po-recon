"""Perplexity Market Research Analyst Agent.

Searches the live web via the Perplexity API (sonar-pro model) to find real,
purchasable HVAC products with accurate specifications, pricing, and source links.

Entry point:
    agent = PerplexityMarketResearchAnalystAgent()
    result = agent.run(proc_request, generated_by=user_or_none)

Returns a dict with keys:
    system_code, system_name, rephrased_query, ai_summary, market_context,
    suggestions (list), perplexity_citations (list)

Raises:
    ValueError  -- PERPLEXITY_API_KEY not set, or Perplexity returned empty/bad content.
    requests.HTTPError -- non-2xx response from Perplexity API.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse as _urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System-code -> human-readable DB name mapping
# (same values as ExternalSourceRegistry.hvac_system_type)
# ---------------------------------------------------------------------------
_SYSTEM_CODE_TO_DB_NAME: dict[str, str] = {
    "SPLIT_AC":    "Split AC",
    "VRF":         "VRF System",
    "PACKAGED_DX": "Packaged Unit (Rooftop)",
    "CHILLER":     "Chilled Water System",
    "FCU":         "Chilled Water System",
    "AHU":         "Chilled Water System",
    "DUCTING":     "Ducting & Accessories",
}

_ICONS = {
    "MANUFACTURER":   "bi-building",
    "DISTRIBUTOR":    "bi-truck",
    "REGULATOR":      "bi-shield-check",
    "STANDARDS_BODY": "bi-patch-check",
    "OTHER":          "bi-link-45deg",
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = (
    "You are a senior HVAC procurement sourcing agent. "
    "A procurement team will use your output to directly visit each product link and "
    "purchase or enquire about the product. Every link you provide MUST be a real, "
    "working URL that leads to the exact product page -- not a homepage, not a category "
    "page, and definitely not an invented URL. "
    "Respond ONLY with a single valid JSON object and nothing else.\n\n"
    "PURCHASE INTENT RULE:\n"
    "Treat each suggestion as a purchase recommendation. The buyer will click "
    "citation_url and expect to land on the specific product detail or listing page "
    "where they can see specs, price, and place an order or enquiry. "
    "If you cannot find a direct product page URL in citations[], use the closest "
    "real URL from citations[] -- do NOT invent or guess a URL.\n\n"
    "GROUNDING RULE:\n"
    "Every suggestion must be a real product you found during this live web search. "
    "product_name, manufacturer, model_code, and price MUST come from actual pages "
    "Perplexity visited. Do NOT fabricate model codes, prices, or URLs.\n\n"
    "CITATION RULES (mandatory -- read carefully):\n"
    "1. After your search, Perplexity gives you a 'citations' array of every real URL "
    "it fetched. These are the ONLY URLs you are allowed to use.\n"
    "2. citation_url: pick the EXACT URL from citations[] that is the product's own "
    "detail or listing page (most specific page available for this product). "
    "Each suggestion MUST use a DIFFERENT citation_url -- two products cannot share "
    "the same link.\n"
    "3. citation_index: the 0-based position of citation_url inside citations[].\n"
    "4. price_source_url: EXACT URL from citations[] where the price figure was found. "
    "Often the same as citation_url. If price is on a different page, use that "
    "page's URL from citations[].\n"
    "5. price_citation_index: 0-based position of price_source_url in citations[].\n"
    "6. NEVER construct, guess, or modify any URL. Copy character-for-character from "
    "citations[]. A fabricated URL is worse than no URL -- it wastes the buyer's time.\n"
    "7. All suggestions must match the exact system type requested. "
    "Return 5 to 7 suggestions from DIFFERENT manufacturers."
)

_USER_PROMPT_TPL = """You are sourcing {system_name} HVAC products for a procurement team who will
CLICK EACH LINK to verify specs, pricing, and place an order. Every citation_url you
return must be a real, directly-accessible product page from Perplexity's citations[] array.

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

=== STRICT SOURCING RULES ===
System type required: {system_name}

For EACH of the 5-7 product suggestions you return:

RULE 1 -- REAL PRODUCT ONLY
  Search for actual {system_name} products on manufacturer websites, authorised distributor
  portals, or B2B marketplaces. product_name, manufacturer, and model_code must all come
  from a real page Perplexity fetched. Do NOT invent or extrapolate any product details.
  Return products from DIFFERENT manufacturers.

RULE 2 -- DIRECT BUY LINK (most important rule)
  citation_url MUST be the EXACT URL of THIS product's own detail or listing page, copied
  character-for-character from Perplexity's citations[] array.
  The buyer will click this link expecting to land on the product page and be able to
  purchase or enquire. If you cannot find a direct product page in citations[], use the
  most specific page available -- but NEVER construct or guess a URL that is not in citations[].
  Two suggestions must NEVER share the same citation_url.
  citation_index = 0-based position of that URL in citations[].

RULE 3 -- PRICE IS MANDATORY
  Read price from the product page. Use real AED figure if shown.
  If no price is visible, write a specific market estimate e.g. "~45,000 AED est."
  NEVER write 'Contact distributor for pricing' or leave blank.
  price_source_url = exact URL in citations[] where the price was found.
  price_citation_index = its index in citations[].

RULE 4 -- NO FABRICATED URLS
  If a product has no matching URL in citations[], do NOT invent one.
  Use the closest real citations[] URL and note the limitation in fit_rationale.

RULE 5 -- QUANTITY
  Return EXACTLY 5 to 7 suggestions. Never return fewer than 5.


#### Expecting Vendor/Seller/B2B Marketplace product pages with specs, price, and a purchase option. websites only one click to buy directly ####

Return a JSON object with this exact structure:
{{
  "rephrased_query": "<one sentence product search query, e.g. 'High efficiency VRF systems for 3000 sq ft fitness studio Abu Dhabi UAE max 45C ambient medium budget' -- do NOT start with 'Market intelligence'>",
  "ai_summary": "<2-3 sentence summary of what products are available and key buying considerations for this request>",
  "market_context": "<current availability, lead times, or pricing trends for {system_name} in this region>",
  "suggestions": [
    {{
      "rank": 1,
      "product_name": "<full product/series name as shown on the product page>",
      "manufacturer": "<brand name>",
      "model_code": "<specific model or series code from the product page>",
      "system_type": "{system_name}",
      "cooling_capacity": "<e.g. 8 TR - 12 TR>",
      "cop_eer": "<e.g. COP 3.8 / EER 13.0>",
      "price_range_aed": "<MANDATORY: price read from the product page, e.g. 42,000 - 58,000 AED or ~50,000 AED est.>",
      "market_availability": "<availability note for UAE / this region>",
      "key_benefits": ["benefit 1", "benefit 2", "benefit 3"],
      "limitations": ["limitation 1", "limitation 2"],
      "fit_score": 88,
      "fit_rationale": "<one sentence why this product fits or does not fit the specific request above>",
      "standards_compliance": ["ASHRAE 90.1", "ESMA UAE"],
      "citation_index": 0,
      "citation_url": "<EXACT URL copied from citations[] -- this is the direct product page the buyer will click to purchase>",
      "citation_source": "<site name where URL was found, e.g. daikin.com, alibaba.com>",
      "price_citation_index": 0,
      "price_source_url": "<EXACT URL from citations[] where the price figure was found; same as citation_url if price is on the product page>",
      "category": "<MANUFACTURER or DISTRIBUTOR>"
    }}
  ]
}}

IMPORTANT REMINDERS:
- citation_url must be a DIRECT PRODUCT PAGE URL from citations[] -- not a homepage,
  not a search page, not a made-up URL. The buyer will click it to buy the product.
- Each suggestion must have a UNIQUE citation_url.
- All {system_name} suggestions only -- no mixed types.
- Ranked by fit_score descending.
"""

_SEARCH_INSTRUCTIONS_TPL = (
    "\n\n=== SEARCH INSTRUCTIONS ===\n"
    "Search the web freely for real, currently available {system_name} products.\n"
    "Prioritise sources that show direct product pages a buyer can act on:\n"
    "  1. Official manufacturer product pages -- e.g. daikin.com/products/..., "
    "carrier.com/products/..., midea.com/products/...\n"
    "  2. Authorised distributor or dealer product listings\n"
    "  3. B2B marketplace product detail pages (Alibaba, IndiaMART, Tradeindia, etc.)\n"
    "  4. HVAC specification or price comparison sites with direct product entries\n"
    "Return 5 to 7 DIFFERENT products from DIFFERENT manufacturers.\n\n"
    "CRITICAL -- citation_url must be the DIRECT PRODUCT PAGE URL:\n"
    "  - The buyer will click this link to view specs and purchase or enquire.\n"
    "  - Copy the EXACT URL from Perplexity's citations[] array -- do NOT modify it.\n"
    "  - Use the most specific URL available (product detail page beats category page).\n"
    "  - Every suggestion must have a DIFFERENT citation_url.\n"
    "  - If a product has no dedicated URL in citations[], use the closest real one "
    "from citations[] and note it in fit_rationale. NEVER fabricate a URL.\n"
    "  - citation_index = 0-based index of citation_url in citations[]."
)

_SOURCE_CLASS_ORDER = {"OEM_OFFICIAL": 0, "AUTHORIZED_DISTRIBUTOR": 1, "OEM_REGIONAL": 2}


class PerplexityMarketResearchAnalystAgent:
    """Live-web HVAC product research agent powered by Perplexity API.

    Usage::

        agent = PerplexityMarketResearchAnalystAgent()
        result = agent.run(proc_request, generated_by=request.user)

    The agent:
    1. Builds a rich HVAC product search prompt from the ProcurementRequest.
    2. Calls the Perplexity sonar-pro model with free web search.
    3. Normalises the JSON response (citation URLs, fit scores, icon classes).
    4. Persists a MarketIntelligenceSuggestion DB record.
    5. Returns a result dict ready for the template/API.
    """

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, proc_request: Any, generated_by: Any = None) -> dict:
        """Execute Perplexity product research for a ProcurementRequest.

        Args:
            proc_request: ProcurementRequest model instance.
            generated_by: User instance or None (for background/Celery runs).

        Returns:
            dict: keys -- system_code, system_name, rephrased_query, ai_summary,
                  market_context, suggestions, perplexity_citations.

        Raises:
            ValueError: PERPLEXITY_API_KEY not set, or Perplexity returned bad content.
            requests.HTTPError: non-2xx HTTP response from Perplexity API.
            ImportError: 'requests' package not installed.
        """
        from django.conf import settings

        api_key = getattr(settings, "PERPLEXITY_API_KEY", "")
        if not api_key:
            raise ValueError(
                "PERPLEXITY_API_KEY is not configured. "
                "Add it to your .env file to enable product research."
            )
        model = getattr(settings, "PERPLEXITY_MODEL", "sonar-pro")

        # 1. Build context from the request
        attrs_block = self.get_attrs_block(proc_request)
        rec_block, system_code, system_name = self.get_rec_context(proc_request)
        db_system_name = _SYSTEM_CODE_TO_DB_NAME.get(system_code, system_name)
        prompt_system_name = db_system_name or system_name or "HVAC System"

        # 2. Load approved sources from registry (used as hints + badge set)
        approved_sources, domain_list = self._load_approved_sources(db_system_name)

        # 3. Build the full user prompt
        user_prompt = self._build_user_prompt(
            proc_request=proc_request,
            attrs_block=attrs_block,
            rec_block=rec_block,
            prompt_system_name=prompt_system_name,
            approved_sources=approved_sources,
        )

        # 4. Call Perplexity
        raw_response = self._call_perplexity(api_key, model, user_prompt)

        # 5. Parse JSON
        data = self._parse_json(raw_response["content"], model)
        perplexity_citations: list[str] = raw_response["citations"]

        # 6. Normalise suggestions
        suggestions = self._normalise_suggestions(
            suggestions=data.get("suggestions", []),
            perplexity_citations=perplexity_citations,
            approved_sources=approved_sources,
            domain_list=domain_list,
        )

        # 7. Persist to DB
        self._persist(
            proc_request=proc_request,
            generated_by=generated_by,
            data=data,
            system_code=system_code,
            system_name=system_name,
            suggestions=suggestions,
            perplexity_citations=perplexity_citations,
        )

        logger.info(
            "PerplexityMarketResearchAnalystAgent.run: "
            "pk=%s system=%s suggestions=%d citations=%d",
            proc_request.pk, prompt_system_name, len(suggestions), len(perplexity_citations),
        )

        return {
            "system_code": system_code,
            "system_name": system_name,
            "rephrased_query": data.get("rephrased_query", ""),
            "ai_summary": data.get("ai_summary", ""),
            "market_context": data.get("market_context", ""),
            "suggestions": suggestions,
            "perplexity_citations": perplexity_citations,
            "source_reference_label": "Perplexity Source References",
            "llm_model_used": raw_response.get("model") or model,
            "llm_usage": raw_response.get("usage") or {},
            "prompt_tokens": (raw_response.get("usage") or {}).get("prompt_tokens"),
            "completion_tokens": (raw_response.get("usage") or {}).get("completion_tokens"),
            "total_tokens": (raw_response.get("usage") or {}).get("total_tokens"),
        }

    # ------------------------------------------------------------------
    # Context builders (also used by MarketIntelligenceService wrapper)
    # ------------------------------------------------------------------

    @staticmethod
    def get_attrs_block(proc_request: Any) -> str:
        """Return formatted string of all ProcurementRequest attributes."""
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
    def get_rec_context(proc_request: Any) -> tuple[str, str, str]:
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
    # Internal steps
    # ------------------------------------------------------------------

    def _load_approved_sources(self, db_system_name: str) -> tuple[list, list]:
        """Load ExternalSourceRegistry entries as suggestion hints."""
        from apps.procurement.models import ExternalSourceRegistry

        sources = list(
            ExternalSourceRegistry.objects.filter(
                hvac_system_type__iexact=db_system_name,
                is_active=True,
                allowed_for_discovery=True,
            ).values("source_name", "domain", "source_url", "source_type")
        )
        if not sources:
            sources = list(
                ExternalSourceRegistry.objects.filter(
                    is_active=True,
                    allowed_for_discovery=True,
                ).values("source_name", "domain", "source_url", "source_type")
            )
        sources.sort(key=lambda s: _SOURCE_CLASS_ORDER.get(s["source_type"], 99))

        seen: set = set()
        domain_list: list = []
        for src in sources:
            if src["domain"] not in seen:
                seen.add(src["domain"])
                domain_list.append(src["domain"])

        return sources, domain_list

    def _build_user_prompt(
        self,
        proc_request: Any,
        attrs_block: str,
        rec_block: str,
        prompt_system_name: str,
        approved_sources: list,
    ) -> str:
        """Compose the full user prompt sent to Perplexity."""
        base = _USER_PROMPT_TPL.format(
            title=proc_request.title,
            description=proc_request.description or "(not provided)",
            country=proc_request.geography_country or "UAE",
            city=proc_request.geography_city or "",
            priority=proc_request.priority,
            currency=proc_request.currency or "AED",
            attrs_block=attrs_block,
            rec_block=rec_block,
            system_name=prompt_system_name,
        )

        # Add approved-source hints if available
        if approved_sources:
            lines = [
                f"  - {s['source_name']} ({s['domain']}): {s['source_url']}"
                for s in approved_sources
            ]
            hint_block = (
                "\n\n=== SUGGESTED SOURCES (PREFERENCE -- NOT A RESTRICTION) ===\n"
                "The following registered sources are good starting points for this "
                "system type. You may search them, but you are also free to search "
                "manufacturer websites, distributor portals, and any other credible "
                "commercial source to find the best real product listings.\n"
                + "\n".join(lines)
            )
            base += hint_block

        base += _SEARCH_INSTRUCTIONS_TPL.format(system_name=prompt_system_name)
        return base

    def _call_perplexity(self, api_key: str, model: str, user_prompt: str) -> dict:
        """POST to Perplexity API and return {content, citations}."""
        try:
            import requests as _requests
        except ImportError:
            raise ImportError("'requests' library is required for Perplexity API calls")

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
            # No search_domain_filter -- free web search
        }

        logger.debug(
            "PerplexityMarketResearchAnalystAgent._call_perplexity: "
            "model=%s prompt_len=%d",
            model, len(user_prompt),
        )

        import time as _time
        _last_exc = None
        for _attempt in range(2):
            try:
                resp = _requests.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=90,  # sonar-pro performs live web searches; allow 90s
                )
                resp.raise_for_status()
                break
            except (
                _requests.exceptions.Timeout,
                _requests.exceptions.ConnectionError,
            ) as exc:
                _last_exc = exc
                if _attempt == 0:
                    logger.warning(
                        "PerplexityMarketResearchAnalystAgent: attempt 1 failed "
                        "(%s: %s); retrying in 5s",
                        type(exc).__name__, exc,
                    )
                    _time.sleep(5)
                else:
                    logger.error(
                        "PerplexityMarketResearchAnalystAgent: attempt 2 also failed "
                        "(%s: %s); giving up",
                        type(exc).__name__, exc,
                    )
                    raise
        else:
            if _last_exc:
                raise _last_exc
        resp_data = resp.json()

        choices = resp_data.get("choices") or []
        if not choices:
            logger.error(
                "PerplexityMarketResearchAnalystAgent: no choices in response: %s",
                json.dumps(resp_data)[:2000],
            )
            raise ValueError(
                f"Perplexity returned no choices. Model: {model}. "
                f"Response keys: {list(resp_data.keys())}"
            )

        content = (choices[0].get("message") or {}).get("content") or ""
        if not content.strip():
            logger.error(
                "PerplexityMarketResearchAnalystAgent: empty content. Full response: %s",
                json.dumps(resp_data)[:2000],
            )
            raise ValueError(
                f"Perplexity returned empty content for model '{model}'. "
                f"finish_reason={choices[0].get('finish_reason')!r}"
            )

        citations: list[str] = resp_data.get("citations") or []
        logger.debug(
            "PerplexityMarketResearchAnalystAgent._call_perplexity: "
            "%d citations returned", len(citations),
        )
        usage = resp_data.get("usage") or {}
        return {
            "content": content,
            "citations": citations,
            "usage": usage,
            "model": model,
        }

    @staticmethod
    def _parse_json(raw_text: str, model: str) -> dict:
        """Extract and parse JSON from raw Perplexity response content.

        Handles three common non-clean responses:
          1. Complete ```json ... ``` fence.
          2. Dangling opening fence with no closing ``` (truncated response).
          3. Leading prose before the first { or [.
        """
        text = raw_text.strip()

        # 1. Complete ```...``` fence (normal case)
        fenced = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        else:
            # 2. Strip a dangling opening fence that has no closing counterpart
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE).strip()

        # 3. Jump to the first { or [ if there is still leading prose
        if text and text[0] not in "{[":
            start = min(
                (text.find(c) for c in "{[" if text.find(c) != -1),
                default=-1,
            )
            if start != -1:
                text = text[start:]

        # 4. Trim trailing content beyond the last closing brace / bracket
        if text:
            last = max(text.rfind("}"), text.rfind("]"))
            if last != -1:
                text = text[: last + 1]

        if not text:
            raise ValueError("Perplexity response content is empty after stripping fences.")

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.error(
                "PerplexityMarketResearchAnalystAgent._parse_json: "
                "JSON parse failed. raw_text[:500]=%r error=%s",
                raw_text[:500], exc,
            )
            raise ValueError(
                f"Perplexity response could not be parsed as JSON: {exc}. "
                f"First 200 chars: {raw_text[:200]!r}"
            ) from exc

    @staticmethod
    def _strip_www(val: str) -> str:
        """Return lowercase domain with leading 'www.' removed."""
        if val.startswith("http"):
            val = _urlparse(val).netloc
        return val.lower().lstrip("www.").lstrip(".")

    def _normalise_suggestions(
        self,
        suggestions: list,
        perplexity_citations: list[str],
        approved_sources: list,
        domain_list: list,
    ) -> list:
        """Normalise suggestion dicts: icons, scores, citation URLs, badge flags."""
        domain_to_source_url = {
            src["domain"]: (src["source_url"] or f"https://{src['domain']}")
            for src in approved_sources
        }
        default_brand_url = (
            domain_to_source_url.get(domain_list[0], f"https://{domain_list[0]}")
            if domain_list else ""
        )
        approved_domain_set = {self._strip_www(d) for d in domain_list}
        domain_to_source_name = {
            self._strip_www(src["domain"]): src["source_name"]
            for src in approved_sources
        }

        # Track used citation indices so each suggestion gets a DIFFERENT URL fallback
        _used_cit_indices: set = set()

        def _next_unused_citation(preferred_idx: int) -> str:
            if not perplexity_citations:
                return default_brand_url
            if (
                0 <= preferred_idx < len(perplexity_citations)
                and preferred_idx not in _used_cit_indices
            ):
                _used_cit_indices.add(preferred_idx)
                return perplexity_citations[preferred_idx]
            for i, url in enumerate(perplexity_citations):
                if i not in _used_cit_indices:
                    _used_cit_indices.add(i)
                    return url
            # All used -- recycle preferred (round-robin)
            return perplexity_citations[preferred_idx % len(perplexity_citations)]

        for s in suggestions:
            # Icon class from category
            cat = s.get("category", "MANUFACTURER").upper()
            s["icon_class"] = _ICONS.get(cat, "bi-building")

            # Clamp fit_score to 0-100
            try:
                s["fit_score"] = max(0, min(100, int(s.get("fit_score", 0))))
            except (TypeError, ValueError):
                s["fit_score"] = 0

            # citation_url -- prefer LLM-provided URL, fall back to citation index
            llm_cit_url = s.get("citation_url", "")
            cit_idx = s.get("citation_index")
            try:
                cit_idx = int(cit_idx)
            except (TypeError, ValueError):
                cit_idx = -1

            if isinstance(llm_cit_url, str) and llm_cit_url.startswith("http"):
                s["citation_url"] = llm_cit_url
                if 0 <= cit_idx < len(perplexity_citations):
                    _used_cit_indices.add(cit_idx)
            else:
                s["citation_url"] = _next_unused_citation(cit_idx)

            # price_source_url
            llm_price_url = s.get("price_source_url", "")
            price_idx = s.get("price_citation_index")
            try:
                price_idx = int(price_idx)
            except (TypeError, ValueError):
                price_idx = -1

            if isinstance(llm_price_url, str) and llm_price_url.startswith("http"):
                s["price_source_url"] = llm_price_url
            elif 0 <= price_idx < len(perplexity_citations):
                s["price_source_url"] = perplexity_citations[price_idx]
            else:
                s["price_source_url"] = ""

            # is_approved_source badge flag
            citation_domain = self._strip_www(s.get("citation_url", ""))
            s["is_approved_source"] = bool(approved_domain_set) and citation_domain in approved_domain_set

            # citation_source label from DB registry if available
            db_label = domain_to_source_name.get(citation_domain, "")
            if db_label:
                s["citation_source"] = db_label

        return suggestions

    @staticmethod
    def _persist(
        proc_request: Any,
        generated_by: Any,
        data: dict,
        system_code: str,
        system_name: str,
        suggestions: list,
        perplexity_citations: list,
    ) -> None:
        """Save MarketIntelligenceSuggestion to DB (fail-silent on error)."""
        from apps.procurement.models import MarketIntelligenceSuggestion
        from apps.agents.services.base_agent import BaseAgent
        try:
            # Phase 1C: sanitize LLM-generated text before DB persistence (ASCII-safe)
            safe_ai_summary = BaseAgent._sanitise_text(data.get("ai_summary", ""))
            safe_market_context = BaseAgent._sanitise_text(data.get("market_context", ""))
            MarketIntelligenceSuggestion.objects.create(
                request=proc_request,
                generated_by=generated_by,
                rephrased_query=data.get("rephrased_query", ""),
                ai_summary=safe_ai_summary,
                market_context=safe_market_context,
                system_code=system_code,
                system_name=system_name,
                suggestions_json=suggestions,
                suggestion_count=len(suggestions),
                perplexity_citations_json=perplexity_citations,
                source_reference_label="Perplexity Source References",
            )
        except Exception as exc:
            logger.warning(
                "PerplexityMarketResearchAnalystAgent._persist: "
                "DB save failed for request pk=%s: %s",
                proc_request.pk, exc,
            )
