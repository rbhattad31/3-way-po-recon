"""HVACRecommendationAgent -- AI reasoning for HVAC tradeoffs and full system recommendation.

Two public entry points:
  - recommend(): called when NO DB rule matched the request attributes.
                 Performs full AI-driven system selection using project attributes,
                 available system types from DB, similar store profiles, and
                 market intelligence data.
  - explain():   called when a DB rule DID match (existing behaviour).
                 Provides procurement-facing tradeoff reasoning for the deterministic result.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from apps.agents.services.llm_client import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known HVAC system type labels (DB-independent fallback reference)
# ---------------------------------------------------------------------------
_KNOWN_SYSTEM_TYPES = {
    "VRF": "Variable Refrigerant Flow (VRF) System",
    "SPLIT_AC": "Split Air Conditioning",
    "PACKAGED_DX": "Packaged DX Unit",
    "CHILLER": "Chilled Water System / Chiller Plant",
    "FCU": "Fan Coil Unit (Chilled Water)",
    "CASSETTE": "Cassette Split Unit",
    "DUCTED_SPLIT": "Ducted Split System",
    "ROOFTOP_UNIT": "Rooftop Package Unit",
}


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract and parse the first JSON object from an LLM response."""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    if not text.startswith("{"):
        obj = re.search(r"\{.*\}", text, re.DOTALL)
        if obj:
            text = obj.group(0)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


