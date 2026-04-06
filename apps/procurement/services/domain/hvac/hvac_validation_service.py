"""HVAC validation helpers integrated into the existing validation pipeline."""
from __future__ import annotations

from typing import Any, Dict, List

from apps.core.enums import (
    ValidationItemStatus,
    ValidationSeverity,
    ValidationSourceType,
    ValidationType,
)
from apps.procurement.domain.hvac.schema import get_hvac_attribute_definitions
from apps.procurement.models import ProcurementRequest


class HVACValidationService:
    """Domain checks for HVAC request quality and ambiguity."""

    @staticmethod
    def validate_request(request: ProcurementRequest, attrs: Dict[str, Any]) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        definitions = get_hvac_attribute_definitions()

        # Required field checks (in addition to generic required-attribute checks)
        for code, meta in definitions.items():
            if not meta.get("required"):
                continue
            value = attrs.get(code)
            status = ValidationItemStatus.PRESENT if value not in (None, "") else ValidationItemStatus.MISSING
            severity = ValidationSeverity.ERROR if status == ValidationItemStatus.MISSING else ValidationSeverity.INFO
            findings.append({
                "item_code": code,
                "item_label": meta.get("label", code),
                "category": ValidationType.ATTRIBUTE_COMPLETENESS,
                "status": status,
                "severity": severity,
                "source_type": ValidationSourceType.ATTRIBUTE,
                "source_reference": "HVAC_SCHEMA_REQUIRED",
                "remarks": "" if status == ValidationItemStatus.PRESENT else f"Required HVAC field '{code}' is missing",
            })

        # Numeric ranges
        findings.extend(HVACValidationService._numeric_check("area_sq_ft", attrs.get("area_sq_ft"), min_value=100, max_value=500000))
        findings.extend(HVACValidationService._numeric_check("ceiling_height_ft", attrs.get("ceiling_height_ft"), min_value=7, max_value=60))
        findings.extend(HVACValidationService._numeric_check("ambient_temp_max_c", attrs.get("ambient_temp_max_c"), min_value=20, max_value=65))

        # Landlord constraints required for mall
        if str(attrs.get("store_type") or "").upper() == "MALL" and not str(attrs.get("landlord_constraints") or "").strip():
            findings.append({
                "item_code": "landlord_constraints",
                "item_label": "Landlord Constraints",
                "category": ValidationType.COMPLIANCE_READINESS,
                "status": ValidationItemStatus.MISSING,
                "severity": ValidationSeverity.CRITICAL,
                "source_type": ValidationSourceType.ATTRIBUTE,
                "source_reference": "HVAC_MALL_CONSTRAINT",
                "remarks": "Mall stores require explicit landlord constraints.",
            })

        # Ambiguity detection in text fields
        text_fields = ["landlord_constraints", "operating_hours", "required_standards_local_notes"]
        ambiguous_terms = ("tbd", "to be decided", "as per site", "na", "n/a", "unknown")
        for code in text_fields:
            value = str(attrs.get(code) or "").strip().lower()
            if not value:
                continue
            if any(term in value for term in ambiguous_terms):
                findings.append({
                    "item_code": code,
                    "item_label": definitions.get(code, {}).get("label", code),
                    "category": ValidationType.AMBIGUITY_CHECK,
                    "status": ValidationItemStatus.AMBIGUOUS,
                    "severity": ValidationSeverity.WARNING,
                    "source_type": ValidationSourceType.ATTRIBUTE,
                    "source_reference": "HVAC_AMBIGUITY",
                    "remarks": f"Field '{code}' appears ambiguous: '{value}'.",
                })

        # Retrofit detection marker
        existing_hvac = str(attrs.get("existing_hvac_type") or "").strip()
        if existing_hvac:
            findings.append({
                "item_code": "existing_hvac_type",
                "item_label": definitions.get("existing_hvac_type", {}).get("label", "Existing HVAC Type"),
                "category": ValidationType.SCOPE_COVERAGE,
                "status": ValidationItemStatus.PRESENT,
                "severity": ValidationSeverity.INFO,
                "source_type": ValidationSourceType.ATTRIBUTE,
                "source_reference": "HVAC_RETROFIT_DETECTED",
                "remarks": "Retrofit/replacement context detected.",
            })

        return findings

    @staticmethod
    def _numeric_check(code: str, raw_value: Any, *, min_value: float, max_value: float) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []
        if raw_value in (None, ""):
            return findings
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            findings.append({
                "item_code": code,
                "item_label": code.replace("_", " ").title(),
                "category": ValidationType.ATTRIBUTE_COMPLETENESS,
                "status": ValidationItemStatus.FAILED,
                "severity": ValidationSeverity.ERROR,
                "source_type": ValidationSourceType.ATTRIBUTE,
                "source_reference": "HVAC_NUMERIC_VALIDATION",
                "remarks": f"Field '{code}' must be numeric.",
            })
            return findings

        if value < min_value or value > max_value:
            findings.append({
                "item_code": code,
                "item_label": code.replace("_", " ").title(),
                "category": ValidationType.ATTRIBUTE_COMPLETENESS,
                "status": ValidationItemStatus.WARNING,
                "severity": ValidationSeverity.WARNING,
                "source_type": ValidationSourceType.ATTRIBUTE,
                "source_reference": "HVAC_NUMERIC_RANGE",
                "remarks": f"Field '{code}'={value} is outside expected range [{min_value}, {max_value}]",
            })
        return findings
