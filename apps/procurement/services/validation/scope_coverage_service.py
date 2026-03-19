"""ScopeCoverageValidationService — validate expected scope categories."""
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
from apps.procurement.models import ProcurementRequest, ValidationRule, ValidationRuleSet

logger = logging.getLogger(__name__)


class ScopeCoverageValidationService:
    """Compare expected scope categories against detected categories in quotation line items."""

    @staticmethod
    def validate(
        request: ProcurementRequest,
        rules: List[ValidationRule],
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []

        # Collect detected categories from quotation line items
        detected_categories = _collect_detected_categories(request)

        # Also check expected categories from rule sets' config_json
        expected_from_config = _collect_expected_from_config(rules)

        category_rules = [
            r for r in rules
            if r.rule_type == ValidationRuleType.REQUIRED_CATEGORY
        ]

        # Evaluate category rules
        for rule in category_rules:
            condition = rule.condition_json or {}
            expected_category = condition.get("category_code", rule.rule_code).upper()

            present = expected_category in detected_categories

            findings.append({
                "item_code": expected_category,
                "item_label": rule.rule_name,
                "category": ValidationType.SCOPE_COVERAGE,
                "status": ValidationItemStatus.PRESENT if present else ValidationItemStatus.MISSING,
                "severity": ValidationSeverity.INFO if present else rule.severity,
                "source_type": ValidationSourceType.LINE_ITEM,
                "source_reference": rule.rule_code,
                "remarks": "" if present else (
                    rule.failure_message or f"Expected scope category '{expected_category}' not found"
                ),
            })

        # Check config-based expected categories not already covered by rules
        checked_categories = {f["item_code"] for f in findings}
        for cat_code in expected_from_config:
            if cat_code in checked_categories:
                continue

            present = cat_code in detected_categories
            findings.append({
                "item_code": cat_code,
                "item_label": cat_code.replace("_", " ").title(),
                "category": ValidationType.SCOPE_COVERAGE,
                "status": ValidationItemStatus.PRESENT if present else ValidationItemStatus.MISSING,
                "severity": ValidationSeverity.INFO if present else ValidationSeverity.WARNING,
                "source_type": ValidationSourceType.LINE_ITEM,
                "source_reference": "config_json",
                "remarks": "" if present else f"Expected category '{cat_code}' not detected in line items",
            })

        return findings


def _collect_detected_categories(request: ProcurementRequest) -> Set[str]:
    """Extract all category_codes from quotation line items."""
    categories: Set[str] = set()
    for quotation in request.quotations.prefetch_related("line_items").all():
        for line in quotation.line_items.all():
            if line.category_code:
                categories.add(line.category_code.upper())
    return categories


def _collect_expected_from_config(rules: List[ValidationRule]) -> Set[str]:
    """Extract expected categories from rule set config_json."""
    expected: Set[str] = set()
    seen_rule_sets: Set[int] = set()
    for rule in rules:
        rs_id = rule.rule_set_id
        if rs_id in seen_rule_sets:
            continue
        seen_rule_sets.add(rs_id)
        config = rule.rule_set.config_json or {}
        for cat in config.get("expected_categories", []):
            if isinstance(cat, str):
                expected.add(cat.upper())
            elif isinstance(cat, dict) and "code" in cat:
                expected.add(cat["code"].upper())
    return expected
