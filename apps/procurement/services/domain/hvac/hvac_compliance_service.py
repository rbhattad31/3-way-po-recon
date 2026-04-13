"""HVAC compliance checks and alignment output."""
from __future__ import annotations

from typing import Any, Dict, List

from apps.core.enums import ComplianceStatus


class HVACComplianceService:
    """Domain-specific HVAC compliance checks (ASHRAE + local suitability signals)."""

    @staticmethod
    def check(attrs: Dict[str, Any], recommendation: Dict[str, Any]) -> Dict[str, Any]:
        rules_checked: List[Dict[str, str]] = []
        violations: List[Dict[str, str]] = []
        recommendations: List[str] = []

        system_type = str(
            recommendation.get("recommended_system_type")
            or recommendation.get("recommended_option")
            or ""
        )
        humidity = str(attrs.get("humidity_level") or "").upper()
        dust = str(attrs.get("dust_exposure") or "").upper()
        fresh_air = str(attrs.get("fresh_air_requirement") or "").upper()
        standards_notes = str(attrs.get("required_standards_local_notes") or "").strip()

        notes_raw = recommendation.get("notes")
        notes_parts: List[str] = []
        if isinstance(notes_raw, list):
            notes_parts.extend(str(item) for item in notes_raw if item)
        elif notes_raw:
            notes_parts.append(str(notes_raw))

        for text_key in ("reasoning_summary", "compliance_notes", "market_notes"):
            value = recommendation.get(text_key)
            if value:
                notes_parts.append(str(value))

        constraints_raw = recommendation.get("constraints") or []
        constraint_parts: List[str] = []
        if isinstance(constraints_raw, list):
            for constraint in constraints_raw:
                if isinstance(constraint, dict):
                    c_type = str(constraint.get("type") or "")
                    c_detail = str(constraint.get("detail") or "")
                    if c_type:
                        constraint_parts.append(c_type)
                    if c_detail:
                        constraint_parts.append(c_detail)
                elif constraint:
                    constraint_parts.append(str(constraint))

        evidence_text = " ".join(notes_parts + constraint_parts).lower()

        rules_checked.append({"rule": "ashrae_alignment", "description": "ASHRAE-aligned system recommendation present"})
        if not system_type:
            violations.append({"rule": "ashrae_alignment", "detail": "No HVAC system type recommended"})

        rules_checked.append({"rule": "ventilation_consideration", "description": "Fresh-air requirement considered"})
        if fresh_air == "HIGH" and system_type in {"SPLIT_SYSTEM"}:
            violations.append({
                "rule": "ventilation_consideration",
                "detail": "High fresh-air requirement may need DOAS/ventilation augmentation.",
            })
            recommendations.append("Add dedicated outside air/ventilation strategy in final engineering design.")

        rules_checked.append({"rule": "humidity_suitability", "description": "Humidity suitability controls considered"})
        if humidity == "HIGH":
            has_humidity_control = any(
                token in evidence_text
                for token in [
                    "anti-corrosion",
                    "anti corrosion",
                    "dehumidification",
                    "dehumidifier",
                    "epoxy-coated",
                    "blue-fin",
                ]
            )
            if not has_humidity_control:
                violations.append({
                    "rule": "humidity_suitability",
                    "detail": "High humidity detected but anti-corrosion/dehumidification notes are missing.",
                })

        rules_checked.append({"rule": "dust_suitability", "description": "Dust protection and filtration considered"})
        if dust == "HIGH":
            has_dust_control = any(
                token in evidence_text
                for token in ["filtration", "filter", "merv", "hepa"]
            )
            if not has_dust_control:
                violations.append({
                    "rule": "dust_suitability",
                    "detail": "High dust exposure detected but filtration note is missing.",
                })

        rules_checked.append({"rule": "local_guidelines", "description": "Local notes captured for authority review"})
        if not standards_notes:
            recommendations.append("Capture local authority/landlord guideline references before IFC stage.")

        hvac_alignment = "FULL"
        if violations:
            hvac_alignment = "REVIEW_REQUIRED" if len(violations) >= 2 else "PARTIAL"

        compliance_status = ComplianceStatus.PASS
        if hvac_alignment == "PARTIAL":
            compliance_status = ComplianceStatus.PARTIAL
        elif hvac_alignment == "REVIEW_REQUIRED":
            compliance_status = ComplianceStatus.FAIL

        return {
            "status": compliance_status,
            "hvac_alignment": hvac_alignment,
            "rules_checked": rules_checked,
            "violations": violations,
            "recommendations": recommendations,
        }
