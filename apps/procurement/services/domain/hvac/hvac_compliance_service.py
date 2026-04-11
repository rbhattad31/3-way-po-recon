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

        system_type = str(recommendation.get("recommended_system_type") or "")
        humidity = str(attrs.get("humidity_level") or "").upper()
        dust = str(attrs.get("dust_exposure") or "").upper()
        fresh_air = str(attrs.get("fresh_air_requirement") or "").upper()
        standards_notes = str(attrs.get("required_standards_local_notes") or "").strip()

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
            if "anti-corrosion" not in " ".join(recommendation.get("notes") or []).lower():
                violations.append({
                    "rule": "humidity_suitability",
                    "detail": "High humidity detected but anti-corrosion/dehumidification notes are missing.",
                })

        rules_checked.append({"rule": "dust_suitability", "description": "Dust protection and filtration considered"})
        if dust == "HIGH":
            if "filtration" not in " ".join(recommendation.get("notes") or []).lower():
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