class HVACRecommendationAgent:
    """AI-powered HVAC recommendation engine.

    Entry points
    ------------
    recommend(attrs, no_match_context, procurement_request_pk)
        Full AI system selection.  Called when the deterministic rules engine
        produced confident=False (no rule matched the given attributes).

    explain(attrs, rule_result)
        Lightweight tradeoff commentary.  Called after a DB rule matched to
        produce human-readable procurement-facing reasoning.
    """

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    # Full recommendation prompt (no-rule-match path)
    RECOMMEND_SYSTEM_PROMPT = (
        "You are a senior HVAC systems engineer and procurement advisor with 20+ years of "
        "experience in GCC (UAE, KSA, Qatar, Kuwait, Bahrain, Oman) retail and commercial "
        "HVAC design and specification.\n\n"
        "The deterministic rules engine found NO matching rule for the provided parameters. "
        "Your task is to independently analyse the project attributes and recommend the most "
        "appropriate HVAC system type.\n\n"
        "You will receive:\n"
        "  - project_attributes: key input parameters for this request\n"
        "  - no_match_context: why the rules engine did not match (parameter summary)\n"
        "  - available_systems: HVAC system types configured in the database\n"
        "  - similar_stores: profiles of comparable stores (may be empty)\n"
        "  - market_intelligence: any available AI market intel for this region (may be empty)\n\n"
        "Instructions:\n"
        "1. Select the single best-fit HVAC system type from available_systems.\n"
        "2. Provide an honest confidence score (0.0-1.0). Be conservative -- if key data is "
        "missing, reduce confidence and set human_validation_required=true.\n"
        "3. List 2-4 concrete decision drivers that led to your selection.\n"
        "4. List 1-3 procurement constraints relevant to this project.\n"
        "5. Suggest one alternate option.\n"
        "6. Estimate indicative cooling load in TR based on area (use 130 W/m2 as a base "
        "rule of thumb for GCC retail; adjust for heat load category HIGH +20%, LOW -15%).\n\n"
        "Return ONLY valid JSON. No markdown. No extra keys.\n"
        "{\n"
        '  "recommended_system_type": "SYSTEM_CODE",\n'
        '  "recommended_option": "Full system name and one-line description",\n'
        '  "reasoning_summary": "Concise expert rationale (2-4 sentences)",\n'
        '  "confidence": 0.75,\n'
        '  "decision_drivers": ["...", "..."],\n'
        '  "tradeoffs": ["...", "..."],\n'
        '  "constraints": [{"type": "TYPE_CODE", "detail": "..."}, ...],\n'
        '  "alternate_option": {"system_type": "CODE", "reason": "..."},\n'
        '  "indicative_capacity_tr": 12.5,\n'
        '  "human_validation_required": true,\n'
        '  "market_notes": "relevant product/market context",\n'
        '  "compliance_notes": "applicable GCC standards"\n'
        "}"
    )

    # Tradeoff explanation prompt (rule-matched path)
    EXPLAIN_SYSTEM_PROMPT = (
        "You are an HVAC solution advisor for procurement pre-design. "
        "You receive a deterministic recommendation result and the original request attributes. "
        "Return JSON with concise reasoning, tradeoffs, decision_drivers, and alternate_option.\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "reasoning_summary": "...",\n'
        '  "tradeoffs": ["..."],\n'
        '  "decision_drivers": ["..."],\n'
        '  "alternate_option": {"system_type": "...", "reason": "..."}\n'
        "}\n"
        "No markdown. No extra keys."
    )

    # ------------------------------------------------------------------
    # Public: full recommendation (no-rule-match path)
    # ------------------------------------------------------------------

    @staticmethod
    def recommend(
        *,
        attrs: Dict[str, Any],
        no_match_context: Dict[str, Any],
        procurement_request_pk: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run a full AI-driven HVAC system recommendation.

        Called when HVACRulesEngine.evaluate() returns confident=False because
        no DB rule matched the given project attributes.

        Parameters
        ----------
        attrs:
            Normalised attribute dict from AttributeService.get_attributes_dict().
        no_match_context:
            The raw reasoning_details dict from HVACRulesEngine.evaluate(), which
            records which rules were evaluated and why none matched.
        procurement_request_pk:
            Optional PK of the ProcurementRequest so market intelligence
            suggestions already generated for this request can be included.

        Returns
        -------
        dict
            A recommendation dict with the same structure as a successful
            HVACRulesEngine.evaluate() result, plus source='hvac_agent'.
            On failure returns confident=False so the upstream orchestrator
            can apply its own AI fallback.
        """
        db_ctx = HVACRecommendationAgent._load_db_context(
            attrs=attrs,
            procurement_request_pk=procurement_request_pk,
        )

        payload: Dict[str, Any] = {
            "project_attributes": attrs,
            "no_match_context": no_match_context,
            "available_systems": db_ctx["available_systems"],
            "similar_stores": db_ctx["similar_stores"],
            "market_intelligence": db_ctx["market_intelligence"],
            "instruction": (
                "Select the best HVAC system for this project. "
                "Return structured JSON only."
            ),
        }

        llm = LLMClient()
        try:
            response = llm.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content=HVACRecommendationAgent.RECOMMEND_SYSTEM_PROMPT,
                    ),
                    LLMMessage(
                        role="user",
                        content=json.dumps(payload, default=str),
                    ),
                ],
            )
            parsed = _extract_json(response.content or "")
            if not parsed:
                raise ValueError("LLM returned non-JSON response")

            # Validate system type against known types
            system_code = str(parsed.get("recommended_system_type") or "").upper()
            system_label = (
                _KNOWN_SYSTEM_TYPES.get(system_code)
                or db_ctx["system_code_to_label"].get(system_code)
                or system_code
            )

            recommended_option = str(
                parsed.get("recommended_option") or system_label or "AI-recommended HVAC system"
            )

            raw_confidence = float(parsed.get("confidence") or 0.65)
            confidence = max(0.0, min(0.95, raw_confidence))

            # If confidence is too low, flag for human review
            human_val = bool(parsed.get("human_validation_required", True))
            if confidence < 0.65:
                human_val = True

            constraints_raw = parsed.get("constraints") or []
            constraints: List[Dict[str, str]] = []
            for c in constraints_raw:
                if isinstance(c, dict):
                    constraints.append({
                        "type": str(c.get("type") or "AGENT_CONSTRAINT"),
                        "detail": str(c.get("detail") or ""),
                    })
                elif isinstance(c, str):
                    constraints.append({"type": "AGENT_CONSTRAINT", "detail": c})

            # Alternate option normalisation
            alt_raw = parsed.get("alternate_option") or {}
            if isinstance(alt_raw, dict):
                alternate_option = alt_raw.get("system_type") or None
                alt_reason = alt_raw.get("reason") or ""
            else:
                alternate_option = str(alt_raw) if alt_raw else None
                alt_reason = ""

            # Indicative capacity
            cap_tr = parsed.get("indicative_capacity_tr")
            try:
                cap_tr = float(cap_tr) if cap_tr is not None else None
            except (TypeError, ValueError):
                cap_tr = None

            cap_guidance: Optional[str] = None
            if cap_tr is not None:
                if cap_tr < 5:
                    cap_guidance = f"{cap_tr:.1f} TR (small -- standard split configuration)"
                elif cap_tr < 20:
                    cap_guidance = f"{cap_tr:.1f} TR (medium -- engineering review recommended)"
                elif cap_tr < 100:
                    cap_guidance = f"{cap_tr:.1f} TR (large -- HVAC engineer sizing analysis mandatory)"
                else:
                    cap_guidance = (
                        f"{cap_tr:.0f} TR (major plant -- full load analysis and engineer sign-off required)"
                    )

            decision_drivers = [
                str(d) for d in (parsed.get("decision_drivers") or []) if d
            ]
            tradeoffs = [str(t) for t in (parsed.get("tradeoffs") or []) if t]

            reasoning_summary = str(
                parsed.get("reasoning_summary")
                or f"AI-recommended {system_code} based on project profile analysis."
            )

            logger.info(
                "HVACRecommendationAgent.recommend: selected %s (confidence=%.2f, "
                "human_validation=%s) for attrs: store_type=%s area=%s ambient=%s",
                system_code,
                confidence,
                human_val,
                attrs.get("store_type"),
                attrs.get("area_sqft"),
                attrs.get("ambient_temp_max"),
            )

            return {
                "recommended_option": recommended_option,
                "system_type_code": system_code,
                "recommended_system_type": system_code,
                "reasoning_summary": reasoning_summary,
                "confident": confidence >= 0.60,
                "confidence": confidence,
                "confidence_score_100": round(confidence * 100),
                "constraints": constraints,
                "alternate_option": (
                    f"{alternate_option} -- {alt_reason}" if alternate_option else None
                ),
                "indicative_capacity_guidance": cap_guidance,
                "decision_drivers": decision_drivers,
                "tradeoffs": tradeoffs,
                "human_validation_required": human_val,
                "market_notes": str(parsed.get("market_notes") or ""),
                "compliance_notes": str(parsed.get("compliance_notes") or ""),
                "reasoning_details": {
                    "source": "hvac_agent",
                    "agent": "HVACRecommendationAgent.recommend",
                    "trigger": "no_rule_match",
                    "rules_evaluated": no_match_context.get("rules_evaluated", 0),
                    "rules_loaded": no_match_context.get("rules_loaded", 0),
                    "db_systems_available": len(db_ctx["available_systems"]),
                    "similar_stores_found": len(db_ctx["similar_stores"]),
                    "market_intel_available": bool(db_ctx["market_intelligence"]),
                    "inputs": no_match_context.get("inputs", {}),
                },
                "source_classes_used": ["HVACRecommendationAgent"],
                "llm_model_used": llm.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "total_tokens": response.total_tokens,
                "llm_usage": {
                    "model": llm.model,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                },
            }

        except Exception as exc:
            logger.warning(
                "HVACRecommendationAgent.recommend failed: %s", exc, exc_info=True
            )
            return {
                "recommended_option": "",
                "system_type_code": "",
                "reasoning_summary": (
                    "AI recommendation could not be generated -- please review manually. "
                    f"Error: {exc}"
                ),
                "confident": False,
                "confidence": 0.0,
                "constraints": [],
                "reasoning_details": {
                    "source": "hvac_agent_error",
                    "error": str(exc),
                    "trigger": "no_rule_match",
                },
            }

    # ------------------------------------------------------------------
    # Private: DB context loader
    # ------------------------------------------------------------------

    @staticmethod
    def _load_db_context(
        attrs: Dict[str, Any],
        procurement_request_pk: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Load DB context for the recommend() prompt.

        Loads:
          - available_systems: all active HVACServiceScope rows (system catalogue)
          - system_code_to_label: mapping of system codes to human names
          - similar_stores: up to 5 HVACStoreProfile rows with same store_type/country
          - market_intelligence: latest MarketIntelligenceSuggestion for the request
        """
        context: Dict[str, Any] = {
            "available_systems": [],
            "system_code_to_label": {},
            "similar_stores": [],
            "market_intelligence": {},
        }

        # -- Available HVAC system types ----------------------------------
        try:
            from apps.procurement.models import HVACServiceScope
            scopes = list(
                HVACServiceScope.objects.filter(is_active=True)
                .order_by("sort_order", "system_type")
                .values(
                    "system_type",
                    "display_name",
                    "description",
                    "typical_applications",
                    "capex_band",
                    "opex_band",
                )
            )
            for s in scopes:
                context["available_systems"].append({
                    "system_type": s.get("system_type", ""),
                    "name": s.get("display_name") or s.get("system_type", ""),
                    "description": s.get("description") or "",
                    "typical_applications": s.get("typical_applications") or "",
                    "capex_band": s.get("capex_band") or "",
                    "opex_band": s.get("opex_band") or "",
                })
                context["system_code_to_label"][s.get("system_type", "")] = (
                    s.get("display_name") or s.get("system_type", "")
                )
        except Exception as exc:
            logger.debug("HVACRecommendationAgent: HVACServiceScope load failed: %s", exc)

        # If no scopes in DB, fall back to hardcoded known types
        if not context["available_systems"]:
            for code, label in _KNOWN_SYSTEM_TYPES.items():
                context["available_systems"].append({
                    "system_type": code,
                    "name": label,
                    "description": "",
                    "typical_applications": "",
                    "capex_band": "",
                    "opex_band": "",
                })
                context["system_code_to_label"][code] = label

        # -- Also add any systems found in HVACRecommendationRule that are not
        #    already in the scope list (broadens the agent's system vocabulary)
        try:
            from apps.procurement.models import HVACRecommendationRule
            rule_systems = set(
                HVACRecommendationRule.objects
                .filter(is_active=True)
                .values_list("recommended_system", flat=True)
                .distinct()
            )
            existing_codes = {s["system_type"] for s in context["available_systems"]}
            for code in rule_systems:
                if code and code not in existing_codes:
                    label = _KNOWN_SYSTEM_TYPES.get(code, code)
                    context["available_systems"].append({
                        "system_type": code,
                        "name": label,
                        "description": "",
                        "typical_applications": "(referenced in DB rules)",
                        "capex_band": "",
                        "opex_band": "",
                    })
                    context["system_code_to_label"][code] = label
        except Exception as exc:
            logger.debug("HVACRecommendationAgent: rule systems load failed: %s", exc)

        # -- Similar store profiles ----------------------------------------
        try:
            from apps.procurement.models import HVACStoreProfile
            store_type = str(attrs.get("store_type") or "").upper()
            country = str(
                attrs.get("country") or attrs.get("geography_country") or ""
            ).upper()
            area_sqft = float(attrs.get("area_sqft") or 0)

            qs = HVACStoreProfile.objects.filter(is_active=True)
            if store_type:
                qs = qs.filter(store_type__iexact=store_type)
            if country:
                qs = qs.filter(country__iexact=country)

            similar = list(qs.order_by("-area_sqft")[:5])
            if not similar and store_type:
                # Relax country filter
                similar = list(
                    HVACStoreProfile.objects
                    .filter(is_active=True, store_type__iexact=store_type)
                    .order_by("-area_sqft")[:5]
                )

            for profile in similar:
                context["similar_stores"].append({
                    "store_id": profile.store_id,
                    "store_type": profile.store_type,
                    "country": profile.country,
                    "city": profile.city,
                    "area_sqft": profile.area_sqft,
                    "ambient_temp_max": profile.ambient_temp_max,
                    "heat_load_category": profile.heat_load_category,
                    "existing_hvac_type": profile.existing_hvac_type,
                    "budget_level": profile.budget_level,
                    "energy_efficiency_priority": profile.energy_efficiency_priority,
                })
        except Exception as exc:
            logger.debug("HVACRecommendationAgent: similar store load failed: %s", exc)

        # -- Market intelligence ------------------------------------------
        if procurement_request_pk:
            try:
                from apps.procurement.models import MarketIntelligenceSuggestion
                mi = (
                    MarketIntelligenceSuggestion.objects
                    .filter(request_id=procurement_request_pk)
                    .order_by("-created_at")
                    .first()
                )
                if mi:
                    context["market_intelligence"] = {
                        "ai_summary": mi.ai_summary or "",
                        "market_context": mi.market_context or "",
                        "system_code": mi.system_code or "",
                        "system_name": mi.system_name or "",
                        "suggestion_count": mi.suggestion_count,
                        "suggestions": (mi.suggestions_json or [])[:5],
                    }
            except Exception as exc:
                logger.debug("HVACRecommendationAgent: market intel load failed: %s", exc)

        return context

    # ------------------------------------------------------------------
    # Public: tradeoff explanation (rule-matched path -- existing behaviour)
    # ------------------------------------------------------------------

    @staticmethod
    def explain(*, attrs: Dict[str, Any], rule_result: Dict[str, Any]) -> Dict[str, Any]:
        """Generate procurement-facing tradeoff reasoning for a matched rule result.

        This is called ONLY when the deterministic rules engine produced a
        confident match.  It does NOT perform system selection.
        """
        llm = LLMClient()
        payload = {
            "attributes": attrs,
            "rule_result": rule_result,
            "instruction": "Provide procurement-facing HVAC reasoning only.",
        }

        try:
            response = llm.chat(
                messages=[
                    LLMMessage(
                        role="system",
                        content=HVACRecommendationAgent.EXPLAIN_SYSTEM_PROMPT,
                    ),
                    LLMMessage(
                        role="user",
                        content=json.dumps(payload, default=str),
                    ),
                ],
            )
            parsed = _extract_json(response.content or "")
            if not parsed:
                raise ValueError("LLM returned non-JSON response")

            return {
                "reasoning_summary": str(parsed.get("reasoning_summary") or ""),
                "tradeoffs": parsed.get("tradeoffs") or [],
                "decision_drivers": parsed.get("decision_drivers") or [],
                "alternate_option": parsed.get("alternate_option") or {},
                "reasoning_details": {
                    "ai_reasoning_used": True,
                    "source": "hvac_agent_explain",
                    "tradeoffs": parsed.get("tradeoffs") or [],
                },
                "llm_model_used": llm.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "total_tokens": response.total_tokens,
                "llm_usage": {
                    "model": llm.model,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "total_tokens": response.total_tokens,
                },
            }
        except Exception as exc:
            logger.warning("HVACRecommendationAgent.explain failed: %s", exc)
            return {
                "reasoning_summary": (
                    "AI tradeoff reasoning unavailable; deterministic recommendation retained."
                ),
                "tradeoffs": [],
                "decision_drivers": [],
                "alternate_option": {},
                "reasoning_details": {
                    "ai_reasoning_used": False,
                    "source": "hvac_agent_explain_error",
                    "error": str(exc),
                },
            }
