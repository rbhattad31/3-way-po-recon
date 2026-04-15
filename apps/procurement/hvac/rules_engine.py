"""HVAC Deterministic Rules Engine.

Implements the 6-step recommendation logic from the HVAC GenAI Consulting
Requirement Document (Section 5.4).

Rule Priority:
  1. Hard constraints first (outdoor unit restrictions, infrastructure)
  2. Primary system selection (store type + CW availability + zone count)
  3. Capacity sizing (area / TR load)
  4. Efficiency and environmental modifiers
  5. Budget constraints
  6. Compliance and special requirements
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from apps.procurement.hvac.constants import (
    COMPLIANCE_STANDARDS_BY_GEO,
    HVAC_REQUIRED_FOR_RECOMMENDATION,
    SYSTEM_TYPES,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: Safe attribute access
# ---------------------------------------------------------------------------
def _get(attrs: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Get attribute value, checking both TEXT and NUMBER slots."""
    val = attrs.get(key)
    if val is None:
        return default
    # Clean up string values
    if isinstance(val, str):
        return val.strip().upper() if val.strip() else default
    return val


def _get_num(attrs: Dict[str, Any], key: str, default: Optional[float] = None) -> Optional[float]:
    """Get a numeric attribute value."""
    val = attrs.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _missing_required(attrs: Dict[str, Any]) -> List[str]:
    """Return list of required attributes that are missing."""
    return [k for k in HVAC_REQUIRED_FOR_RECOMMENDATION if not attrs.get(k)]


# ---------------------------------------------------------------------------
# DB-driven rules engine
# ---------------------------------------------------------------------------


