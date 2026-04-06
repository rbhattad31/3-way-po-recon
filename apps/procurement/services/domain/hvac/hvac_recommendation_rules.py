"""Deterministic HVAC recommendation rules engine."""
from __future__ import annotations

from typing import Any, Dict, List


class HVACRecommendationRules:
    """Rule-based HVAC product selection recommendation."""

    @staticmethod
    def evaluate(attrs: Dict[str, Any]) -> Dict[str, Any]:
        archetype = HVACRecommendationRules._detect_archetype(attrs)
        drivers: List[str] = []
        constraints: List[str] = []
        assumptions: List[str] = []
        notes: List[str] = []

        store_type = str(attrs.get("store_type") or "").upper()
        budget_level = str(attrs.get("budget_level") or "MEDIUM").upper()
        heat_load = str(attrs.get("heat_load_category") or "MEDIUM").upper()
        humidity = str(attrs.get("humidity_level") or "MEDIUM").upper()
        dust = str(attrs.get("dust_exposure") or "MEDIUM").upper()
        efficiency_priority = str(attrs.get("energy_efficiency_priority") or "MEDIUM").upper()
        landlord = str(attrs.get("landlord_constraints") or "").lower()
        ambient = float(attrs.get("ambient_temp_max_c") or 0)

        multiple_options = False

        recommended_system = "PACKAGED_DX"
        alternate_option = {
            "system_type": "VRF",
            "reason": "Alternative for zoned control and staged expansion.",
        }

        if archetype == "mall_interface" and ("no outdoor" in landlord or "no condenser" in landlord):
            recommended_system = "CHILLED_WATER_INTERFACE"
            alternate_option = {
                "system_type": "VRF",
                "reason": "Only if landlord allows outdoor condenser provision.",
            }
            drivers.append("Mall tenancy with landlord restrictions on outdoor units.")

        elif archetype == "standalone_retail" and ambient >= 45:
            recommended_system = "PACKAGED_DX"
            alternate_option = {
                "system_type": "VRF",
                "reason": "VRF can improve partial-load efficiency if CAPEX permits.",
            }
            drivers.append("High ambient conditions favor robust packaged DX deployment.")
            multiple_options = True

        elif efficiency_priority == "HIGH":
            recommended_system = "VRF"
            alternate_option = {
                "system_type": "PACKAGED_DX",
                "reason": "Lower upfront CAPEX where zoning flexibility is less critical.",
            }
            drivers.append("High energy-efficiency priority and zoning suitability.")
            multiple_options = True

        area = float(attrs.get("area_sq_ft") or 0)
        if area and area < 4000 and heat_load == "LOW":
            recommended_system = "SPLIT_SYSTEM"
            alternate_option = {
                "system_type": "VRF",
                "reason": "Upgrade path when multi-zone expansion is expected.",
            }
            drivers.append("Small scope with low heat load supports split-system simplicity.")

        if dust == "HIGH":
            notes.append("High dust exposure: include higher-grade pre-filtration and maintenance schedule.")
            constraints.append("Filtration class and coil protection must be explicitly specified.")

        if humidity == "HIGH":
            notes.append("High humidity: include anti-corrosion coating and latent-load control strategy.")
            constraints.append("Corrosion-protected coils/components required.")

        if budget_level == "LOW":
            assumptions.append("Low budget level selected; recommendation keeps minimum compliant baseline without downgrading compliance.")

        capacity_band = HVACRecommendationRules._capacity_band(attrs)
        confidence = HVACRecommendationRules._confidence(attrs, multiple_options)
        human_validation_required = confidence < 0.88 or multiple_options or bool(attrs.get("normalization_issues"))

        reasoning_summary = (
            f"Recommended {recommended_system} for archetype '{archetype}' with capacity band {capacity_band}. "
            "Indicative only. Final design required."
        )

        return {
            "recommended_option": f"{recommended_system} ({capacity_band})",
            "recommended_system_type": recommended_system,
            "capacity_band": capacity_band,
            "decision_drivers": drivers,
            "constraints": constraints,
            "assumptions": assumptions,
            "notes": notes,
            "archetype": archetype,
            "alternate_option": alternate_option,
            "reasoning_summary": reasoning_summary,
            "reasoning_details": {
                "rule_engine": "HVAC_RULES_V1",
                "archetype": archetype,
                "capacity_note": "Indicative only. Final design required.",
            },
            "confidence": confidence,
            "confidence_score": confidence,
            "confident": confidence >= 0.8,
            "requires_ai_reasoning": multiple_options,
            "human_validation_required": human_validation_required,
            "constraints_and_assumptions": constraints + assumptions,
        }

    @staticmethod
    def _detect_archetype(attrs: Dict[str, Any]) -> str:
        store_type = str(attrs.get("store_type") or "").upper()
        format_code = str(attrs.get("store_format") or "").upper()
        heat_load = str(attrs.get("heat_load_category") or "").upper()
        area = float(attrs.get("area_sq_ft") or 0)
        existing_hvac = str(attrs.get("existing_hvac_type") or "").strip().lower()

        if existing_hvac:
            return "retrofit_replacement"
        if store_type == "MALL":
            return "mall_interface"
        if format_code in {"HYPERMARKET", "FURNITURE"} or (area >= 20000 and heat_load == "HIGH"):
            return "high_load_large_format"
        return "standalone_retail"

    @staticmethod
    def _capacity_band(attrs: Dict[str, Any]) -> str:
        area = float(attrs.get("area_sq_ft") or 0)
        heat_load = str(attrs.get("heat_load_category") or "MEDIUM").upper()

        multiplier = {"LOW": 1.0, "MEDIUM": 1.2, "HIGH": 1.5}.get(heat_load, 1.2)
        tr = (area / 500.0) * multiplier if area > 0 else 0

        if tr < 5:
            return "<5 TR"
        if tr < 10:
            return "5–10 TR"
        if tr < 20:
            return "10–20 TR"
        if tr < 40:
            return "20–40 TR"
        return "40+ TR"

    @staticmethod
    def _confidence(attrs: Dict[str, Any], multiple_options: bool) -> float:
        required_fields = [
            "store_id",
            "brand",
            "country",
            "city",
            "store_type",
            "store_format",
            "area_sq_ft",
            "ceiling_height_ft",
            "ambient_temp_max_c",
            "humidity_level",
            "dust_exposure",
            "heat_load_category",
            "landlord_constraints",
            "budget_level",
        ]
        filled = sum(1 for field in required_fields if attrs.get(field) not in (None, ""))
        completeness = filled / max(len(required_fields), 1)

        ambiguity_penalty = 0.12 if multiple_options else 0.0
        if attrs.get("normalization_issues"):
            ambiguity_penalty += 0.08

        confidence = completeness * 0.92 - ambiguity_penalty
        return max(0.2, min(0.98, round(confidence, 2)))
