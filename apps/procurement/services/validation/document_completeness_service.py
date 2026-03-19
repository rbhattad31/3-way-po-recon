"""DocumentCompletenessValidationService — validate required documents/sections."""
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


class DocumentCompletenessValidationService:
    """Check that required uploaded documents or extracted sections exist."""

    @staticmethod
    def validate(
        request: ProcurementRequest,
        rules: List[ValidationRule],
    ) -> List[Dict[str, Any]]:
        findings: List[Dict[str, Any]] = []

        doc_rules = [
            r for r in rules
            if r.rule_type == ValidationRuleType.REQUIRED_DOCUMENT
        ]

        if not doc_rules:
            return findings

        # Gather existing documents from quotations
        quotations = list(
            request.quotations.select_related("uploaded_document").all()
        )
        has_quotation = len(quotations) > 0
        has_uploaded_doc = any(q.uploaded_document_id for q in quotations)
        has_extracted = any(
            q.extraction_status == "COMPLETED" for q in quotations
        )
        has_line_items = any(q.line_items.exists() for q in quotations)

        # Evaluate each document rule
        for rule in doc_rules:
            condition = rule.condition_json or {}
            doc_type = condition.get("document_type", rule.rule_code)

            present = _check_document_presence(
                doc_type, has_quotation, has_uploaded_doc, has_extracted, has_line_items
            )

            findings.append({
                "item_code": doc_type,
                "item_label": rule.rule_name,
                "category": ValidationType.DOCUMENT_COMPLETENESS,
                "status": ValidationItemStatus.PRESENT if present else ValidationItemStatus.MISSING,
                "severity": ValidationSeverity.INFO if present else rule.severity,
                "source_type": ValidationSourceType.DOCUMENT,
                "source_reference": rule.rule_code,
                "remarks": "" if present else (
                    rule.failure_message or f"Required document '{doc_type}' not found"
                ),
            })

        return findings


def _check_document_presence(
    doc_type: str,
    has_quotation: bool,
    has_uploaded_doc: bool,
    has_extracted: bool,
    has_line_items: bool,
) -> bool:
    """Map generic document types to presence checks."""
    doc_type_upper = doc_type.upper()

    if doc_type_upper in ("QUOTATION", "QUOTATION_FILE", "SUPPLIER_QUOTATION"):
        return has_quotation
    if doc_type_upper in ("UPLOADED_DOCUMENT", "QUOTATION_ATTACHMENT"):
        return has_uploaded_doc
    if doc_type_upper in ("BOQ", "BILL_OF_QUANTITIES", "LINE_ITEMS"):
        return has_line_items
    if doc_type_upper in ("EXTRACTED_DATA", "EXTRACTION"):
        return has_extracted
    if doc_type_upper in ("SPECIFICATION", "TECHNICAL_SPEC"):
        # Could be checked via attributes or document metadata in future
        return has_uploaded_doc
    # Default: check if any document exists
    return has_quotation or has_uploaded_doc
