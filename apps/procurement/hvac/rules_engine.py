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
# Rule library — each rule is a callable that returns (match, option, reasoning, confidence)
# ---------------------------------------------------------------------------

class HVACRulesEngine:
    """Stateless deterministic HVAC recommendation rules engine.

    Call `evaluate(request, attrs)` to get a recommendation dict.
    """

    @staticmethod
    def evaluate(
        domain_code: str,
        attrs: Dict[str, Any],
        geography_country: str = "",
    ) -> Dict[str, Any]:
        """Evaluate all rules and return the best recommendation.

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
        if domain_code.upper() != "HVAC":
            return {
                "recommended_option": "",
                "reasoning_summary": "Non-HVAC domain — deferring to AI.",
                "confident": False,
                "confidence": 0.0,
                "constraints": [],
                "reasoning_details": {"source": "rules_engine", "domain": domain_code},
            }

        missing = _missing_required(attrs)
        if missing:
            return {
                "recommended_option": "",
                "reasoning_summary": (
                    f"Cannot determine recommendation: required attributes missing: {', '.join(missing)}. "
                    "Please provide all mandatory store parameters."
                ),
                "confident": False,
                "confidence": 0.0,
                "constraints": [{"type": "MISSING_DATA", "detail": f"Missing: {', '.join(missing)}"}],
                "reasoning_details": {"source": "rules_engine", "missing_attrs": missing},
            }

        # Gather normalised inputs
        store_type = _get(attrs, "store_type", "STANDALONE")
        zone_count = _get_num(attrs, "zone_count", 1)
        area_sqm = _get_num(attrs, "area_sqm", 0)
        ambient_max = _get_num(attrs, "ambient_temp_max", 45)
        cw_available = _get(attrs, "chilled_water_available", "NO")
        outdoor_restriction = _get(attrs, "outdoor_unit_restriction", "NO")
        efficiency_priority = _get(attrs, "efficiency_priority", "NO")
        dust_level = _get(attrs, "dust_level", "LOW")
        humidity_level = _get(attrs, "humidity_level", "LOW")
        budget_category = _get(attrs, "budget_category", "MEDIUM")
        cooling_load_tr = _get_num(attrs, "cooling_load_tr")
        noise_sensitivity = _get(attrs, "noise_sensitivity", "LOW")
        existing_infra = _get(attrs, "existing_infrastructure", "NONE")

        # Derive cooling load if not provided (rough rule: 130W/sqm for GCC retail)
        estimated_tr = cooling_load_tr or (area_sqm * 130 / 3517) if area_sqm else None

        constraints: List[Dict[str, str]] = []
        rules_fired: List[str] = []

        # ── Rule 1: Outdoor unit restriction ─────────────────────────────
        if outdoor_restriction == "YES":
            constraints.append({
                "type": "OUTDOOR_UNIT_NOT_ALLOWED",
                "detail": "Landlord/authority does not permit outdoor condensing units.",
            })
            rules_fired.append("RULE_01_OUTDOOR_RESTRICTION")

        # ── Rule 2: Existing chilled water infrastructure ─────────────────
        if cw_available == "YES" and existing_infra in ("CHILLED_WATER", "NONE", ""):
            constraints.append({
                "type": "CW_INTEGRATION",
                "detail": "Existing chilled water backbone available — FCU integration recommended.",
            })

        # ── Rule 3: Compliance standards by geography ─────────────────────
        geo_upper = geography_country.strip().upper()
        applicable_standards = (
            COMPLIANCE_STANDARDS_BY_GEO.get(geo_upper)
            or COMPLIANCE_STANDARDS_BY_GEO.get("UAE")  # GCC default
        )
        constraints.append({
            "type": "COMPLIANCE",
            "detail": f"Applicable standards: {', '.join(applicable_standards[:3])}",
        })

        # ── Rule 4: High dust → filtration ────────────────────────────────
        if dust_level == "HIGH":
            constraints.append({
                "type": "FILTRATION_REQUIRED",
                "detail": "High dust environment: ASHRAE MERV 11+ or G4/F7 pre-filter mandatory.",
            })
            rules_fired.append("RULE_04_HIGH_DUST")

        # ── Rule 5: High humidity → anti-corrosion ────────────────────────
        if humidity_level == "HIGH" or geo_upper in ("", "UAE_COASTAL", "UAE"):
            constraints.append({
                "type": "ANTI_CORROSION_COILS",
                "detail": "Coastal/humid environment: epoxy-coated or blue-fin coil treatment required.",
            })
            rules_fired.append("RULE_05_HIGH_HUMIDITY")

        # ── Rule 6: Noise sensitivity ─────────────────────────────────────
        if noise_sensitivity == "HIGH":
            constraints.append({
                "type": "LOW_NOISE_EQUIPMENT",
                "detail": "High noise sensitivity: select low-dB indoor units (≤35 dBA at 1m).",
            })

        # ── Rule 7: Efficiency priority ───────────────────────────────────
        if efficiency_priority == "YES":
            constraints.append({
                "type": "EFFICIENCY_REQUIREMENT",
                "detail": (
                    "Efficiency priority set: minimum SEER/IPLV thresholds apply. "
                    "Prefer VRF (IPLV ≥ 5.0) or chilled water system."
                ),
            })
            rules_fired.append("RULE_07_EFFICIENCY")

        # ─────────────────────────────────────────────────────────────────
        # PRIMARY SYSTEM SELECTION RULES
        # ─────────────────────────────────────────────────────────────────

        # Decision matrix based on store type + CW + zone count + area
        selected_option = None
        confidence = 0.0
        reasoning_summary = ""
        reasoning_lines: List[str] = []

        # ── MALL + Chilled Water available → FCU ─────────────────────────
        if store_type in ("MALL",) and cw_available == "YES":
            selected_option = "FCU_CHILLED_WATER"
            confidence = 0.95
            rules_fired.append("RULE_M1_MALL_FCU_CW")
            reasoning_lines.append(
                "Mall store with chilled water backbone available → FCU (Fan Coil Unit) on "
                "chilled water is the standard approach. No outdoor units required; "
                "maximises energy efficiency using central plant."
            )

        # ── MALL + No Chilled Water → VRF (multi-zone) or Split ──────────
        elif store_type in ("MALL",) and cw_available != "YES":
            if zone_count and zone_count >= 3:
                selected_option = "VRF_SYSTEM"
                confidence = 0.82
                rules_fired.append("RULE_M2_MALL_NO_CW_VRF")
                reasoning_lines.append(
                    f"Mall store without chilled water, {int(zone_count)} zones → "
                    "VRF recommended. Single outdoor unit with multiple indoor units "
                    "minimises installation footprint in mall structure."
                )
            else:
                if outdoor_restriction == "YES":
                    selected_option = "CASSETTE_SPLIT"
                    confidence = 0.75
                    rules_fired.append("RULE_M3_MALL_NO_CW_SPLIT_RESTRICTED")
                    reasoning_lines.append(
                        "Mall without CW and outdoor restriction: ceiling cassette split "
                        "units with concealed refrigerant lines recommended."
                    )
                else:
                    selected_option = "SPLIT_SYSTEM"
                    confidence = 0.80
                    rules_fired.append("RULE_M3_MALL_NO_CW_SPLIT")
                    reasoning_lines.append(
                        f"Mall store without CW, {int(zone_count or 1)} zone(s) → "
                        "Split systems are cost-effective for low zone count."
                    )

        # ── STANDALONE + High Ambient + Multiple Zones → VRF ─────────────
        elif store_type in ("STANDALONE", "OFFICE") and ambient_max and ambient_max >= 46:
            if zone_count and zone_count >= 3:
                if efficiency_priority == "YES" or (estimated_tr and estimated_tr > 15):
                    selected_option = "VRF_SYSTEM"
                    confidence = 0.92
                    rules_fired.append("RULE_S1_STANDALONE_HIGH_AMB_VRF")
                    reasoning_lines.append(
                        f"Standalone store with max ambient {ambient_max}°C and {int(zone_count)} zones. "
                        "VRF system recommended: superior performance at high ambient temperatures, "
                        "individual zone control, and highest efficiency (IPLV ≥ 4.5) under GCC conditions."
                    )
                else:
                    selected_option = "VRF_SYSTEM"
                    confidence = 0.88
                    rules_fired.append("RULE_S1b_STANDALONE_HIGH_AMB_VRF")
                    reasoning_lines.append(
                        f"Standalone store with high ambient ({ambient_max}°C) and multi-zone requirement → "
                        "VRF is recommended for reliable performance and flexible zoning."
                    )
            else:
                # Small standalone, high ambient but few zones
                if estimated_tr and estimated_tr > 10 and budget_category not in ("LOW",):
                    selected_option = "VRF_SYSTEM"
                    confidence = 0.78
                    rules_fired.append("RULE_S2_STANDALONE_MEDIUM_VRF")
                    reasoning_lines.append(
                        f"Standalone with {int(zone_count or 2)} zone(s) and moderate load "
                        f"({estimated_tr:.1f} TR estimated) → VRF preferred for high ambient performance."
                    )
                else:
                    selected_option = "SPLIT_SYSTEM"
                    confidence = 0.85
                    rules_fired.append("RULE_S3_STANDALONE_SMALL_SPLIT")
                    reasoning_lines.append(
                        f"Standalone store, {int(zone_count or 1)} zone(s), load ≈ "
                        f"{estimated_tr:.1f} TR → Split systems are cost-effective and widely serviceable."
                    )

        # ── STANDALONE + Normal Ambient ───────────────────────────────────
        elif store_type in ("STANDALONE", "OFFICE"):
            if zone_count and zone_count >= 4:
                selected_option = "VRF_SYSTEM"
                confidence = 0.85
                rules_fired.append("RULE_S4_STANDALONE_MULTI_VRF")
                reasoning_lines.append(
                    f"{int(zone_count)} independent zones → VRF recommended for individual zone control "
                    "and reduced refrigerant piping complexity vs multiple split systems."
                )
            elif zone_count and zone_count >= 2 and budget_category not in ("LOW",):
                selected_option = "VRF_SYSTEM"
                confidence = 0.72
                rules_fired.append("RULE_S5_STANDALONE_2ZONE_VRF")
                reasoning_lines.append(
                    "2-zone standalone store with medium/high budget → "
                    "VRF provides better long-term efficiency though split systems are viable."
                )
            else:
                selected_option = "SPLIT_SYSTEM"
                confidence = 0.88
                rules_fired.append("RULE_S6_STANDALONE_1ZONE_SPLIT")
                reasoning_lines.append(
                    "Single/low-zone standalone store → Split systems are the most practical "
                    "and cost-effective solution."
                )

        # ── WAREHOUSE + Large Load → Packaged DX or Chiller ──────────────
        elif store_type == "WAREHOUSE":
            if estimated_tr and estimated_tr > 200:
                selected_option = "CHILLER_PLANT"
                confidence = 0.90
                rules_fired.append("RULE_W1_WAREHOUSE_CHILLER")
                reasoning_lines.append(
                    f"Warehouse with estimated load {estimated_tr:.0f} TR → "
                    "Central chiller plant (air-cooled, McQuay/Carrier/York) recommended "
                    "for optimal lifecycle cost and reliability."
                )
            elif estimated_tr and estimated_tr > 50:
                selected_option = "PACKAGED_DX_UNIT"
                confidence = 0.87
                rules_fired.append("RULE_W2_WAREHOUSE_PACKAGED")
                reasoning_lines.append(
                    f"Warehouse with load {estimated_tr:.0f} TR → "
                    "Rooftop packaged DX unit with ductwork recommended. "
                    "Self-contained, minimal plant room required."
                )
            else:
                selected_option = "SPLIT_SYSTEM"
                confidence = 0.80
                rules_fired.append("RULE_W3_WAREHOUSE_SMALL_SPLIT")
                reasoning_lines.append(
                    f"Small warehouse (area {area_sqm:.0f} sqm, load ≈ {estimated_tr:.1f} TR) → "
                    "Split systems are practical for small conditioned warehouse spaces."
                )

        # ── DATA CENTER ───────────────────────────────────────────────────
        elif store_type == "DATA_CENTER":
            selected_option = "CHILLER_PLANT"
            confidence = 0.95
            rules_fired.append("RULE_DC_CHILLER")
            constraints.append({
                "type": "DATA_CENTER_REDUNDANCY",
                "detail": "N+1 redundancy required. Precision cooling units (CRAC/CRAH) may be needed.",
            })
            reasoning_lines.append(
                "Data centre: chiller plant with N+1 redundancy and 24/7 operation profile. "
                "Precision cooling preferred for rack-level thermal management."
            )

        # ── RESTAURANT / FOOD & BEVERAGE ──────────────────────────────────
        elif store_type in ("RESTAURANT",):
            if cw_available == "YES":
                selected_option = "FCU_CHILLED_WATER"
                confidence = 0.88
                rules_fired.append("RULE_R1_RESTAURANT_FCU")
            else:
                selected_option = "CASSETTE_SPLIT"
                confidence = 0.82
                rules_fired.append("RULE_R2_RESTAURANT_CASSETTE")
            constraints.append({
                "type": "KITCHEN_EXHAUST",
                "detail": "Kitchen exhaust/make-up air system required per ASHRAE 62.1 §6.4.",
            })
            reasoning_lines.append(
                f"Restaurant/F&B use: {'FCU on CW' if selected_option == 'FCU_CHILLED_WATER' else 'Cassette splits'} "
                "with kitchen exhaust ventilation. High fresh air rates required per ASHRAE 62.1."
            )

        # ── Fallback — insufficient data for deterministic decision ────────
        if not selected_option:
            return {
                "recommended_option": "",
                "reasoning_summary": (
                    "Insufficient parameters for a deterministic recommendation. "
                    f"Store type: {store_type}, Zones: {zone_count}, Area: {area_sqm} sqm. "
                    "Deferring to AI analysis."
                ),
                "confident": False,
                "confidence": 0.0,
                "constraints": constraints,
                "reasoning_details": {
                    "source": "rules_engine",
                    "rules_evaluated": len(rules_fired),
                    "rules_fired": rules_fired,
                    "inputs": {
                        "store_type": store_type,
                        "zone_count": zone_count,
                        "area_sqm": area_sqm,
                        "ambient_max": ambient_max,
                        "cw_available": cw_available,
                        "estimated_tr": estimated_tr,
                    },
                },
            }

        # ── Apply budget override ─────────────────────────────────────────
        original_option = selected_option
        if budget_category == "LOW":
            if selected_option in ("VRF_SYSTEM", "CHILLER_PLANT", "FCU_CHILLED_WATER"):
                constraints.append({
                    "type": "BUDGET_CONSTRAINT",
                    "detail": (
                        f"Budget constraint (LOW) noted. {SYSTEM_TYPES[selected_option]['name']} "
                        "has higher upfront cost. Verify budget adequacy or consider split systems."
                    ),
                })
                confidence = max(confidence - 0.08, 0.60)
                rules_fired.append("RULE_BUDGET_LOW_ADJUSTMENT")

        # ── Compile final result ──────────────────────────────────────────
        system_info = SYSTEM_TYPES.get(selected_option, {})
        recommendation_text = (
            f"{system_info.get('name', selected_option)} — "
            f"{system_info.get('description', '')}"
        )

        full_reasoning = " ".join(reasoning_lines)
        if not full_reasoning:
            full_reasoning = f"Rules engine selected {selected_option} based on: {', '.join(rules_fired)}."

        return {
            "recommended_option": recommendation_text,
            "system_type_code": selected_option,
            "reasoning_summary": full_reasoning,
            "confident": True,
            "confidence": round(confidence, 3),
            "constraints": constraints,
            "reasoning_details": {
                "source": "rules_engine",
                "rules_evaluated": len(rules_fired),
                "rules_fired": rules_fired,
                "inputs": {
                    "store_type": store_type,
                    "zone_count": zone_count,
                    "area_sqm": area_sqm,
                    "ambient_temp_max": ambient_max,
                    "chilled_water_available": cw_available,
                    "outdoor_unit_restriction": outdoor_restriction,
                    "efficiency_priority": efficiency_priority,
                    "dust_level": dust_level,
                    "humidity_level": humidity_level,
                    "budget_category": budget_category,
                    "estimated_cooling_tr": round(estimated_tr, 1) if estimated_tr else None,
                },
                "applicable_standards": applicable_standards,
                "system_type": system_info,
            },
        }
