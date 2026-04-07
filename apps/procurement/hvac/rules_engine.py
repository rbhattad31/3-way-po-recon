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

        # Gather normalised inputs (new schema -- sq ft + heat_load_category)
        store_type = _get(attrs, "store_type", "STANDALONE")
        area_sqft_val = _get_num(attrs, "area_sqft", 0)
        area_sqm = (area_sqft_val * 0.0929) if area_sqft_val else 0.0  # convert to sqm internally
        ambient_max = _get_num(attrs, "ambient_temp_max", 45)
        heat_load_category = _get(attrs, "heat_load_category", "MEDIUM")

        # Derive chilled water availability and outdoor restriction from free-text fields
        landlord_text = (attrs.get("landlord_constraints") or "").lower()
        existing_hvac_text = (attrs.get("existing_hvac_type") or "").lower()
        cw_available = "YES" if (
            "chilled water" in landlord_text or " cw " in landlord_text
            or "chilled water" in existing_hvac_text
        ) else "NO"
        outdoor_restriction = "YES" if (
            "no outdoor" in landlord_text
            or "outdoor unit" in landlord_text
            or "restrict" in landlord_text
        ) else "NO"
        existing_infra = (
            "CHILLED_WATER" if "chilled water" in existing_hvac_text
            else (_get(attrs, "existing_hvac_type", "NONE") or "NONE")
        )

        efficiency_priority = _get(attrs, "energy_efficiency_priority", "NO")
        dust_level = _get(attrs, "dust_exposure", "LOW")
        humidity_level = _get(attrs, "humidity_level", "LOW")
        budget_category = _get(attrs, "budget_level", "MEDIUM")
        maintenance_priority = _get(attrs, "maintenance_priority", "STANDARD")
        store_format = _get(attrs, "store_format", "")
        footfall_category = _get(attrs, "footfall_category", "")
        fresh_air_requirement = _get(attrs, "fresh_air_requirement", "NO")

        # heat_load_category drives zone proxy: HIGH -> 3 zones, MEDIUM -> 2, LOW -> 1
        zone_count = 3.0 if heat_load_category == "HIGH" else (2.0 if heat_load_category == "MEDIUM" else 1.0)

        # Derive estimated cooling load from area (GCC rule: 130 W/sqm)
        estimated_tr = (area_sqm * 130 / 3517) if area_sqm else None

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

        # ── Rule 5a: High humidity → anti-corrosion ─────────────────────────
        if humidity_level == "HIGH":
            constraints.append({
                "type": "ANTI_CORROSION_COILS",
                "detail": "High humidity level: epoxy-coated or blue-fin coil treatment required. Corrosion-resistant casing mandatory.",
            })
            rules_fired.append("RULE_05_HIGH_HUMIDITY")
        # ── Rule 5b: GCC coastal geo → salt-air protection ────────────────
        elif geo_upper in ("UAE", "UAE_COASTAL", "QATAR", "QAT", "BAHRAIN", "BHR"):
            constraints.append({
                "type": "ANTI_CORROSION_COILS",
                "detail": "GCC coastal location: blue-fin or epoxy-coated coil treatment recommended for salt-air and sand protection.",
            })
            rules_fired.append("RULE_05b_COASTAL_ANTI_CORROSION")

        # ── Rule 7: Efficiency priority ───────────────────────────────────────────
        if efficiency_priority in ("YES", "HIGH"):
            constraints.append({
                "type": "EFFICIENCY_REQUIREMENT",
                "detail": (
                    "Efficiency priority set: minimum SEER/IPLV thresholds apply. "
                    "Prefer VRF (IPLV >= 5.0) or chilled water system."
                ),
            })
            rules_fired.append("RULE_07_EFFICIENCY")

        # ── Rule 8: Fresh air requirement ─────────────────────────────────────
        if fresh_air_requirement in ("YES", "HIGH", "REQUIRED"):
            constraints.append({
                "type": "FRESH_AIR_REQUIRED",
                "detail": (
                    "Fresh air requirement specified: energy recovery ventilator (ERV) or heat recovery "
                    "unit (HRU) integration required. Ventilation rates per ASHRAE 62.1 / local authority."
                ),
            })
            rules_fired.append("RULE_08_FRESH_AIR_ERV")

        # ── Rule 9: High footfall ──────────────────────────────────────────────
        if footfall_category in ("HIGH", "VERY_HIGH"):
            constraints.append({
                "type": "HIGH_FOOTFALL_LOAD_MARGIN",
                "detail": (
                    f"High footfall category ({footfall_category}): add 10-15% capacity margin above "
                    "base load calculation. Lower noise systems (cassette or concealed duct) preferred."
                ),
            })
            rules_fired.append("RULE_09_HIGH_FOOTFALL")

        # ─────────────────────────────────────────────────────────────────
        # PRIMARY SYSTEM SELECTION RULES
        # ─────────────────────────────────────────────────────────────────

        # Decision matrix based on store type + CW + zone count + area
        selected_option = None
        confidence = 0.0
        reasoning_summary = ""
        reasoning_lines: List[str] = []

        # ─────────────────────────────────────────────────────────────────
        # PRE-EMPTIVE UNIVERSAL RULES
        # Fire before the store-type-specific chain in priority order:
        #   TECH (highest) -> R3 Large+HiEff -> U2 LowBudget -> STDEFF -> STDMAINT -> U3 (fallback)
        # ─────────────────────────────────────────────────────────────────

        # ── Rule TECH: Small technical room or isolated intervention ───────
        # POC condition 4: Small technical room or isolated intervention -> Split
        _TECHNICAL_FORMATS = (
            "TECHNICAL_ROOM", "SERVER_ROOM", "TELECOM_ROOM", "IT_ROOM",
            "KIOSK", "BOOTH", "CONTROL_ROOM",
        )
        if (
            (store_format and any(t in store_format for t in _TECHNICAL_FORMATS))
            or (area_sqft_val is not None and area_sqft_val < 300)
        ):
            selected_option = "SPLIT_SYSTEM"
            confidence = 0.92
            rules_fired.append("RULE_TECH_ISOLATED_SPLIT")
            reasoning_lines.append(
                f"Store format '{store_format or 'SMALL_AREA'}' ({area_sqft_val:.0f} sqft) is a "
                "small technical room or isolated intervention. Single-zone split AC is the standard "
                "recommendation: simple installation, minimal capital outlay, and sufficient for "
                "limited-scope cooling with a well-defined local heat source."
            )

        # ── R2: Small Area (< 2,000 sqm) -- Universal Rule -> Split AC ───
        if not selected_option and area_sqm and area_sqm < 2000:
            selected_option = "SPLIT_SYSTEM"
            confidence = 0.88
            rules_fired.append("RULE_U1_SMALL_AREA_SPLIT_AC")
            reasoning_lines.append(
                f"Conditioned area {area_sqm:.0f} sqm is below the 2,000 sqm threshold. "
                "A split AC system provides sufficient capacity at the lowest capital cost "
                "and is the most practical, widely-serviceable choice for compact units."
            )

        # ── R3: Large Standalone + High Ambient + High Efficiency -> VRF ─
        if (
            not selected_option
            and store_type in ("STANDALONE", "OFFICE")
            and area_sqm is not None and area_sqm >= 5000
            and ambient_max is not None and ambient_max >= 45
            and efficiency_priority in ("YES", "HIGH")
        ):
            selected_option = "VRF_SYSTEM"
            confidence = 0.91
            rules_fired.append("RULE_S2_LARGE_STANDALONE_HIEFF_VRF")
            reasoning_lines.append(
                f"Large standalone store ({area_sqm:.0f} sqm, >=5,000 sqm threshold) with "
                f"high ambient temperature ({ambient_max}C) and HIGH efficiency priority. "
                "VRF heat-recovery system delivers best GCC peak-load performance and meets "
                "ASHRAE 90.1 IPLV targets efficiently across full-load and part-load conditions."
            )

        # ── R5: Mid Area (2,000-5,000 sqm) + Low Budget -> Packaged Unit ──
        # Low budget is a hard pre-empt before efficiency/maintenance rules below.
        if (
            not selected_option
            and area_sqm is not None and 2000 <= area_sqm <= 5000
            and budget_category == "LOW"
        ):
            selected_option = "PACKAGED_DX_UNIT"
            confidence = 0.85
            rules_fired.append("RULE_U2_MID_AREA_LOW_BUDGET_PKG")
            reasoning_lines.append(
                f"Mid-size conditioned area ({area_sqm:.0f} sqm, 2,000-5,000 sqm range) with "
                "a LOW budget category. Packaged rooftop DX unit is the most cost-effective "
                "option: single self-contained piece of equipment, minimal installation "
                "complexity, and lowest capital cost in this area bracket."
            )

        # ── STDEFF: Standalone medium-large + high ambient + efficiency priority → VRF ──
        # POC condition 2/3: depends on zoning and maintenance posture.
        # Efficiency-first posture upgrades Packaged DX to VRF.
        if (
            not selected_option
            and store_type in ("STANDALONE", "OFFICE")
            and area_sqm is not None and 2000 <= area_sqm <= 5000
            and ambient_max is not None and ambient_max >= 45
            and efficiency_priority in ("YES", "HIGH")
            and budget_category not in ("LOW",)
        ):
            selected_option = "VRF_SYSTEM"
            confidence = 0.87
            rules_fired.append("RULE_S_MEDLARGE_HIAMB_EFF_VRF")
            reasoning_lines.append(
                f"Standalone medium-large store ({area_sqm:.0f} sqm, 2,000-5,000 sqm range) with "
                f"high ambient ({ambient_max}C) and HIGH efficiency priority. "
                "VRF recommended over packaged DX: superior part-load efficiency (IPLV >= 4.5) under "
                "GCC peak conditions, flexible zone control, and lower lifecycle energy costs."
            )

        # ── STDMAINT: Standalone medium-large + high ambient + high maintenance posture → Packaged DX ──
        # POC condition 2: depends on maintenance posture.
        # High maintenance burden preference favours self-contained packaged DX over VRF.
        if (
            not selected_option
            and store_type in ("STANDALONE", "OFFICE")
            and area_sqm is not None and 2000 <= area_sqm <= 5000
            and ambient_max is not None and ambient_max >= 45
            and maintenance_priority in ("HIGH", "PREMIUM")
            and (zone_count is None or zone_count <= 2)
        ):
            selected_option = "PACKAGED_DX_UNIT"
            confidence = 0.84
            rules_fired.append("RULE_S_MEDLARGE_MAINT_PACKAGED")
            reasoning_lines.append(
                f"Standalone medium-large store ({area_sqm:.0f} sqm) with high ambient ({ambient_max}C) and "
                "HIGH maintenance priority. Packaged rooftop DX unit preferred: self-contained unit, "
                "single field-service point, minimal refrigerant circuit complexity, and lowest "
                "maintenance burden vs VRF -- matching the high maintenance-convenience posture."
            )

        # ── R6: Standalone medium-large (2000-5000 sqm) + High Ambient + Low Zones → Packaged DX ──
        # POC condition 2 fallback when neither efficiency nor maintenance posture fired above.
        if (
            not selected_option
            and store_type in ("STANDALONE", "OFFICE")
            and area_sqm is not None and 2000 <= area_sqm <= 5000
            and ambient_max is not None and ambient_max >= 45
            and (zone_count is None or zone_count <= 2)
        ):
            selected_option = "PACKAGED_DX_UNIT"
            confidence = 0.84
            rules_fired.append("RULE_U3_STANDALONE_MEDLARGE_HIAMB_PACKAGED")
            reasoning_lines.append(
                f"Standalone medium-large store ({area_sqm:.0f} sqm, 2,000-5,000 sqm range) with "
                f"high ambient temperature ({ambient_max}C) and {int(zone_count or 1)} zone(s). "
                "Packaged rooftop DX unit recommended: self-contained, handles high GCC ambient well, "
                "avoids multiple outdoor condensing units, and suits low-zone retail configurations."
            )

        # ── MALL + Chilled Water available -> FCU ─────────────────────────
        # Guard: entire store-type chain is skipped if a universal rule already fired.
        if not selected_option and store_type in ("MALL",) and cw_available == "YES":
            selected_option = "FCU_CHILLED_WATER"
            confidence = 0.95
            rules_fired.append("RULE_M1_MALL_FCU_CW")
            if outdoor_restriction == "YES":
                # Req doc condition 1: Mall + CW + outdoor restriction → FCU (landlord infra fixed)
                rules_fired.append("RULE_M1b_OUTDOOR_RESTRICTION_FORCES_FCU")
                reasoning_lines.append(
                    "Mall store with chilled water backbone available AND outdoor unit restriction in force. "
                    "FCU on chilled water is the only viable option — landlord infrastructure is fixed; "
                    "no outdoor condensing units are permitted. FCU avoids outdoor components entirely."
                )
            else:
                reasoning_lines.append(
                    "Mall store with chilled water backbone available → FCU (Fan Coil Unit) on "
                    "chilled water is the standard approach. No outdoor units required; "
                    "maximises energy efficiency using central plant."
                )

        # ── MALL + No Chilled Water -> VRF (multi-zone) or Split ──────────
        elif not selected_option and store_type in ("MALL",) and cw_available != "YES":
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

        # ── STANDALONE + High Ambient + Multiple Zones -> VRF ─────────────
        elif not selected_option and store_type in ("STANDALONE", "OFFICE") and ambient_max and ambient_max >= 46:
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
                        f"Standalone store, {int(zone_count or 1)} zone(s), load approx "
                        f"{estimated_tr:.1f} TR -> Split systems are cost-effective and widely serviceable."
                    )

        # ── STANDALONE + Normal Ambient ───────────────────────────────────
        elif not selected_option and store_type in ("STANDALONE", "OFFICE"):
            if zone_count and zone_count >= 3 and efficiency_priority in ("YES", "HIGH"):
                # Req doc condition 3: Segmented zones + strong efficiency priority → VRF
                selected_option = "VRF_SYSTEM"
                confidence = 0.87
                rules_fired.append("RULE_S3b_SEGMENTED_ZONES_EFFICIENCY_VRF")
                reasoning_lines.append(
                    f"Standalone store with {int(zone_count)} segmented zones and HIGH efficiency priority. "
                    "VRF with heat recovery recommended: delivers best GCC part-load and full-load efficiency "
                    "(IPLV >= 5.0), enables fully independent zone control, and satisfies ASHRAE 90.1 "
                    "efficiency targets — the required profile for multi-zone, efficiency-driven procurement."
                )
            elif zone_count and zone_count >= 4:
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

        # ── WAREHOUSE + Large Load -> Packaged DX or Chiller ──────────────
        elif not selected_option and store_type == "WAREHOUSE":
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
                _est_tr_str = f"{estimated_tr:.1f} TR" if estimated_tr else "load unknown"
                reasoning_lines.append(
                    f"Small warehouse (area {area_sqm:.0f} sqm, {_est_tr_str}) -> "
                    "Split systems are practical for small conditioned warehouse spaces."
                )

        # ── DATA CENTER ───────────────────────────────────────────────────
        elif not selected_option and store_type == "DATA_CENTER":
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
        elif not selected_option and store_type in ("RESTAURANT",):
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
                        "heat_load_category": heat_load_category,
                        "zone_count_derived": zone_count,
                        "area_sqft": area_sqft_val,
                        "area_sqm_derived": area_sqm,
                        "ambient_temp_max": ambient_max,
                        "cw_derived": cw_available,
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
            # Req doc condition 7: Low budget + compliance mandatory → risk note
            constraints.append({
                "type": "LOW_BUDGET_COMPLIANCE_RISK",
                "detail": (
                    "Low budget category: do NOT downgrade to non-compliant equipment. "
                    f"Minimum standards ({', '.join((applicable_standards or ['ASHRAE 90.1', 'ESMA 5-star'])[:2])}) "
                    "must still be satisfied. Return the minimum compliant option and flag for HVAC engineer validation "
                    "before purchase order is raised."
                ),
            })
            rules_fired.append("RULE_BUDGET_LOW_COMPLIANCE_RISK")

        # ── Compile final result ──────────────────────────────────────────
        system_info = SYSTEM_TYPES.get(selected_option, {})
        recommendation_text = (
            f"{system_info.get('name', selected_option)} — "
            f"{system_info.get('description', '')}"
        )

        full_reasoning = " ".join(reasoning_lines)
        if not full_reasoning:
            full_reasoning = f"Rules engine selected {selected_option} based on: {', '.join(rules_fired)}."

        # ── Alternate option (Req doc 5.5) ────────────────────────────────
        _ALTERNATE_MAP = {
            "FCU_CHILLED_WATER": "VRF_SYSTEM",
            "VRF_SYSTEM": "PACKAGED_DX_UNIT" if budget_category == "LOW" else "SPLIT_SYSTEM",
            "PACKAGED_DX_UNIT": "VRF_SYSTEM" if efficiency_priority in ("YES", "HIGH") else "SPLIT_SYSTEM",
            "CHILLER_PLANT": "PACKAGED_DX_UNIT",
            "CASSETTE_SPLIT": "SPLIT_SYSTEM",
            "SPLIT_SYSTEM": "VRF_SYSTEM" if (zone_count and zone_count >= 2) else None,
        }
        alt_code = _ALTERNATE_MAP.get(selected_option)
        alt_info = SYSTEM_TYPES.get(alt_code, {}) if alt_code else {}
        alternate_option = (
            f"{alt_info.get('name', alt_code)} -- {alt_info.get('description', '')}"
            if alt_code and alt_info else None
        )

        # ── Indicative capacity guidance (Req doc 5.5) ────────────────────
        if estimated_tr:
            if estimated_tr < 5:
                cap_guidance = (
                    f"{estimated_tr:.1f} TR (small -- up to 2 indoor units; "
                    "standard split configuration suitable)"
                )
            elif estimated_tr < 20:
                cap_guidance = (
                    f"{estimated_tr:.1f} TR (medium -- engineering review recommended "
                    "to confirm final design load before specification)"
                )
            elif estimated_tr < 100:
                cap_guidance = (
                    f"{estimated_tr:.1f} TR (large -- HVAC engineer detailed sizing "
                    "mandatory before procurement)"
                )
            else:
                cap_guidance = (
                    f"{estimated_tr:.0f} TR (major plant -- full load analysis, "
                    "tender document, and HVAC engineer sign-off mandatory)"
                )
        else:
            cap_guidance = (
                "Cooling load not calculable -- area data not provided. "
                "HVAC engineer to perform full load analysis before procurement."
            )

        # ── Top decision drivers (Req doc 5.5 -- 3 to 6 human-readable drivers) ─
        _RULE_DRIVER_MAP: Dict[str, str] = {
            "RULE_TECH_ISOLATED_SPLIT": "Small technical room or isolated intervention identified",
            "RULE_U1_SMALL_AREA_SPLIT_AC": "Store area below 2,000 sqm -- split AC most practical",
            "RULE_U2_MID_AREA_LOW_BUDGET_PKG": "Mid-size area with LOW budget -- packaged DX most cost-effective",
            "RULE_S_MEDLARGE_HIAMB_EFF_VRF": "Medium-large standalone with high ambient and efficiency priority -- VRF selected",
            "RULE_S_MEDLARGE_MAINT_PACKAGED": "Medium-large standalone with high maintenance posture -- packaged DX selected",
            "RULE_U3_STANDALONE_MEDLARGE_HIAMB_PACKAGED": "Standalone medium-large store with high ambient, low zone count",
            "RULE_S2_LARGE_STANDALONE_HIEFF_VRF": "Large standalone store (>=5,000 sqm) with high efficiency priority",
            "RULE_M1_MALL_FCU_CW": "Mall store with confirmed chilled water backbone",
            "RULE_M1b_OUTDOOR_RESTRICTION_FORCES_FCU": "Outdoor unit restriction forces FCU on chilled water",
            "RULE_M2_MALL_NO_CW_VRF": "Mall store without chilled water -- multi-zone VRF",
            "RULE_M3_MALL_NO_CW_SPLIT": "Mall store without chilled water -- single/low zone split",
            "RULE_M3_MALL_NO_CW_SPLIT_RESTRICTED": "Mall store with outdoor restriction and no chilled water",
            "RULE_S1_STANDALONE_HIGH_AMB_VRF": "Standalone store with extreme ambient (>= 46C) and multi-zone",
            "RULE_S1b_STANDALONE_HIGH_AMB_VRF": "Standalone store with high ambient, multi-zone VRF required",
            "RULE_S2_STANDALONE_MEDIUM_VRF": "Standalone moderate load with VRF for high ambient performance",
            "RULE_S3_STANDALONE_SMALL_SPLIT": "Small standalone store, single zone -- split systems cost-effective",
            "RULE_S3b_SEGMENTED_ZONES_EFFICIENCY_VRF": "Segmented zones with strong efficiency priority -- VRF heat recovery",
            "RULE_S4_STANDALONE_MULTI_VRF": "Multiple independent zones requiring individual control",
            "RULE_S5_STANDALONE_2ZONE_VRF": "2-zone standalone with standard/premium budget",
            "RULE_S6_STANDALONE_1ZONE_SPLIT": "Single-zone standalone -- split most practical",
            "RULE_W1_WAREHOUSE_CHILLER": "Warehouse with major cooling load (> 200 TR)",
            "RULE_W2_WAREHOUSE_PACKAGED": "Warehouse with medium cooling load (50-200 TR)",
            "RULE_W3_WAREHOUSE_SMALL_SPLIT": "Small warehouse, limited cooling scope",
            "RULE_DC_CHILLER": "Data centre requiring precision cooling with N+1 redundancy",
            "RULE_R1_RESTAURANT_FCU": "Restaurant/F&B with chilled water available",
            "RULE_R2_RESTAURANT_CASSETTE": "Restaurant/F&B without chilled water",
            # Modifier / constraint rules
            "RULE_01_OUTDOOR_RESTRICTION": "Outdoor unit restriction by landlord or authority",
            "RULE_04_HIGH_DUST": "High dust environment -- enhanced filtration mandatory",
            "RULE_05_HIGH_HUMIDITY": "High humidity -- anti-corrosion coil treatment required",
            "RULE_05b_COASTAL_ANTI_CORROSION": "GCC coastal location -- salt-air protection required",
            "RULE_07_EFFICIENCY": "Efficiency priority -- minimum SEER/IPLV thresholds apply",
            "RULE_08_FRESH_AIR_ERV": "Fresh air requirement -- ERV/HRU integration required",
            "RULE_09_HIGH_FOOTFALL": "High footfall -- capacity margin and low-noise system preferred",
            "RULE_BUDGET_LOW_ADJUSTMENT": "LOW budget -- upfront cost of preferred system flagged",
            "RULE_BUDGET_LOW_COMPLIANCE_RISK": "LOW budget -- minimum compliant option must still be met",
        }
        _MODIFIER_PREFIXES = (
            "RULE_04", "RULE_05", "RULE_07", "RULE_08", "RULE_09",
            "RULE_01_OUTDOOR", "RULE_BUDGET_LOW",
        )
        primary_rules = [
            r for r in rules_fired
            if not any(r.startswith(p) for p in _MODIFIER_PREFIXES)
        ]
        top_decision_drivers = [
            _RULE_DRIVER_MAP.get(r, r)
            for r in (primary_rules or rules_fired)[:6]
        ]

        # ── Compliance alignment (Req doc 5.5) ────────────────────────────
        compliance_alignment = {
            "standards": applicable_standards or [],
            "summary": (
                f"Recommendation aligned with: {', '.join((applicable_standards or [])[:4])}. "
                + ("LOW budget: minimum standard compliance must be verified before PO. "
                   if budget_category == "LOW" else "")
                + ("HVAC engineer validation required before procurement action. "
                   if confidence < 0.75 else "")
            ),
        }

        # ── Constraints and assumptions (Req doc 5.5) ─────────────────────
        # Group raw constraints into four human-readable categories.
        _LANDLORD_TYPES = {"OUTDOOR_UNIT_NOT_ALLOWED", "CW_INTEGRATION"}
        _ENV_TYPES = {
            "FILTRATION_REQUIRED", "ANTI_CORROSION_COILS",
            "FRESH_AIR_REQUIRED", "HIGH_FOOTFALL_LOAD_MARGIN",
        }
        _OPS_TYPES = {"DATA_CENTER_REDUNDANCY", "KITCHEN_EXHAUST"}
        _BUDGET_TYPES = {"BUDGET_CONSTRAINT", "LOW_BUDGET_COMPLIANCE_RISK"}
        constraints_and_assumptions = {
            "landlord": [c["detail"] for c in constraints if c["type"] in _LANDLORD_TYPES],
            "environmental": [c["detail"] for c in constraints if c["type"] in _ENV_TYPES],
            "operational": [c["detail"] for c in constraints if c["type"] in _OPS_TYPES],
            "budget": [c["detail"] for c in constraints if c["type"] in _BUDGET_TYPES],
            "assumptions": [
                f"Area: {area_sqft_val:.0f} sqft ({area_sqm:.0f} sqm derived)",
                f"Ambient max: {ambient_max}C (default 45C applied if not provided)",
                f"Chilled water available: {cw_available} (derived from landlord_constraints text)",
                f"Outdoor unit restriction: {outdoor_restriction} (derived from landlord_constraints text)",
                f"Zone count proxy: {int(zone_count)} (derived from heat_load_category={heat_load_category})",
                (
                    f"Estimated cooling load: {estimated_tr:.1f} TR (area x 130 W/sqm GCC rule)"
                    if estimated_tr else "Cooling load estimate not available (area not specified)"
                ),
            ],
        }

        # ── Required human validation (Req doc 5.5) ───────────────────────
        required_human_validation = (
            confidence < 0.75
            or (estimated_tr is not None and estimated_tr > 100)
            or (ambient_max is not None and ambient_max >= 48)
            or store_type == "DATA_CENTER"
            or budget_category == "LOW"
            or fresh_air_requirement in ("YES", "HIGH", "REQUIRED")
        )

        return {
            "recommended_option": recommendation_text,
            "system_type_code": selected_option,
            "reasoning_summary": full_reasoning,
            "confident": True,
            "confidence": round(confidence, 3),
            "confidence_score_100": round(confidence * 100),
            "constraints": constraints,
            "constraints_and_assumptions": constraints_and_assumptions,
            "compliance_alignment": compliance_alignment,
            "alternate_option": alternate_option,
            "indicative_capacity_guidance": cap_guidance,
            "top_decision_drivers": top_decision_drivers,
            "required_human_validation": required_human_validation,
            "reasoning_details": {
                "source": "rules_engine",
                "rules_evaluated": len(rules_fired),
                "rules_fired": rules_fired,
                "inputs": {
                    "store_type": store_type,
                    "store_format": store_format,
                    "heat_load_category": heat_load_category,
                    "zone_count_derived": zone_count,
                    "area_sqft": area_sqft_val,
                    "area_sqm_derived": round(area_sqm, 1),
                    "ambient_temp_max": ambient_max,
                    "chilled_water_derived": cw_available,
                    "outdoor_restriction_derived": outdoor_restriction,
                    "energy_efficiency_priority": efficiency_priority,
                    "maintenance_priority": maintenance_priority,
                    "footfall_category": footfall_category,
                    "fresh_air_requirement": fresh_air_requirement,
                    "dust_exposure": dust_level,
                    "humidity_level": humidity_level,
                    "budget_level": budget_category,
                    "estimated_cooling_tr": round(estimated_tr, 1) if estimated_tr else None,
                },
                "applicable_standards": applicable_standards,
                "system_type": system_info,
            },
        }
