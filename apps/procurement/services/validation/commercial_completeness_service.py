"""CommercialCompletenessValidationService — validate commercial terms presence."""
from __future__ import annotations

import logging
import re
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

# Generic commercial elements to check
DEFAULT_COMMERCIAL_TERMS: List[Dict[str, Any]] = [
    {"code": "WARRANTY", "label": "Warranty Terms", "keywords": ["warranty", "guarantee", "warranty period"]},
    {"code": "DELIVERY", "label": "Delivery Terms", "keywords": ["delivery", "shipping", "freight", "lead time", "delivery schedule"]},
    {"code": "PAYMENT_TERMS", "label": "Payment Terms", "keywords": ["payment", "payment terms", "advance", "credit", "retention"]},
    {"code": "TAXES", "label": "Taxes / VAT", "keywords": ["tax", "vat", "gst", "duty", "customs"]},
    {"code": "INSTALLATION", "label": "Installation Scope", "keywords": ["installation", "install", "commissioning", "erection"]},
    {"code": "SUPPORT", "label": "After-Sales Support", "keywords": ["support", "maintenance", "amc", "service level", "sla"]},
    {"code": "LEAD_TIME", "label": "Lead Time", "keywords": ["lead time", "delivery period", "days", "weeks", "timeline"]},
    {"code": "TESTING", "label": "Testing / Commissioning", "keywords": ["testing", "commissioning", "inspection", "acceptance"]},
]


class CommercialCompletenessValidationService:
    """Check whether key commercial elements are present."""

    @staticmethod
    def validate(
        request: ProcurementRequest,
        rules: List[ValidationRule],
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []

        # Gather all searchable text
        searchable_text = _collect_searchable_text(request)

        # Build commercial checks from rules + defaults
        checks = _build_checks(rules)

        for check in checks:
            code = check["code"]
            label = check["label"]
            keywords = check.get("keywords", [])
            severity = check.get("severity", ValidationSeverity.WARNING)
            source_ref = check.get("source_reference", "commercial_check")

            found = _keywords_present(searchable_text, keywords)

            findings.append({
                "item_code": code,
                "item_label": label,
                "category": ValidationType.COMMERCIAL_COMPLETENESS,
                "status": ValidationItemStatus.PRESENT if found else ValidationItemStatus.MISSING,
                "severity": ValidationSeverity.INFO if found else severity,
                "source_type": ValidationSourceType.RULE,
                "source_reference": source_ref,
                "remarks": "" if found else f"Commercial element '{label}' not detected",
            })

        return findings


def _collect_searchable_text(request: ProcurementRequest) -> str:
    """Concatenate all text from request, attributes, and quotation line items."""
    parts: List[str] = []

    if request.description:
        parts.append(request.description)

    for attr in request.attributes.all():
        if attr.value_text:
            parts.append(attr.value_text)

    for quotation in request.quotations.prefetch_related("line_items").all():
        for line in quotation.line_items.all():
            if line.description:
                parts.append(line.description)

    return " ".join(parts).lower()


def _build_checks(rules: List[ValidationRule]) -> List[Dict[str, Any]]:
    """Build commercial checks from rules + defaults."""
    checks: List[Dict[str, Any]] = []
    rule_codes: Set[str] = set()

    commercial_rules = [
        r for r in rules
        if r.rule_type == ValidationRuleType.COMMERCIAL_CHECK
    ]

    for rule in commercial_rules:
        condition = rule.condition_json or {}
        checks.append({
            "code": rule.rule_code,
            "label": rule.rule_name,
            "keywords": condition.get("keywords", []),
            "severity": rule.severity,
            "source_reference": rule.rule_code,
        })
        rule_codes.add(rule.rule_code)

    # Add defaults not already covered by rules
    for default in DEFAULT_COMMERCIAL_TERMS:
        if default["code"] not in rule_codes:
            checks.append({
                **default,
                "severity": ValidationSeverity.WARNING,
                "source_reference": "default_commercial",
            })

    return checks


def _keywords_present(text: str, keywords: List[str]) -> bool:
    """Check if any keyword is present in text."""
    for kw in keywords:
        if kw.lower() in text:
            return True
    return False
