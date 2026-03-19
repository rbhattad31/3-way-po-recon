"""ComplianceReadinessValidationService — check compliance-related inputs."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Set

from apps.core.enums import (
    ValidationItemStatus,
    ValidationRuleType,
    ValidationSeverity,
    ValidationSourceType,
    ValidationType,
)
from apps.procurement.models import ProcurementRequest, ValidationRule

logger = logging.getLogger(__name__)


class ComplianceReadinessValidationService:
    """Check whether enough compliance info exists to proceed."""

    @staticmethod
    def validate(
        request: ProcurementRequest,
        rules: List[ValidationRule],
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []

        compliance_rules = [
            r for r in rules
            if r.rule_type == ValidationRuleType.COMPLIANCE_CHECK
        ]

        if not compliance_rules:
            return findings

        # Gather attribute data for inspection
        attrs = {
            a.attribute_code: a
            for a in request.attributes.all()
        }

        # Gather searchable text
        text_corpus = _collect_text(request)

        for rule in compliance_rules:
            condition = rule.condition_json or {}
            check_type = condition.get("check_type", "attribute")

            if check_type == "attribute":
                # Check if a specific attribute exists and has a value
                attr_code = condition.get("attribute_code", rule.rule_code)
                attr = attrs.get(attr_code)
                present = attr is not None and bool(
                    (attr.value_text or "").strip()
                    or attr.value_number is not None
                    or attr.value_json
                )
            elif check_type == "keyword":
                # Check if compliance keywords are mentioned
                keywords = condition.get("keywords", [])
                present = any(kw.lower() in text_corpus for kw in keywords)
            elif check_type == "geography":
                # Check that geography is specified
                present = bool(request.geography_country.strip())
            else:
                present = False

            findings.append({
                "item_code": rule.rule_code,
                "item_label": rule.rule_name,
                "category": ValidationType.COMPLIANCE_READINESS,
                "status": ValidationItemStatus.PRESENT if present else ValidationItemStatus.MISSING,
                "severity": ValidationSeverity.INFO if present else rule.severity,
                "source_type": ValidationSourceType.RULE,
                "source_reference": rule.rule_code,
                "remarks": "" if present else (
                    rule.failure_message or f"Compliance input '{rule.rule_name}' not found"
                ),
                "details_json": {"remediation_hint": rule.remediation_hint} if not present and rule.remediation_hint else None,
            })

        return findings


def _collect_text(request: ProcurementRequest) -> str:
    """Lower-case corpus of all text for keyword scanning."""
    parts: List[str] = []
    if request.description:
        parts.append(request.description)
    for attr in request.attributes.all():
        if attr.value_text:
            parts.append(attr.value_text)
    return " ".join(parts).lower()
