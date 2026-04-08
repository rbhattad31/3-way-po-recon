"""ReasonSummaryAgent
====================
Deterministic agent that transforms a persisted RecommendationResult
(including its output_payload_json and reasoning_details_json) into a
richly-structured explanation suitable for display in the workspace UI.

No LLM call is made - this is pure parsing/formatting so it is always fast
and always available even without an OpenAI key.

Usage
-----
    from apps.procurement.agents.reason_summary_agent import ReasonSummaryAgent
    ctx = ReasonSummaryAgent.generate(recommendation_result)

The returned dict has these top-level keys:

    headline          (str)  -- one-liner "why this system"
    system_name       (str)  -- short product name
    system_code       (str)  -- e.g. "FCU_CHILLED_WATER"
    system_description(str)  -- full product description
    reasoning_summary (str)  -- paragraph from the engine
    confidence_pct    (int)  -- 0-100
    compliance_status (str)  -- PASS / FAIL / NOT_CHECKED
    capacity_guidance (str)
    human_validation  (bool)
    alternate_option  (str | None) -- alternate system name
    top_drivers       list[str]    -- bullet-point decision drivers
    rules_table       list[dict]   -- {code, description, role, role_class}
    conditions_table  list[dict]   -- {factor, value, derived, impact, highlight}
    alternatives_table list[dict]  -- {system, reason}
    constraints       dict         -- {landlord, environmental, operational, budget}
    assumptions       list[str]
    thought_steps     list[dict]   -- {step, stage, decision, reasoning}
    standards         list[str]
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Human-readable labels for input keys shown in the Conditions table
# ---------------------------------------------------------------------------
_FIELD_LABELS: Dict[str, str] = {
    "store_type":                  "Store / Facility Type",
    "store_format":                "Store Format",
    "area_sqft":                   "Area (sqft)",
    "area_sqm_derived":            "Area (sqm, derived)",
    "ambient_temp_max":            "Max Ambient Temp (C)",
    "chilled_water_derived":       "Chilled Water Available",
    "outdoor_restriction_derived": "Outdoor Unit Restriction",
    "energy_efficiency_priority":  "Energy Efficiency Priority",
    "maintenance_priority":        "Maintenance Priority",
    "heat_load_category":          "Heat Load Category",
    "zone_count_derived":          "Zone Count (derived)",
    "estimated_cooling_tr":        "Estimated Cooling Load (TR)",
    "dust_exposure":               "Dust Exposure",
    "humidity_level":              "Humidity Level",
    "budget_level":                "Budget Level",
    "footfall_category":           "Footfall Category",
    "fresh_air_requirement":       "Fresh Air Requirement",
}

# Impact description per factor
_FIELD_IMPACT: Dict[str, str] = {
    "store_type":                  "Primary routing: determines which rule chain fires",
    "area_sqft":                   "Drives size-category classification (small/mid/large)",
    "area_sqm_derived":            "Internal sqm used by rule thresholds (2000 / 5000 sqm bands)",
    "ambient_temp_max":            "High ambient (>=45C) activates GCC desert-heat rules",
    "chilled_water_derived":       "CW='YES' routes MALL to FCU; 'NO' routes to VRF/Split",
    "outdoor_restriction_derived": "Forces FCU or cassette when ODU cannot be installed",
    "energy_efficiency_priority":  "'HIGH' upgrades PACKAGED -> VRF for lifecycle savings",
    "maintenance_priority":        "'HIGH' prefers single-point packaged units over VRF",
    "heat_load_category":          "Used as proxy for zone count (LOW=1, MEDIUM=2, HIGH=3+)",
    "zone_count_derived":          "Multi-zone (>=3) triggers VRF preference",
    "estimated_cooling_tr":        ">200 TR triggers chiller plant; 50-200 TR -> packaged DX",
    "dust_exposure":               "'HIGH' adds filtration constraints; does NOT change system",
    "humidity_level":              "'HIGH' adds anti-corrosion constraints",
    "budget_level":                "'LOW' prevents VRF/Chiller upgrade even if efficiency flags",
    "footfall_category":           "High footfall adds capacity margin advice",
    "fresh_air_requirement":       "'YES' or 'HIGH' triggers ERV/HRV integration constraint",
}

# Whether a field value DIRECTLY changed the system selection
_SYSTEM_DECIDING_FIELDS = {
    "store_type", "area_sqft", "area_sqm_derived", "ambient_temp_max",
    "chilled_water_derived", "outdoor_restriction_derived",
    "zone_count_derived", "estimated_cooling_tr", "budget_level",
    "energy_efficiency_priority", "maintenance_priority", "heat_load_category",
}

# Rule roles
_MODIFIER_PREFIXES = (
    "RULE_04", "RULE_05", "RULE_07", "RULE_08", "RULE_09",
    "RULE_01_OUTDOOR", "RULE_BUDGET_LOW",
)
_RULE_DESCRIPTIONS: Dict[str, str] = {
    "RULE_TECH_ISOLATED_SPLIT":              "Small technical room or isolated intervention (<300 sqft)",
    "RULE_U1_SMALL_AREA_SPLIT_AC":          "Store area below 2,000 sqm -- split AC most practical",
    "RULE_U2_MID_AREA_LOW_BUDGET_PKG":      "Mid-size area with LOW budget -- packaged DX most cost-effective",
    "RULE_S_MEDLARGE_HIAMB_EFF_VRF":        "Medium-large standalone: high ambient + HIGH efficiency -> VRF",
    "RULE_S_MEDLARGE_MAINT_PACKAGED":       "Medium-large standalone: HIGH maintenance posture -> packaged DX",
    "RULE_U3_STANDALONE_MEDLARGE_HIAMB_PACKAGED": "Standalone medium-large, high ambient, low zone count",
    "RULE_S2_LARGE_STANDALONE_HIEFF_VRF":   "Large standalone (>=5,000 sqm) with high efficiency priority",
    "RULE_M1_MALL_FCU_CW":                  "Mall store with confirmed chilled water backbone -> FCU",
    "RULE_M1b_OUTDOOR_RESTRICTION_FORCES_FCU": "Outdoor unit restriction forces FCU on chilled water",
    "RULE_M2_MALL_NO_CW_VRF":              "Mall store without chilled water -- multi-zone VRF",
    "RULE_M3_MALL_NO_CW_SPLIT":             "Mall store without chilled water -- single/low zone -> split",
    "RULE_M3_MALL_NO_CW_SPLIT_RESTRICTED":  "Mall store with outdoor restriction and no chilled water",
    "RULE_S1_STANDALONE_HIGH_AMB_VRF":      "Standalone extreme ambient (>=46C) and multi-zone -> VRF",
    "RULE_S1b_STANDALONE_HIGH_AMB_VRF":     "Standalone high ambient, multi-zone VRF required",
    "RULE_S2_STANDALONE_MEDIUM_VRF":        "Standalone moderate load with VRF for high ambient performance",
    "RULE_S3_STANDALONE_SMALL_SPLIT":       "Small standalone store, single zone -- split systems optimal",
    "RULE_S3b_SEGMENTED_ZONES_EFFICIENCY_VRF": "Segmented zones + strong efficiency -> VRF heat recovery",
    "RULE_S4_STANDALONE_MULTI_VRF":         "Multiple independent zones requiring individual control",
    "RULE_S5_STANDALONE_2ZONE_VRF":         "2-zone standalone with medium/high budget",
    "RULE_S6_STANDALONE_1ZONE_SPLIT":       "Single-zone standalone -- split most practical",
    "RULE_W1_WAREHOUSE_CHILLER":            "Warehouse major cooling load (>200 TR) -> chiller plant",
    "RULE_W2_WAREHOUSE_PACKAGED":           "Warehouse medium load (50-200 TR) -> packaged DX",
    "RULE_W3_WAREHOUSE_SMALL_SPLIT":        "Small warehouse limited cooling scope -> split",
    "RULE_DC_CHILLER":                      "Data centre -- precision cooling with N+1 redundancy",
    "RULE_R1_RESTAURANT_FCU":               "Restaurant/F&B with chilled water available",
    "RULE_R2_RESTAURANT_CASSETTE":          "Restaurant/F&B without chilled water",
    "RULE_01_OUTDOOR_RESTRICTION":          "Outdoor unit restriction by landlord or authority",
    "RULE_04_HIGH_DUST":                    "High dust environment -- enhanced G4+F7 filtration mandatory",
    "RULE_05_HIGH_HUMIDITY":                "High humidity -- anti-corrosion coil treatment required",
    "RULE_05b_COASTAL_ANTI_CORROSION":      "GCC coastal location -- salt-air protection required",
    "RULE_07_EFFICIENCY":                   "Efficiency priority -- minimum SEER/IPLV thresholds apply",
    "RULE_08_FRESH_AIR_ERV":                "Fresh air requirement -- ERV/HRU integration required",
    "RULE_09_HIGH_FOOTFALL":                "High footfall -- capacity margin and low-noise preferred",
    "RULE_BUDGET_LOW_ADJUSTMENT":           "LOW budget -- upfront cost of preferred system flagged",
    "RULE_BUDGET_LOW_COMPLIANCE_RISK":      "LOW budget -- minimum compliant option must still be met",
}

# System display names
_SYSTEM_NAMES: Dict[str, str] = {
    "FCU_CHILLED_WATER":  "Fan Coil Unit on Chilled Water (FCU-CW)",
    "VRF_SYSTEM":         "Variable Refrigerant Flow System (VRF/VRV)",
    "PACKAGED_DX_UNIT":   "Packaged Rooftop DX Unit",
    "SPLIT_SYSTEM":       "Split Air Conditioning System",
    "CASSETTE_SPLIT":     "Ceiling Cassette Split System",
    "CHILLER_PLANT":      "Chiller Plant (Air/Water Cooled)",
    "AHU_DUCTED":         "Air Handling Unit (AHU) - Ducted",
    "ERV":                "Energy Recovery Ventilation Unit (ERV/HRU)",
}

# Highlight class per field value
def _highlight(field: str, value: Any) -> str:
    """Return Bootstrap CSS class for the value cell."""
    v = str(value).upper()
    if field == "chilled_water_derived":
        return "text-success fw-semibold" if v == "YES" else "text-muted"
    if field == "outdoor_restriction_derived":
        return "text-warning fw-semibold" if v == "YES" else "text-muted"
    if field == "ambient_temp_max":
        try:
            if float(value) >= 46:
                return "text-danger fw-semibold"
        except (TypeError, ValueError):
            pass
    if field == "budget_level":
        return "text-danger fw-semibold" if v == "LOW" else (
            "text-warning fw-semibold" if v == "MEDIUM" else "text-success fw-semibold"
        )
    if field == "energy_efficiency_priority":
        return "text-success fw-semibold" if v == "HIGH" else "text-muted"
    if field == "maintenance_priority":
        return "text-warning fw-semibold" if v == "HIGH" else "text-muted"
    if field in _SYSTEM_DECIDING_FIELDS:
        return "text-dark"
    return "text-muted"


class ReasonSummaryAgent:
    """Deterministic agent that explains why a recommendation was made.

    No LLM call - purely parses the persisted output_payload_json.
    """

    @staticmethod
    def generate(result) -> Dict[str, Any]:
        """Generate a structured explanation dict from a RecommendationResult.

        Parameters
        ----------
        result : RecommendationResult ORM object

        Returns
        -------
        dict  -- keys described in module docstring
        """
        try:
            payload: Dict[str, Any] = result.output_payload_json or {}
            details: Dict[str, Any] = result.reasoning_details_json or {}

            # If reasoning_details_json is empty, fall back to nested key in payload
            if not details:
                details = payload.get("reasoning_details", {})
            else:
                # reasoning_details_json may itself BE the inner reasoning_details dict
                if "source" not in details and "rules_fired" not in details:
                    details = payload.get("reasoning_details", {})

            inputs: Dict[str, Any] = details.get("inputs", {})
            rules_fired: List[str] = details.get("rules_fired", [])
            system_info: Dict[str, Any] = details.get("system_type", {})
            applicable_standards: List[str] = details.get("applicable_standards", [])

            system_code: str = payload.get("system_type_code", "") or ""
            system_name: str = (
                system_info.get("name", "")
                or _SYSTEM_NAMES.get(system_code, system_code)
            )
            system_description: str = system_info.get("description", "")

            # Extract clean product name from recommended_option (strip the long description)
            raw_rec_option: str = result.recommended_option or payload.get("recommended_option", "")
            if " -- " in raw_rec_option:
                system_display = raw_rec_option.split(" -- ")[0].strip()
            elif " - " in raw_rec_option:
                system_display = raw_rec_option.split(" - ")[0].strip()
            elif " (" in raw_rec_option and system_code:
                system_display = system_name or raw_rec_option
            else:
                system_display = system_name or raw_rec_option

            reasoning_summary: str = result.reasoning_summary or payload.get("reasoning_summary", "")
            confidence: float = float(result.confidence_score or payload.get("confidence", 0.0))
            confidence_pct: int = round(confidence * 100)

            top_drivers: List[str] = payload.get("top_decision_drivers", [])
            alternate_raw: Optional[str] = payload.get("alternate_option")
            cap_guidance: str = payload.get("indicative_capacity_guidance", "")
            human_validation: bool = bool(payload.get("required_human_validation", False))

            # -- Headline (one-liner explanation) --------------------------------
            headline = ReasonSummaryAgent._build_headline(
                system_code, system_display, inputs, rules_fired, payload
            )

            # -- Rules table -----------------------------------------------------
            rules_table = ReasonSummaryAgent._build_rules_table(rules_fired)

            # -- Conditions table ------------------------------------------------
            conditions_table = ReasonSummaryAgent._build_conditions_table(inputs)

            # -- Alternatives table ----------------------------------------------
            alternatives_table = ReasonSummaryAgent._build_alternatives_table(
                system_code, alternate_raw, payload, inputs
            )

            # -- Constraints & assumptions ----------------------------------------
            raw_c_and_a = payload.get("constraints_and_assumptions", {})
            if isinstance(raw_c_and_a, dict):
                constraints = {
                    "landlord":     raw_c_and_a.get("landlord", []),
                    "environmental": raw_c_and_a.get("environmental", []),
                    "operational":  raw_c_and_a.get("operational", []),
                    "budget":       raw_c_and_a.get("budget", []),
                }
                assumptions = raw_c_and_a.get("assumptions", [])
            else:
                raw_constraints = payload.get("constraints", [])
                flat = [
                    c["detail"] if isinstance(c, dict) else str(c)
                    for c in (raw_constraints or [])
                ]
                constraints = {"landlord": [], "environmental": flat, "operational": [], "budget": []}
                assumptions = []

            # -- Thought steps from run.thought_process_log ----------------------
            thought_steps = []
            try:
                run = result.run
                log = getattr(run, "thought_process_log", None) or []
                for step in log:
                    if isinstance(step, dict):
                        thought_steps.append({
                            "step": step.get("step", ""),
                            "stage": step.get("stage", ""),
                            "decision": step.get("decision", ""),
                            "reasoning": step.get("reasoning", ""),
                        })
            except Exception:
                pass

            return {
                "headline":          headline,
                "system_name":       system_display,
                "system_code":       system_code,
                "system_description": system_description or raw_rec_option,
                "reasoning_summary": reasoning_summary,
                "confidence_pct":    confidence_pct,
                "compliance_status": result.compliance_status or "NOT_CHECKED",
                "capacity_guidance": cap_guidance,
                "human_validation":  human_validation,
                "alternate_option":  alternate_raw,
                "top_drivers":       top_drivers,
                "rules_table":       rules_table,
                "conditions_table":  conditions_table,
                "alternatives_table": alternatives_table,
                "constraints":       constraints,
                "assumptions":       assumptions,
                "thought_steps":     thought_steps,
                "standards":         applicable_standards,
            }

        except Exception as exc:
            logger.warning("ReasonSummaryAgent.generate failed: %s", exc, exc_info=True)
            return {
                "headline":          "Reasoning summary unavailable.",
                "system_name":       getattr(result, "recommended_option", "")[:60],
                "system_code":       "",
                "system_description": "",
                "reasoning_summary": getattr(result, "reasoning_summary", ""),
                "confidence_pct":    round(float(getattr(result, "confidence_score", 0.0) or 0) * 100),
                "compliance_status": getattr(result, "compliance_status", "NOT_CHECKED"),
                "capacity_guidance": "",
                "human_validation":  False,
                "alternate_option":  None,
                "top_drivers":       [],
                "rules_table":       [],
                "conditions_table":  [],
                "alternatives_table": [],
                "constraints":       {},
                "assumptions":       [],
                "thought_steps":     [],
                "standards":         [],
            }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headline(
        system_code: str,
        system_display: str,
        inputs: Dict[str, Any],
        rules_fired: List[str],
        payload: Dict[str, Any],
    ) -> str:
        store_type = str(inputs.get("store_type") or "").title()
        area_sqm = inputs.get("area_sqm_derived", 0) or 0
        area_sqft = inputs.get("area_sqft") or 0
        ambient = inputs.get("ambient_temp_max")
        cw = str(inputs.get("chilled_water_derived") or "NO").upper()

        # Build context clause
        clauses = []
        if store_type:
            clauses.append(store_type.replace("_", " ").title() + " facility")
        if area_sqm:
            clauses.append(f"{area_sqm:,.0f} sqm floor area")
        if ambient:
            clauses.append(f"max ambient {ambient}C")
        if cw == "YES":
            clauses.append("chilled water infrastructure available")

        context_str = (", ".join(clauses) + " -- ") if clauses else ""

        # Primary rule
        primary_rules = [
            r for r in rules_fired
            if not any(r.startswith(p) for p in _MODIFIER_PREFIXES)
        ]
        rule_desc = ""
        if primary_rules:
            rule_desc = _RULE_DESCRIPTIONS.get(primary_rules[0], primary_rules[0].replace("_", " ").title())

        if rule_desc:
            return f"{context_str}{system_display} selected: {rule_desc.lower()}."
        elif system_display:
            return f"{context_str}{system_display} selected based on deterministic rule matching."
        return "Recommendation generated by the HVAC rules engine."

    @staticmethod
    def _build_rules_table(rules_fired: List[str]) -> List[Dict[str, str]]:
        rows = []
        for code in rules_fired:
            is_modifier = any(code.startswith(p) for p in _MODIFIER_PREFIXES)
            rows.append({
                "code":        code,
                "description": _RULE_DESCRIPTIONS.get(code, code.replace("_", " ").title()),
                "role":        "Modifier / Constraint" if is_modifier else "Primary Selection",
                "role_class":  "text-muted" if is_modifier else "text-success fw-semibold",
                "badge_class": "bg-secondary" if is_modifier else "bg-success",
            })
        return rows

    @staticmethod
    def _build_conditions_table(inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = []
        DISPLAY_ORDER = [
            "store_type", "store_format", "area_sqft", "area_sqm_derived",
            "ambient_temp_max", "heat_load_category", "zone_count_derived",
            "estimated_cooling_tr", "chilled_water_derived",
            "outdoor_restriction_derived", "budget_level",
            "energy_efficiency_priority", "maintenance_priority",
            "dust_exposure", "humidity_level", "footfall_category",
            "fresh_air_requirement",
        ]
        seen = set()
        for key in DISPLAY_ORDER:
            val = inputs.get(key)
            if val is None:
                continue
            seen.add(key)
            derived = key.endswith("_derived") or key in (
                "area_sqm_derived", "zone_count_derived", "estimated_cooling_tr",
                "chilled_water_derived", "outdoor_restriction_derived"
            )
            rows.append({
                "factor":    _FIELD_LABELS.get(key, key.replace("_", " ").title()),
                "value":     val,
                "derived":   derived,
                "impact":    _FIELD_IMPACT.get(key, "Informational"),
                "highlight": _highlight(key, val),
                "is_key":    key in _SYSTEM_DECIDING_FIELDS,
            })
        # Remaining keys not in display order
        for key, val in inputs.items():
            if key in seen or val is None:
                continue
            rows.append({
                "factor":    _FIELD_LABELS.get(key, key.replace("_", " ").title()),
                "value":     val,
                "derived":   False,
                "impact":    _FIELD_IMPACT.get(key, "Informational"),
                "highlight": _highlight(key, val),
                "is_key":    key in _SYSTEM_DECIDING_FIELDS,
            })
        return rows

    @staticmethod
    def _build_alternatives_table(
        selected_code: str,
        alternate_raw: Optional[str],
        payload: Dict[str, Any],
        inputs: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        rows = []
        # Add the alternate from the engine
        if alternate_raw:
            alt_name = alternate_raw.split(" -- ")[0].strip() if " -- " in alternate_raw else alternate_raw
            rows.append({
                "system":         alt_name,
                "reason_rejected": (
                    alternate_raw.split(" -- ", 1)[1].strip()
                    if " -- " in alternate_raw
                    else "Not optimal for the given parameters."
                ),
                "reason_class":   "text-muted",
            })

        # Budget-based exclusions
        budget = str(inputs.get("budget_level") or "").upper()
        if budget == "LOW" and selected_code in ("PACKAGED_DX_UNIT", "SPLIT_SYSTEM"):
            for excluded in ("VRF_SYSTEM", "CHILLER_PLANT", "FCU_CHILLED_WATER"):
                if excluded != selected_code and excluded not in [r["system"] for r in rows]:
                    rows.append({
                        "system":          _SYSTEM_NAMES.get(excluded, excluded),
                        "reason_rejected": "Excluded: LOW budget level cannot support higher CAPEX system.",
                        "reason_class":    "text-warning",
                    })
                    break

        # CW-based exclusion
        cw = str(inputs.get("chilled_water_derived") or "NO").upper()
        if cw == "NO" and "FCU_CHILLED_WATER" != selected_code:
            rows.append({
                "system":          _SYSTEM_NAMES.get("FCU_CHILLED_WATER", "FCU Chilled Water"),
                "reason_rejected": "Not viable: chilled water infrastructure not confirmed in landlord constraints.",
                "reason_class":    "text-danger",
            })

        return rows
