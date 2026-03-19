"""AmbiguityValidationService — detect vague or unclear descriptions."""
from __future__ import annotations

import logging
import re
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

# Default ambiguity patterns — can be overridden via rule condition_json
DEFAULT_AMBIGUITY_PATTERNS: List[Dict[str, str]] = [
    {"pattern": r"\bas\s+required\b", "label": "Vague: 'as required'"},
    {"pattern": r"\bincluding\s+all\s+accessories\b", "label": "Bundled: 'including all accessories'"},
    {"pattern": r"\bcomplete\s+system\b", "label": "Vague: 'complete system'"},
    {"pattern": r"\bmiscellaneous\b", "label": "Vague: 'miscellaneous'"},
    {"pattern": r"\blump\s*sum\b", "label": "Bundled: 'lumpsum'"},
    {"pattern": r"\bjob\s+complete\b", "label": "Vague: 'job complete'"},
    {"pattern": r"\bas\s+per\s+standard\b", "label": "Vague: 'as per standard'"},
    {"pattern": r"\bsupply\s+and\s+install", "label": "Bundled: 'supply and install'"},
    {"pattern": r"\betc\.?\b", "label": "Vague: 'etc'"},
    {"pattern": r"\ball\s+inclusive\b", "label": "Vague: 'all inclusive'"},
    {"pattern": r"\bturnkey\b", "label": "Vague: 'turnkey'"},
    {"pattern": r"\ball\s+related\b", "label": "Vague: 'all related'"},
]


class AmbiguityValidationService:
    """Detect vague scope descriptions and ambiguous line items."""

    @staticmethod
    def validate(
        request: ProcurementRequest,
        rules: List[ValidationRule],
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []

        # Collect ambiguity patterns from rules
        patterns = _build_patterns(rules)

        # Check request description
        if request.description:
            desc_hits = _scan_text(request.description, patterns)
            for hit in desc_hits:
                findings.append({
                    "item_code": f"DESC_{hit['pattern_label'][:30].upper().replace(' ', '_')}",
                    "item_label": hit["pattern_label"],
                    "category": ValidationType.AMBIGUITY_CHECK,
                    "status": ValidationItemStatus.AMBIGUOUS,
                    "severity": ValidationSeverity.WARNING,
                    "source_type": ValidationSourceType.ATTRIBUTE,
                    "source_reference": "request.description",
                    "remarks": f"Ambiguous phrase detected: '{hit['matched_text']}' in request description",
                })

        # Check quotation line items
        for quotation in request.quotations.prefetch_related("line_items").all():
            for line in quotation.line_items.all():
                text = line.description or ""
                if line.normalized_description:
                    text = line.normalized_description

                hits = _scan_text(text, patterns)
                for hit in hits:
                    findings.append({
                        "item_code": f"LINE_{line.line_number}_{hit['pattern_label'][:20].upper().replace(' ', '_')}",
                        "item_label": f"Line {line.line_number}: {hit['pattern_label']}",
                        "category": ValidationType.AMBIGUITY_CHECK,
                        "status": ValidationItemStatus.AMBIGUOUS,
                        "severity": ValidationSeverity.WARNING,
                        "source_type": ValidationSourceType.LINE_ITEM,
                        "source_reference": f"quotation:{quotation.pk}:line:{line.line_number}",
                        "remarks": f"Ambiguous phrase '{hit['matched_text']}' in line item description",
                    })

        # Check attribute values for ambiguity
        for attr in request.attributes.all():
            if attr.value_text:
                hits = _scan_text(attr.value_text, patterns)
                for hit in hits:
                    findings.append({
                        "item_code": f"ATTR_{attr.attribute_code}_{hit['pattern_label'][:20].upper().replace(' ', '_')}",
                        "item_label": f"Attribute '{attr.attribute_label}': {hit['pattern_label']}",
                        "category": ValidationType.AMBIGUITY_CHECK,
                        "status": ValidationItemStatus.AMBIGUOUS,
                        "severity": ValidationSeverity.WARNING,
                        "source_type": ValidationSourceType.ATTRIBUTE,
                        "source_reference": attr.attribute_code,
                        "remarks": f"Ambiguous phrase '{hit['matched_text']}' in attribute value",
                    })

        return findings


def _build_patterns(rules: List[ValidationRule]) -> List[Dict[str, str]]:
    """Build regex patterns from rules + defaults."""
    patterns = list(DEFAULT_AMBIGUITY_PATTERNS)

    ambiguity_rules = [
        r for r in rules
        if r.rule_type == ValidationRuleType.AMBIGUITY_PATTERN
    ]

    for rule in ambiguity_rules:
        condition = rule.condition_json or {}
        if "pattern" in condition:
            patterns.append({
                "pattern": condition["pattern"],
                "label": rule.rule_name,
            })
        if "patterns" in condition:
            for p in condition["patterns"]:
                if isinstance(p, str):
                    patterns.append({"pattern": p, "label": rule.rule_name})
                elif isinstance(p, dict):
                    patterns.append({
                        "pattern": p.get("pattern", ""),
                        "label": p.get("label", rule.rule_name),
                    })

    return patterns


def _scan_text(text: str, patterns: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Scan text against all patterns, return matches."""
    hits: List[Dict[str, str]] = []
    text_lower = text.lower()
    for p in patterns:
        regex = p.get("pattern", "")
        if not regex:
            continue
        match = re.search(regex, text_lower, re.IGNORECASE)
        if match:
            hits.append({
                "pattern_label": p.get("label", regex),
                "matched_text": match.group(0),
            })
    return hits
