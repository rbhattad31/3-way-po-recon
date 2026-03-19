"""AttributeCompletenessValidationService — validate required structured attributes."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from apps.core.enums import (
    ValidationItemStatus,
    ValidationRuleType,
    ValidationSeverity,
    ValidationSourceType,
    ValidationType,
)
from apps.procurement.models import ProcurementRequest, ValidationRule

logger = logging.getLogger(__name__)


class AttributeCompletenessValidationService:
    """Check whether all required structured fields on a ProcurementRequest are present."""

    @staticmethod
    def validate(
        request: ProcurementRequest,
        rules: List[ValidationRule],
    ) -> List[Dict[str, Any]]:
        """Return a list of finding dicts for attribute completeness.

        Each finding: {item_code, item_label, category, status, severity,
                       source_type, source_reference, remarks}
        """
        findings: List[Dict[str, Any]] = []

        # Collect existing attribute codes and values
        attrs = {
            a.attribute_code: a
            for a in request.attributes.all()
        }

        # Filter rules relevant to attribute checks
        attr_rules = [
            r for r in rules
            if r.rule_type == ValidationRuleType.REQUIRED_ATTRIBUTE
        ]

        for rule in attr_rules:
            condition = rule.condition_json or {}
            attr_code = condition.get("attribute_code", rule.rule_code)

            attr_obj = attrs.get(attr_code)
            if attr_obj is None:
                findings.append(_missing_finding(rule, attr_code))
                continue

            # Check if value is populated
            has_value = bool(
                attr_obj.value_text.strip()
                or attr_obj.value_number is not None
                or attr_obj.value_json
            )
            if not has_value:
                findings.append(_empty_finding(rule, attr_code))
                continue

            # Optional type validation
            expected_type = condition.get("expected_type")
            if expected_type == "NUMBER" and attr_obj.value_number is None:
                findings.append({
                    "item_code": attr_code,
                    "item_label": rule.rule_name,
                    "category": ValidationType.ATTRIBUTE_COMPLETENESS,
                    "status": ValidationItemStatus.WARNING,
                    "severity": ValidationSeverity.WARNING,
                    "source_type": ValidationSourceType.ATTRIBUTE,
                    "source_reference": rule.rule_code,
                    "remarks": rule.failure_message or f"Expected numeric value for '{attr_code}'",
                })
                continue

            # Present and valid
            findings.append({
                "item_code": attr_code,
                "item_label": rule.rule_name,
                "category": ValidationType.ATTRIBUTE_COMPLETENESS,
                "status": ValidationItemStatus.PRESENT,
                "severity": ValidationSeverity.INFO,
                "source_type": ValidationSourceType.ATTRIBUTE,
                "source_reference": rule.rule_code,
                "remarks": "",
            })

        # Also check inherently required attributes (is_required=True on the attribute)
        for attr_code, attr_obj in attrs.items():
            if attr_obj.is_required and attr_code not in {f["item_code"] for f in findings}:
                has_value = bool(
                    attr_obj.value_text.strip()
                    or attr_obj.value_number is not None
                    or attr_obj.value_json
                )
                findings.append({
                    "item_code": attr_code,
                    "item_label": attr_obj.attribute_label or attr_code,
                    "category": ValidationType.ATTRIBUTE_COMPLETENESS,
                    "status": ValidationItemStatus.PRESENT if has_value else ValidationItemStatus.MISSING,
                    "severity": ValidationSeverity.INFO if has_value else ValidationSeverity.ERROR,
                    "source_type": ValidationSourceType.ATTRIBUTE,
                    "source_reference": "is_required",
                    "remarks": "" if has_value else f"Required attribute '{attr_code}' has no value",
                })

        return findings


def _missing_finding(rule: ValidationRule, attr_code: str) -> Dict[str, Any]:
    return {
        "item_code": attr_code,
        "item_label": rule.rule_name,
        "category": ValidationType.ATTRIBUTE_COMPLETENESS,
        "status": ValidationItemStatus.MISSING,
        "severity": rule.severity,
        "source_type": ValidationSourceType.ATTRIBUTE,
        "source_reference": rule.rule_code,
        "remarks": rule.failure_message or f"Required attribute '{attr_code}' is missing",
    }


def _empty_finding(rule: ValidationRule, attr_code: str) -> Dict[str, Any]:
    return {
        "item_code": attr_code,
        "item_label": rule.rule_name,
        "category": ValidationType.ATTRIBUTE_COMPLETENESS,
        "status": ValidationItemStatus.MISSING,
        "severity": rule.severity,
        "source_type": ValidationSourceType.ATTRIBUTE,
        "source_reference": rule.rule_code,
        "remarks": rule.failure_message or f"Required attribute '{attr_code}' has no value",
    }