class HVACRulesEngine:
    """DB-driven HVAC recommendation rules engine.

    All recommendation logic is stored in HVACRecommendationRule records
    (configured via the Configuration page).  Rules are evaluated in ascending
    priority order; the first rule whose conditions match the request attributes
    determines the recommended system.  No hardcoded decision logic is applied.

    Call `evaluate(domain_code, attrs, geography_country)` to get a
    recommendation dict.
    """

    # Default confidence assigned to DB-rule matches.
    DEFAULT_CONFIDENCE = 0.90

    @staticmethod
    def evaluate(
        domain_code: str,
        attrs: Dict[str, Any],
        geography_country: str = "",
    ) -> Dict[str, Any]:
        """Evaluate DB-configured rules and return the first matching recommendation.

        Rules are loaded from HVACRecommendationRule (is_active=True), ordered by
        priority ascending.  The first rule whose conditions match attrs determines
        the outcome.  No hardcoded Python decision logic is applied.

        Returns the standard recommendation dict:
          {
            recommended_option: str,
            reasoning_summary: str,
            reasoning_details: dict,
            confidence: float,
            constraints: list,
            confident: bool,
          }
        """
        # â”€â”€ Domain guard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if domain_code.upper() != "HVAC":
            return {
                "recommended_option": "",
                "reasoning_summary": "Non-HVAC domain -- deferring to AI.",
                "confident": False,
                "confidence": 0.0,
                "constraints": [],
                "reasoning_details": {"source": "db_rules", "domain": domain_code},
            }

        # â”€â”€ Required fields check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        missing = _missing_required(attrs)
        if missing:
            return {
                "recommended_option": "",
                "reasoning_summary": (
                    f"Cannot determine recommendation: required attributes missing: "
                    f"{', '.join(missing)}. Please fill in all mandatory fields."
                ),
                "confident": False,
                "confidence": 0.0,
                "constraints": [{"type": "MISSING_DATA", "detail": f"Missing: {', '.join(missing)}"}],
                "reasoning_details": {"source": "db_rules", "missing_attrs": missing},
            }

        # â”€â”€ Load active DB rules ordered by priority â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            from apps.procurement.models import HVACRecommendationRule
            active_rules: List[HVACRecommendationRule] = list(
                HVACRecommendationRule.objects
                .filter(is_active=True)
                .order_by("priority", "rule_code")
            )
        except Exception:
            logger.exception("HVACRulesEngine: failed to load rules from DB.")
            return {
                "recommended_option": "",
                "reasoning_summary": "Rules could not be loaded from the database. Contact your administrator.",
                "confident": False,
                "confidence": 0.0,
                "constraints": [],
                "reasoning_details": {"source": "db_rules", "error": "db_load_failed"},
            }

        if not active_rules:
            return {
                "recommended_option": "",
                "reasoning_summary": (
                    "No active HVAC recommendation rules are configured. "
                    "Please add rules in Configuration -> HVAC Recommendation Rules."
                ),
                "confident": False,
                "confidence": 0.0,
                "constraints": [],
                "reasoning_details": {"source": "db_rules", "rules_loaded": 0},
            }

        # â”€â”€ Normalise key inputs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        store_type = _get(attrs, "store_type", "")
        country = str(attrs.get("country") or attrs.get("geography_country") or "").strip().upper()
        city = str(attrs.get("city") or attrs.get("geography_city") or "").strip().upper()
        area_sqft_val: float = _get_num(attrs, "area_sqft", 0) or 0.0
        ambient_max: float = _get_num(attrs, "ambient_temp_max", 0) or 0.0
        budget_category = _get(attrs, "budget_level", "")
        efficiency_priority = _get(attrs, "energy_efficiency_priority", "")
        dust_level = _get(attrs, "dust_exposure", "LOW")
        humidity_level = _get(attrs, "humidity_level", "LOW")
        fresh_air_req = _get(attrs, "fresh_air_requirement", "NO")
        footfall_category = _get(attrs, "footfall_category", "")
        landlord_text = (attrs.get("landlord_constraints") or "").lower()
        area_sqm: float = area_sqft_val * 0.0929

        # â”€â”€ Evaluate rules: first match wins â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        matched_rule = None
        rules_evaluated = 0
        for rule in active_rules:
            rules_evaluated += 1
            try:
                if rule.matches(attrs):
                    matched_rule = rule
                    break
            except Exception:
                logger.warning(
                    "HVACRulesEngine: rule evaluation failed for rule_code=%s; skipping",
                    getattr(rule, "rule_code", ""),
                    exc_info=True,
                )

        if matched_rule is None:
            return {
                "recommended_option": "",
                "reasoning_summary": (
                    f"None of the {rules_evaluated} configured rules matched the given "
                    f"parameters (store_type={store_type}, area={area_sqft_val:.0f} sqft, "
                    f"ambient={ambient_max}C, budget={budget_category}, "
                    f"energy_priority={efficiency_priority}). "
                    "Please review the rules in Configuration -> HVAC Recommendation Rules."
                ),
                "confident": False,
                "confidence": 0.0,
                "constraints": [],
                "reasoning_details": {
                    "source": "db_rules",
                    "rules_loaded": len(active_rules),
                    "rules_evaluated": rules_evaluated,
                    "inputs": {
                        "country": country,
                        "city": city,
                        "store_type": store_type,
                        "area_sqft": area_sqft_val,
                        "ambient_temp_max": ambient_max,
                        "budget_level": budget_category,
                        "energy_efficiency_priority": efficiency_priority,
                    },
                },
            }

        # â”€â”€ Rule matched -- build constraint annotations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        constraints: List[Dict[str, str]] = []

        # Geography / compliance standards
        geo_upper = (geography_country or "").strip().upper()
        applicable_standards = (
            COMPLIANCE_STANDARDS_BY_GEO.get(geo_upper)
            or COMPLIANCE_STANDARDS_BY_GEO.get("UAE")
        )
        if applicable_standards:
            constraints.append({
                "type": "COMPLIANCE",
                "detail": f"Applicable standards: {', '.join(applicable_standards[:3])}",
            })

        # Outdoor unit restriction
        outdoor_restriction = (
            "no outdoor" in landlord_text
            or "outdoor unit" in landlord_text
            or "restrict" in landlord_text
        )
        if outdoor_restriction:
            constraints.append({
                "type": "OUTDOOR_UNIT_NOT_ALLOWED",
                "detail": "Landlord / authority does not permit outdoor condensing units.",
            })

        # Chilled water integration note
        cw_available = "chilled water" in landlord_text or " cw " in landlord_text
        if cw_available:
            constraints.append({
                "type": "CW_INTEGRATION",
                "detail": "Existing chilled water backbone available -- FCU integration may be considered.",
            })

        # High dust
        if dust_level == "HIGH":
            constraints.append({
                "type": "FILTRATION_REQUIRED",
                "detail": "High dust environment: ASHRAE MERV 11+ or G4/F7 pre-filter mandatory.",
            })

        # Humidity
        if humidity_level == "HIGH":
            constraints.append({
                "type": "ANTI_CORROSION_COILS",
                "detail": "High humidity: epoxy-coated or blue-fin coil treatment required.",
            })
        elif geo_upper in ("UAE", "UAE_COASTAL", "QATAR", "QAT", "BAHRAIN", "BHR"):
            constraints.append({
                "type": "ANTI_CORROSION_COILS",
                "detail": "GCC coastal location: blue-fin or epoxy-coated coils recommended for salt-air protection.",
            })

        # Efficiency
        if efficiency_priority in ("YES", "HIGH", "MEDIUM_HIGH"):
            constraints.append({
                "type": "EFFICIENCY_REQUIREMENT",
                "detail": "Efficiency priority set: minimum SEER/IPLV thresholds apply.",
            })

        # Fresh air
        if fresh_air_req in ("YES", "HIGH", "REQUIRED"):
            constraints.append({
                "type": "FRESH_AIR_REQUIRED",
                "detail": "Fresh air requirement: ERV/HRU integration required per ASHRAE 62.1.",
            })

        # High footfall
        if footfall_category in ("HIGH", "VERY_HIGH"):
            constraints.append({
                "type": "HIGH_FOOTFALL_LOAD_MARGIN",
                "detail": (
                    f"High footfall ({footfall_category}): add 10-15% capacity margin. "
                    "Low-noise units preferred."
                ),
            })

        # Low budget compliance note
        if budget_category == "LOW":
            constraints.append({
                "type": "LOW_BUDGET_COMPLIANCE_RISK",
                "detail": (
                    "Low budget: minimum compliant equipment must still be specified. "
                    "Do not procure below applicable standards."
                ),
            })

        # â”€â”€ Compile final result â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        selected_option = matched_rule.recommended_system
        system_info = SYSTEM_TYPES.get(selected_option, {})

        recommendation_text = (
            f"{system_info.get('name', selected_option)} -- {system_info.get('description', '')}"
            if system_info
            else selected_option
        )

        rationale = matched_rule.rationale or matched_rule.rule_name

        # Indicative capacity guidance
        estimated_tr = (area_sqm * 130 / 3517) if area_sqm else None
        if estimated_tr:
            if estimated_tr < 5:
                cap_guidance = f"{estimated_tr:.1f} TR (small -- standard split configuration)"
            elif estimated_tr < 20:
                cap_guidance = f"{estimated_tr:.1f} TR (medium -- engineering review recommended before specification)"
            elif estimated_tr < 100:
                cap_guidance = f"{estimated_tr:.1f} TR (large -- HVAC engineer sizing analysis mandatory)"
            else:
                cap_guidance = f"{estimated_tr:.0f} TR (major plant -- full load analysis and engineer sign-off required)"
        else:
            cap_guidance = "Cooling load estimate not available -- area data missing."

        # Compliance alignment
        compliance_alignment = {
            "standards": applicable_standards or [],
            "summary": (
                f"Recommendation aligned with: {', '.join((applicable_standards or [])[:4])}. "
                + ("Low budget: verify minimum compliance before raising PO. " if budget_category == "LOW" else "")
            ),
        }

        # Alternate option
        alt_code = matched_rule.alternate_system or None
        alt_info = SYSTEM_TYPES.get(alt_code, {}) if alt_code else {}
        alternate_option = (
            f"{alt_info.get('name', alt_code)} -- {alt_info.get('description', '')}"
            if alt_code and alt_info
            else alt_code or None
        )

        required_human_validation = (
            budget_category == "LOW"
            or (estimated_tr is not None and estimated_tr > 100)
            or ambient_max >= 48
            or fresh_air_req in ("YES", "HIGH", "REQUIRED")
        )

        # -- Build matched_condition_keys: only the input keys the rule actually tested --
        _matched_keys: List[str] = []
        if matched_rule.country_filter:
            _matched_keys.append("country")
        if matched_rule.city_filter:
            _matched_keys.append("city")
        if matched_rule.store_type_filter:
            _matched_keys.append("store_type")
        if matched_rule.area_sq_ft_min is not None or matched_rule.area_sq_ft_max is not None:
            _matched_keys.append("area_sqft")
            _matched_keys.append("area_sqm_derived")
        if matched_rule.ambient_temp_min_c is not None:
            _matched_keys.append("ambient_temp_max")
        if matched_rule.budget_level_filter:
            _matched_keys.append("budget_level")
        if matched_rule.energy_priority_filter:
            _matched_keys.append("energy_efficiency_priority")
        # Derived conditions inferred from landlord text also count as matched
        if outdoor_restriction:
            _matched_keys.append("outdoor_restriction_derived")
        if cw_available:
            _matched_keys.append("chilled_water_derived")

        # -- Build rule_conditions: ALL standard params with configured filter value or "Any"
        # This allows the UI to show every parameter the rule can evaluate, even wildcards.
        def _area_filter_label() -> str:
            lo = matched_rule.area_sq_ft_min
            hi = matched_rule.area_sq_ft_max
            if lo is not None and hi is not None:
                return f"{lo:,.0f} - {hi:,.0f} sqft"
            if lo is not None:
                return f">= {lo:,.0f} sqft"
            if hi is not None:
                return f"<= {hi:,.0f} sqft"
            return "Any"

        _rule_conditions: Dict[str, str] = {
            "country":                    matched_rule.country_filter or "Any",
            "city":                       matched_rule.city_filter or "Any",
            "store_type":                 matched_rule.store_type_filter or "Any",
            "area_sqft":                  _area_filter_label(),
            "ambient_temp_max":           (
                f">= {matched_rule.ambient_temp_min_c}C"
                if matched_rule.ambient_temp_min_c is not None
                else "Any"
            ),
            "budget_level":               matched_rule.budget_level_filter or "Any",
            "energy_efficiency_priority": matched_rule.energy_priority_filter or "Any",
            "outdoor_restriction_derived": "YES (required)" if outdoor_restriction else "Any",
            "chilled_water_derived":       "YES (required)" if cw_available else "Any",
        }

        return {
            "recommended_option": recommendation_text,
            "system_type_code": selected_option,
            "reasoning_summary": rationale,
            "confident": True,
            "confidence": HVACRulesEngine.DEFAULT_CONFIDENCE,
            "confidence_score_100": round(HVACRulesEngine.DEFAULT_CONFIDENCE * 100),
            "constraints": constraints,
            "alternate_option": alternate_option,
            "indicative_capacity_guidance": cap_guidance,
            "compliance_alignment": compliance_alignment,
            "required_human_validation": required_human_validation,
            "top_decision_drivers": [
                f"Rule {matched_rule.rule_code}: {matched_rule.rule_name}",
                f"Store type: {store_type}",
                f"Area: {area_sqft_val:.0f} sqft",
                f"Ambient max: {ambient_max}C",
                f"Budget: {budget_category}",
                f"Energy priority: {efficiency_priority}",
            ],
            "reasoning_details": {
                "source": "db_rules",
                "rule_matched": matched_rule.rule_code,
                "rule_name": matched_rule.rule_name,
                "rule_priority": matched_rule.priority,
                "rule_id": matched_rule.pk,
                "rules_loaded": len(active_rules),
                "rules_evaluated": rules_evaluated,
                "db_rule": {
                    "id": matched_rule.pk,
                    "rule_code": matched_rule.rule_code,
                    "rule_name": matched_rule.rule_name,
                    "priority": matched_rule.priority,
                    "recommended_system": matched_rule.recommended_system,
                    "alternate_system": matched_rule.alternate_system,
                    "rationale": matched_rule.rationale,
                },
                "matched_condition_keys": _matched_keys,
                "rule_conditions": _rule_conditions,
                "inputs": {
                    "country": country,
                    "city": city,
                    "store_type": store_type,
                    "area_sqft": area_sqft_val,
                    "area_sqm_derived": round(area_sqm, 1),
                    "ambient_temp_max": ambient_max,
                    "budget_level": budget_category,
                    "energy_efficiency_priority": efficiency_priority,
                    "dust_exposure": dust_level,
                    "humidity_level": humidity_level,
                    "fresh_air_requirement": fresh_air_req,
                    "footfall_category": footfall_category,
                    "chilled_water_derived": "YES" if cw_available else "NO",
                    "outdoor_restriction_derived": "YES" if outdoor_restriction else "NO",
                    "estimated_cooling_tr": round(estimated_tr, 1) if estimated_tr else None,
                },
                "applicable_standards": applicable_standards or [],
                "system_type": system_info,
            },
        }

