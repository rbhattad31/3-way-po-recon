"""HVACRecommendationAgent -- AI reasoning for HVAC tradeoffs and full system recommendation.

Two public entry points:
  - recommend(): called when NO DB rule matched the request attributes.
                 Performs full AI-driven system selection using project attributes,
                 available system types from DB, similar store profiles, and
                 market intelligence data.
  - explain():   called when a DB rule DID match (existing behaviour).
                 Provides procurement-facing tradeoff reasoning for the deterministic result.


                 give the db rules for references and exisiting systems 
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
        "CONTEXT: The deterministic rules engine evaluated multiple rules but NONE matched completely.\n\n"
        "Your task: Analyse why the rules failed and recommend the most appropriate HVAC system "
        "by understanding the attribute patterns that nearly matched.\n\n"
        "INPUT DATA:\n"
        "  - project_attributes: the request parameters that caused rule mismatches\n"
        "  - rules_failed: rules that were evaluated but FAILED (with reason for each failure)\n"
        "  - rules_near_miss: rules that ALMOST matched (closest candidates)\n"
        "  - no_match_context: summary of why no rule matched (inputs, mismatches)\n"
        "  - available_systems: all HVAC system types configured in database\n"
        "  - db_rules_reference: sample of rules showing decision patterns\n"
        "  - similar_stores: comparable stores in same region/type\n"
        "  - market_intelligence: current market context (may be empty)\n\n"
        "ANALYSIS APPROACH:\n"
        "1. Review rules_failed to understand why each rule rejected this request.\n"
        "2. Study rules_near_miss - these are candidates that ALMOST matched.\n"
        "   If a rule failed only on area or budget, the system type may still be appropriate.\n"
        "3. Look at available_systems recommended by near-miss rules.\n"
        "4. Cross-reference with db_rules_reference pattern matching.\n"
        "5. Select best-fit system type based on project attributes.\n\n"
        "RECOMMENDATION PROCESS:\n"
        "1. Study the near-miss rules - which systems did they recommend?\n"
        "2. Identify which attribute mismatch caused rejection (area? budget? ambient?).\n"
        "3. Select the system that best fits the ACTUAL attributes (not just rule proxies).\n"
        "4. Provide confidence based on:\n"
        "   - How close the near-miss rules were (0.9+ confidence if very close match)\n"
        "   - How consistent the near-miss recommendations are (multiple rules >> one rule)\n"
        "   - Data completeness (missing attributes >> lower confidence)\n"
        "5. Set human_validation_required=true if:\n"
        "   - Confidence < 0.70\n"
        "   - Multiple contradictory near-miss rules\n"
        "   - Key attributes missing\n\n"
        "RESPONSE FORMAT:\n"
        "Return ONLY valid JSON. No markdown. No extra keys.\n"
        "{\n"
        '  "recommended_system_type": "SYSTEM_CODE",\n'
        '  "recommended_option": "Full system name and description",\n'
        '  "reasoning_summary": "Why this system fits (2-4 sentences, reference near-miss rules and attribute logic)",\n'
        '  "confidence": 0.75,\n'
        '  "decision_drivers": ["Near-miss rule X recommended VRF; your area matches that pattern", "..."],\n'
        '  "tradeoffs": ["...", "..."],\n'
        '  "constraints": [{"type": "TYPE", "detail": "..."}, ...],\n'
        '  "alternate_option": {"system_type": "CODE", "reason": "...from other near-miss rule..."},\n'
        '  "indicative_capacity_tr": 12.5,\n'
        '  "human_validation_required": true,\n'
        '  "market_notes": "market context",\n'
        '  "compliance_notes": "GCC standards"\n'
        "}"
    )

    # Tradeoff explanation prompt (rule-matched path)
    EXPLAIN_SYSTEM_PROMPT = (
        "You are an HVAC solution advisor for procurement pre-design. "
        "A deterministic rule matched and recommended a system. "
        "Your task is to provide procurement-facing reasoning, tradeoffs, and alternatives.\n\n"
        "You receive:\n"
        "  - The matched rule and recommendation result\n"
        "  - Original request attributes\n"
        "  - Database rules reference (to identify alternative patterns)\n\n"
        "Generate a professional explanation that:\n"
        "  - Justifies the matched rule's recommendation\n"
        "  - Lists 2-3 key tradeoffs (cost, efficiency, complexity)\n"
        "  - References similar rules or patterns as alternatives\n"
        "  - Identifies any constraints or special conditions\n\n"
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
            "rules_failed": db_ctx["rules_failed"],  # NEW: rules that failed
            "rules_near_miss": db_ctx["rules_near_miss"],  # NEW: rules that almost matched
            "no_match_context": no_match_context,
            "available_systems": db_ctx["available_systems"],
            "db_rules_reference": db_ctx["db_rules_reference"],
            "similar_stores": db_ctx["similar_stores"],
            "market_intelligence": db_ctx["market_intelligence"],
            "instruction": (
                "Analyse why rules failed and recommend the best system based on near-miss patterns. "
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
                or f"AI-recommended {system_code} based on project profile analysis and rule pattern matching."
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
                    "rules_failed_count": len(db_ctx["rules_failed"]),
                    "rules_near_miss_count": len(db_ctx["rules_near_miss"]),
                    "db_systems_available": len(db_ctx["available_systems"]),
                    "db_rules_reference_used": len(db_ctx["db_rules_reference"]),
                    "similar_stores_found": len(db_ctx["similar_stores"]),
                    "market_intel_available": bool(db_ctx["market_intelligence"]),
                    "rules_failed_summary": [
                        {
                            "rule_code": r.get("rule_code"),
                            "recommended_system": r.get("recommended_system"),
                            "failure_reasons": r.get("failure_reasons", [])[:2],  # Top 2 reasons
                        }
                        for r in db_ctx["rules_failed"][:3]  # Top 3 failures
                    ],
                    "rules_near_miss_summary": [
                        {
                            "rule_code": r.get("rule_code"),
                            "recommended_system": r.get("recommended_system"),
                            "failure_reasons": r.get("failure_reasons", [])[:1],
                        }
                        for r in db_ctx["rules_near_miss"][:3]  # Top 3 near-misses
                    ],
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
          - db_rules_reference: up to 10 active rules showing decision patterns
          - rules_failed: rules evaluated that did NOT match (with failure reasons)
          - rules_near_miss: rules that ALMOST matched (closest candidates)
          - similar_stores: up to 5 HVACStoreProfile rows with same store_type/country
          - market_intelligence: latest MarketIntelligenceSuggestion for the request
        """
        context: Dict[str, Any] = {
            "available_systems": [],
            "system_code_to_label": {},
            "db_rules_reference": [],
            "rules_failed": [],  # NEW: rules that failed evaluation
            "rules_near_miss": [],  # NEW: near-miss rules
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
                    "equipment_scope",
                    "installation_services",
                )
            )
            for s in scopes:
                context["available_systems"].append({
                    "system_type": s.get("system_type", ""),
                    "name": s.get("display_name") or s.get("system_type", ""),
                    "equipment_scope": s.get("equipment_scope") or "",
                    "installation_services": s.get("installation_services") or "",
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
                    "equipment_scope": "",
                    "installation_services": "",
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
                        "equipment_scope": "",
                        "installation_services": "(referenced in DB rules)",
                    })
                    context["system_code_to_label"][code] = label
        except Exception as exc:
            logger.debug("HVACRecommendationAgent: rule systems load failed: %s", exc)

        # -- Database rules reference ----------------------------------------
        # Fetch up to 10 active rules to show the LLM how decisions are made
        try:
            from apps.procurement.models import HVACRecommendationRule
            active_rules = list(
                HVACRecommendationRule.objects
                .filter(is_active=True)
                .order_by("priority", "rule_code")[:10]
            )
            for rule in active_rules:
                rule_summary = {
                    "rule_code": rule.rule_code,
                    "rule_name": rule.rule_name,
                    "priority": rule.priority,
                    "recommended_system": rule.recommended_system,
                    "conditions": [],
                }
                # Build human-readable conditions
                if rule.store_type_filter:
                    rule_summary["conditions"].append(f"store_type: {rule.store_type_filter}")
                if rule.area_sq_ft_min is not None:
                    rule_summary["conditions"].append(f"area >= {rule.area_sq_ft_min} sqft")
                if rule.area_sq_ft_max is not None:
                    rule_summary["conditions"].append(f"area < {rule.area_sq_ft_max} sqft")
                if rule.ambient_temp_min_c is not None:
                    rule_summary["conditions"].append(f"ambient >= {rule.ambient_temp_min_c}C")
                if rule.budget_level_filter:
                    rule_summary["conditions"].append(f"budget: {rule.budget_level_filter}")
                if rule.energy_priority_filter:
                    rule_summary["conditions"].append(f"energy_priority: {rule.energy_priority_filter}")
                if rule.country_filter:
                    rule_summary["conditions"].append(f"country: {rule.country_filter}")
                if rule.city_filter:
                    rule_summary["conditions"].append(f"city: {rule.city_filter}")
                
                context["db_rules_reference"].append(rule_summary)
        except Exception as exc:
            logger.debug("HVACRecommendationAgent: DB rules reference load failed: %s", exc)

        # -- Evaluate rules to find failures and near-misses ----------------------
        # Dynamically check which rules almost matched
        try:
            from apps.procurement.models import HVACRecommendationRule
            all_rules = list(
                HVACRecommendationRule.objects
                .filter(is_active=True)
                .order_by("priority", "rule_code")
            )
            
            for rule in all_rules:
                try:
                    matched = rule.matches(attrs)
                    if not matched:
                        # Rule failed: analyse why
                        failure_reasons = []
                        
                        # Check each condition
                        if rule.store_type_filter and str(attrs.get("store_type", "")).upper() != rule.store_type_filter:
                            failure_reasons.append(f"store_type mismatch (rule: {rule.store_type_filter}, actual: {attrs.get('store_type')})")
                        
                        area_val = float(attrs.get("area_sqft", 0)) if attrs.get("area_sqft") else 0
                        if rule.area_sq_ft_min is not None and area_val < rule.area_sq_ft_min:
                            failure_reasons.append(f"area too small (rule min: {rule.area_sq_ft_min}, actual: {area_val:.0f})")
                        if rule.area_sq_ft_max is not None and area_val >= rule.area_sq_ft_max:
                            failure_reasons.append(f"area too large (rule max: {rule.area_sq_ft_max}, actual: {area_val:.0f})")
                        
                        ambient_val = float(attrs.get("ambient_temp_max", 0)) if attrs.get("ambient_temp_max") else 0
                        if rule.ambient_temp_min_c is not None and ambient_val < rule.ambient_temp_min_c:
                            failure_reasons.append(f"ambient too cool (rule min: {rule.ambient_temp_min_c}C, actual: {ambient_val}C)")
                        
                        if rule.budget_level_filter and str(attrs.get("budget_level", "")).upper() != rule.budget_level_filter:
                            failure_reasons.append(f"budget mismatch (rule: {rule.budget_level_filter}, actual: {attrs.get('budget_level')})")
                        
                        if rule.energy_priority_filter and str(attrs.get("energy_efficiency_priority", "")).upper() != rule.energy_priority_filter:
                            failure_reasons.append(f"energy priority mismatch (rule: {rule.energy_priority_filter}, actual: {attrs.get('energy_efficiency_priority')})")
                        
                        if rule.country_filter and str(attrs.get("country", "")).upper() not in [c.strip().upper() for c in rule.country_filter.split("|")]:
                            failure_reasons.append(f"country mismatch (rule: {rule.country_filter}, actual: {attrs.get('country')})")
                        
                        if rule.city_filter and str(attrs.get("city", "")).upper() != rule.city_filter.upper():
                            failure_reasons.append(f"city mismatch (rule: {rule.city_filter}, actual: {attrs.get('city')})")
                        
                        # Count how many conditions failed
                        failure_count = len(failure_reasons)
                        total_conditions = sum(1 for x in [
                            rule.store_type_filter,
                            rule.area_sq_ft_min or rule.area_sq_ft_max,
                            rule.ambient_temp_min_c,
                            rule.budget_level_filter,
                            rule.energy_priority_filter,
                            rule.country_filter,
                            rule.city_filter,
                        ] if x)
                        
                        failed_rule = {
                            "rule_code": rule.rule_code,
                            "rule_name": rule.rule_name,
                            "recommended_system": rule.recommended_system,
                            "priority": rule.priority,
                            "failure_reasons": failure_reasons,
                            "conditions_failed": failure_count,
                            "total_conditions": total_conditions,
                        }
                        
                        # Near-miss if only 1-2 conditions failed OR all conditions match but something else failed
                        if failure_count <= 2 and total_conditions > 0:
                            context["rules_near_miss"].append(failed_rule)
                        else:
                            context["rules_failed"].append(failed_rule)
                except Exception as e:
                    logger.debug("HVACRecommendationAgent: rule evaluation error for rule %s: %s", rule.rule_code, e)
            
            # Sort near-miss rules by closeness (fewest failures first)
            context["rules_near_miss"] = sorted(
                context["rules_near_miss"],
                key=lambda x: (x["conditions_failed"], x["priority"])
            )[:5]  # Top 5 near-miss candidates
            
        except Exception as exc:
            logger.debug("HVACRecommendationAgent: rule evaluation failed: %s", exc)

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
