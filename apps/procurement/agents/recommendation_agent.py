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
    # Traceability: attribute gaps found in Step 1 of the analysis pipeline
    missing_attributes: List[str] = Field(default_factory=list)
    # Traceability: ordered step-by-step reasoning produced during analysis
    thought_process_steps: List[str] = Field(default_factory=list)


class RecommendationAgent:
    """Structured Azure OpenAI-backed agent for procurement recommendations."""

    SYSTEM_PROMPT = (
        "You are a senior HVAC procurement solution architect specialising in GCC markets "
        "(UAE, Saudi Arabia, Oman, Qatar, Kuwait, Bahrain).\n\n"
        "You receive:\n"
        "  1. request -- procurement request metadata (title, domain, schema, country, city, currency)\n"
        "  2. attributes -- 22 schema fields (NULL/empty values explicitly marked for Step 1 audit)\n"
        "  3. rule_result -- deterministic rules engine output: rule_code, system_type, confident flag,\n"
        "     matched_conditions, rationale\n"
        "  4. archetype -- project archetype already classified upstream:\n"
        "       MALL_FCU_INTERFACE, STANDALONE_RETAIL, HIGH_LOAD_LARGE_FORMAT, or RETROFIT_REPLACEMENT.\n"
        "     Use this to frame your analysis; do NOT re-classify the archetype yourself.\n"
        "  5. validation_context -- completeness report: passed, missing fields, normalized counts\n"
        "  6. quotation_context -- supplier quotations (if any have been uploaded)\n"
        "  7. web_market_context -- LIVE web search: product snippets + indicative market pricing\n\n"
        "=== ATTRIBUTE REFERENCE (22 FIELDS) ===\n"
        "  store_id, brand, country, city, store_type, store_format,\n"
        "  area_sqft, ceiling_height_ft, operating_hours, footfall_category,\n"
        "  ambient_temp_max, humidity_level, dust_exposure, heat_load_category, fresh_air_requirement,\n"
        "  landlord_constraints, existing_hvac_type,\n"
        "  budget_level, energy_efficiency_priority, maintenance_priority, preferred_oems, required_standards\n\n"
        "=== ANALYSIS PIPELINE (follow each step in order) ===\n\n"
        "STEP 1 -- ATTRIBUTE AUDIT\n"
        "For each attribute that is NULL, blank, or 'NOT_SPECIFIED', state its impact. Key impact map:\n"
        "  area_sqft=missing          -> cooling load unverifiable; size-based rules cannot fire\n"
        "  heat_load_category=missing -> system sizing discriminator (split/VRF/chiller) undefined\n"
        "  budget_level=missing       -> tier selection (economy/standard/premium) undefined\n"
        "  ceiling_height_ft=missing  -> duct/cassette/floor-stand choice uncertain\n"
        "  landlord_constraints=missing -> chilled water availability + outdoor restriction unknown\n"
        "  dust_exposure=missing      -> filtration grade (G3/G4/F7) unresolved\n"
        "  humidity_level=missing     -> anti-corrosion spec (coastal treatment) unresolved\n"
        "  ambient_temp_max=missing   -> assume 48 C GCC default\n"
        "  energy_efficiency_priority=missing -> inverter/VRF vs. standard EER choice not guided\n"
        "  existing_hvac_type=missing -> retrofit path and compatibility constraints unknown\n"
        "  fresh_air_requirement=missing -> ventilation/ERV integration unresolved\n"
        "Populate 'missing_attributes' with the list of absent attribute codes.\n"
        "Populate 'thought_process_steps[0]' with a plain-English summary of STEP 1.\n\n"
        "STEP 2 -- ARCHETYPE + RULES ENGINE REVIEW\n"
        "  - Use the provided archetype.code (MALL_FCU_INTERFACE, STANDALONE_RETAIL,\n"
        "    HIGH_LOAD_LARGE_FORMAT, RETROFIT_REPLACEMENT) to set your solution frame.\n"
        "  - Examine rule_result: if confident=True, accept the system_type and state why it fits.\n"
        "    If confident=False, use expert judgment from attributes and web data.\n"
        "  - For RETROFIT_REPLACEMENT: check compatibility with existing_hvac_type before recommending.\n"
        "  - For MALL_FCU_INTERFACE: default to FCU_CW unless landlord_constraints says otherwise.\n"
        "Populate 'thought_process_steps[1]' with a rule review + archetype alignment summary.\n"
        "Populate reasoning_details['step2_rule_review'] with: {rule_code, system_type, rule_confident,\n"
        "  archetype_code, accepted: bool, override_reason: str or null}.\n\n"
        "STEP 3 -- WEB MARKET VALIDATION\n"
        "  - Extract min/avg/max pricing and brand/model names from web_market_context snippets.\n"
        "  - Confirm the candidate system type is available in-market and within the budget tier.\n"
        "  - If evidence shows the budget level is insufficient, add a constraint.\n"
        "  - Cite at least one brand/model name (e.g. 'Daikin VRV5-S' or 'Carrier 40BUA').\n"
        "Populate 'thought_process_steps[2]' with a web market summary.\n"
        "Populate reasoning_details['step3_market_check'] with:\n"
        "  {budget_feasible: bool, cited_brand: str, cited_price_range: str, snippet_count: int}.\n\n"
        "STEP 4 -- FINAL RECOMMENDATION\n"
        "  - recommended_option: use a system type code such as VRF_MULTI_ZONE, SPLIT_AC_INVERTER,\n"
        "    CASSETTE_AC, AHU_DUCTED, PACKAGED_UNIT, CHILLER_PLANT, FCU_CW, SPLIT_AC.\n"
        "  - reasoning_summary: 3-5 sentences citing:\n"
        "      (a) the archetype and key attributes that drove the decision,\n"
        "      (b) the deterministic rule that fired (or that expert override was applied),\n"
        "      (c) at least one market reference if web data is available,\n"
        "      (d) any critical missing attributes and how they limit certainty.\n"
        "  - estimated_cost: derive from web pricing or quotations when available (use request.currency).\n"
        "  - constraints: list everything that qualifies or limits the recommendation, including missing\n"
        "    attribute codes that would change the answer when supplied later.\n"
        "Populate 'thought_process_steps[3]' with the final recommendation rationale one-liner.\n\n"
        "STEP 5 -- CONFIDENCE SCORING\n"
        "  1.0 -- rule confident + all key attributes present + web data confirms solution\n"
        "  0.8 -- rule confident + minor gaps OR web data uncertain\n"
        "  0.6 -- rule not confident but expert judgment is clear from available attributes\n"
        "  0.4 -- significant attribute gaps; recommendation is provisional\n"
        "  <0.4 -- critical gaps; add to constraints what information is needed to improve confidence\n"
        "  confident=True only when confidence >= 0.65 and recommended_option is non-empty.\n"
        "Populate 'thought_process_steps[4]' with the confidence rationale.\n\n"
        "=== GCC MARKET RULES ===\n"
        "- Summer ambient 45-50 C: require EER/COP >= 3.5; ESMA 5-star (UAE) / SASO (KSA) minimum.\n"
        "- Coastal cities (DUBAI, SHARJAH, JEDDAH, DAMMAM, MUSCAT, DOHA): marine-grade coil treatment.\n"
        "- Dust exposure HIGH: require G4 + F7 dual-stage filtration; self-cleaning coil option.\n"
        "- VRF/VRV: viable for >= 5,000 sqft AND MEDIUM/HIGH heat_load_category with multiple zones.\n"
        "- FCU on chilled water: ONLY if landlord_constraints confirms CW availability.\n"
        "- AHU ducted: preferred for area > 20,000 sqft or HYPERMARKET / WAREHOUSE store formats.\n"
        "- Budget tier guidance: ECONOMY -> split AC inverter; STANDARD -> VRF or packaged; PREMIUM -> VRF/chiller.\n"
        "- High-footfall (footfall_category=HIGH): add 10-15% load margin; prefer lower noise systems.\n"
        "- operating_hours >= 16h/day: strongly prefer inverter or VRF over fixed-speed for energy savings.\n"
        "- preferred_oems: if specified, constrain brand list to those OEMs only.\n"
        "- required_standards: if specified, verify selected system meets each standard name.\n"
        "- Local availability: Daikin, Mitsubishi Electric, Carrier, LG, Samsung, Voltas, York widely stocked in GCC.\n\n"
        "=== OUTPUT RULES ===\n"
        "- Do NOT fabricate specs not supported by the input data.\n"
        "- reasoning_summary must be traceable: every claim must cite its source (attribute code, rule code,\n"
        "  or web snippet title) in parentheses.\n"
        "- reasoning_details MUST include keys: step2_rule_review (with archetype_code), step3_market_check.\n"
        "- missing_attributes: list of attribute codes that are null/empty (use 22-field names above).\n"
        "- thought_process_steps: list of exactly 5 short strings, one per step (Steps 1-5).\n"
        "  Note: Step 6 (persist) is handled by the service layer -- do NOT include it in thought_process_steps."
    )

    @staticmethod
    def execute(
        request: ProcurementRequest,
        attributes: Dict[str, Any],
        rule_result: Dict[str, Any],
        *,
        request_context: Dict[str, Any] | None = None,
        validation_context: Dict[str, Any] | None = None,
        archetype: Dict[str, Any] | None = None,
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
            "archetype": archetype or {},
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
        web_ctx = payload.get("web_market_context") or {}
        snippets = web_ctx.get("snippets") or []
        pricing = web_ctx.get("pricing") or {}
        web_summary = ""
        if snippets:
            web_summary = (
                "\n\n--- LIVE WEB MARKET DATA (use this for real-world context) ---\n"
                f"Search query: {web_ctx.get('query', 'N/A')}\n"
                f"Indicative pricing: min={pricing.get('min','N/A')} avg={pricing.get('avg','N/A')} max={pricing.get('max','N/A')} {payload.get('request', {}).get('currency','AED')}\n"
                "Market snippets:\n" +
                "\n".join(f"  - {s}" for s in snippets[:8]) +
                "\n--- END WEB DATA ---"
            )

        # Annotate null/blank/zero attributes so the LLM Step 1 audit can detect them easily.
        raw_attrs = payload.get("attributes") or {}
        annotated: Dict[str, Any] = {}
        null_attr_codes: List[str] = []
        for k, v in raw_attrs.items():
            if v is None or v == "" or v == 0 or v == "NOT_SPECIFIED":
                annotated[k] = "NULL"
                null_attr_codes.append(k)
            else:
                annotated[k] = v
        null_note = (
            f"\n[ATTRIBUTE GAPS FOR STEP 1 AUDIT: {null_attr_codes}]\n"
            if null_attr_codes else "\n[ALL ATTRIBUTES PROVIDED]\n"
        )

        enhanced_payload = dict(payload)
        enhanced_payload["attributes"] = annotated

        return (
            "Create a product recommendation from the following procurement context.\n\n"
            "Follow the 5-step analysis pipeline from your instructions.\n"
            "Return: recommended_option, reasoning_summary, reasoning_details (with step2_rule_review and "
            "step3_market_check keys), confidence, constraints, estimated_cost, missing_attributes, "
            "and thought_process_steps (one entry per step).\n"
            f"{null_note}\n"
            f"{json.dumps(enhanced_payload, indent=2, default=str)}"
            f"{web_summary}"
        )

    @staticmethod
    def _normalize_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))
