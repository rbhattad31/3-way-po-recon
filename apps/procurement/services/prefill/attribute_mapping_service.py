"""AttributeMappingService — maps extracted fields to request attributes / quotation fields."""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# Synonym mapping: extracted field name → canonical attribute code
_REQUEST_FIELD_SYNONYMS: dict[str, str] = {
    # Title
    "title": "title",
    "project_title": "title",
    "project_name": "title",
    "rfq_title": "title",
    "name": "title",
    "subject": "title",
    # Description
    "description": "description",
    "scope": "description",
    "scope_of_work": "description",
    "project_description": "description",
    "details": "description",
    "summary": "description",
    # Domain
    "domain": "domain_code",
    "domain_code": "domain_code",
    "category": "domain_code",
    "sector": "domain_code",
    "industry": "domain_code",
    # Geography
    "country": "geography_country",
    "geography_country": "geography_country",
    "location_country": "geography_country",
    "city": "geography_city",
    "geography_city": "geography_city",
    "location_city": "geography_city",
    "location": "geography_city",
    # Currency
    "currency": "currency",
    "currency_code": "currency",
    # Budget
    "budget": "budget",
    "estimated_budget": "budget",
    "budget_amount": "budget",
    "total_budget": "budget",
    # Timeline
    "deadline": "deadline",
    "due_date": "deadline",
    "submission_date": "deadline",
    "delivery_date": "delivery_date",
    "completion_date": "delivery_date",
    "timeline": "timeline",
    "project_duration": "timeline",
    # Quantity
    "quantity": "quantity",
    "qty": "quantity",
    "total_quantity": "quantity",
    # Specification
    "specifications": "specifications",
    "technical_specifications": "specifications",
    "tech_specs": "specifications",
    "spec": "specifications",
    "requirements": "requirements",
    "technical_requirements": "requirements",
    "technical_requirement": "requirements",
    "scope_item": "requirements",
    "scope_items": "requirements",
    # Compliance / standards
    "compliance": "compliance",
    "compliance_requirement": "compliance",
    "certifications": "compliance",
    "standards": "compliance",
    "certification": "compliance",
    # Acceptance criteria
    "acceptance_criteria": "acceptance_criteria",
    "acceptance": "acceptance_criteria",
    # Warranty
    "warranty": "warranty",
    "warranty_period": "warranty",
    "warranty_terms": "warranty",
    # Delivery
    "delivery": "delivery_date",
    "installation": "installation",
    "installation_requirements": "installation",
    # Other procurement attributes
    "payment_terms": "payment_terms",
    "payment_conditions": "payment_terms",
    "lead_time": "lead_time",
    "delivery_time": "lead_time",
    "brand": "brand",
    "preferred_brand": "brand",
    "model": "model",
    "dimensions": "dimensions",
    "material": "material",
    "color": "color",
    "capacity": "capacity",
    "power": "power",
    "voltage": "voltage",
    "weight": "weight",
    "size": "size",
}

# Quotation header field synonyms
_QUOTATION_FIELD_SYNONYMS: dict[str, str] = {
    "vendor_name": "vendor_name",
    "vendor": "vendor_name",
    "supplier": "vendor_name",
    "supplier_name": "vendor_name",
    "company_name": "vendor_name",
    "quotation_number": "quotation_number",
    "quote_number": "quotation_number",
    "reference_number": "quotation_number",
    "ref_no": "quotation_number",
    "proposal_number": "quotation_number",
    "quotation_date": "quotation_date",
    "quote_date": "quotation_date",
    "date": "quotation_date",
    "proposal_date": "quotation_date",
    "total_amount": "total_amount",
    "total": "total_amount",
    "grand_total": "total_amount",
    "net_total": "total_amount",
    "subtotal": "subtotal",
    "sub_total": "subtotal",
    "currency": "currency",
    "currency_code": "currency",
    "warranty": "warranty_terms",
    "warranty_terms": "warranty_terms",
    "warranty_period": "warranty_terms",
    "payment_terms": "payment_terms",
    "payment_conditions": "payment_terms",
    "delivery_terms": "delivery_terms",
    "delivery_period": "delivery_terms",
    "lead_time": "lead_time",
    "delivery_time": "lead_time",
    "taxes": "taxes",
    "tax": "taxes",
    "vat": "taxes",
    "tax_amount": "taxes",
    "exclusions": "exclusions",
    "not_included": "exclusions",
    "installation": "installation_terms",
    "installation_terms": "installation_terms",
    "support": "support_terms",
    "amc": "support_terms",
    "testing": "testing_terms",
    "commissioning": "testing_terms",
}

# Core ProcurementRequest fields (set directly on model, not as attributes)
_REQUEST_CORE_FIELDS = {
    "title", "description", "domain_code", "geography_country",
    "geography_city", "currency",
}

# Confidence thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.7
LOW_CONFIDENCE_THRESHOLD = 0.4


class AttributeMappingService:
    """Map extracted fields to canonical request attributes or quotation fields."""

    @staticmethod
    def map_request_fields(extracted: dict[str, Any]) -> dict:
        """Map extracted raw fields to request core fields + dynamic attributes.

        Returns:
            {
                "core_fields": {field: {"value": ..., "confidence": float}},
                "attributes": [{attribute_code, attribute_label, value, data_type, confidence}],
                "unmapped": [{key, value}],
            }
        """
        core_fields: dict[str, dict] = {}
        attributes: list[dict] = []
        unmapped: list[dict] = []

        fields = extracted.get("fields", extracted)
        if isinstance(fields, list):
            # List of {key, value, confidence} dicts
            field_items = [
                (f.get("key", ""), f.get("value", ""), f.get("confidence", 0.5))
                for f in fields
            ]
        else:
            field_items = [
                (k, v.get("value", v) if isinstance(v, dict) else v,
                 v.get("confidence", 0.5) if isinstance(v, dict) else 0.5)
                for k, v in fields.items()
                if k not in ("line_items", "items", "confidence", "attributes", "requirements")
            ]

        # Handle nested 'attributes' / 'requirements' arrays from LLM
        for list_key in ("attributes", "requirements"):
            nested = extracted.get(list_key) or (fields.get(list_key) if isinstance(fields, dict) else None)
            if isinstance(nested, list):
                for entry in nested:
                    if isinstance(entry, dict):
                        field_items.append((
                            entry.get("key") or entry.get("name", ""),
                            entry.get("value", ""),
                            entry.get("confidence", 0.5),
                        ))
                    elif isinstance(entry, str):
                        # Plain string requirement
                        field_items.append(("requirements", entry, 0.5))

        for raw_key, value, confidence in field_items:
            normalized_key = raw_key.lower().strip().replace(" ", "_").replace("-", "_")
            canonical = _REQUEST_FIELD_SYNONYMS.get(normalized_key)

            if not canonical:
                unmapped.append({"key": raw_key, "value": value, "confidence": confidence})
                continue

            if canonical in _REQUEST_CORE_FIELDS:
                core_fields[canonical] = {
                    "value": str(value).strip() if value else "",
                    "confidence": confidence,
                }
            else:
                data_type = AttributeMappingService._infer_data_type(value)
                attributes.append({
                    "attribute_code": canonical,
                    "attribute_label": canonical.replace("_", " ").title(),
                    "value": value,
                    "data_type": data_type,
                    "confidence": confidence,
                })

        return {
            "core_fields": core_fields,
            "attributes": attributes,
            "unmapped": unmapped,
        }

    @staticmethod
    def map_quotation_fields(extracted: dict[str, Any]) -> dict:
        """Map extracted raw fields to quotation header fields + line items.

        Returns:
            {
                "header_fields": {field: {"value": ..., "confidence": float}},
                "commercial_terms": [{term, value, confidence}],
                "line_items": [{line_number, description, quantity, unit, unit_rate, total_amount, brand, model, confidence}],
                "unmapped": [{key, value}],
            }
        """
        header_fields: dict[str, dict] = {}
        commercial_terms: list[dict] = []
        unmapped: list[dict] = []

        _HEADER_CORE = {"vendor_name", "quotation_number", "quotation_date", "total_amount", "currency", "subtotal"}
        _COMMERCIAL_TERMS = {
            "warranty_terms", "payment_terms", "delivery_terms", "lead_time",
            "taxes", "exclusions", "installation_terms", "support_terms", "testing_terms",
        }

        fields = extracted.get("fields", extracted)
        if isinstance(fields, list):
            field_items = [
                (f.get("key", ""), f.get("value", ""), f.get("confidence", 0.5))
                for f in fields
            ]
        else:
            field_items = [
                (k, v.get("value", v) if isinstance(v, dict) else v,
                 v.get("confidence", 0.5) if isinstance(v, dict) else 0.5)
                for k, v in fields.items()
                if k not in ("line_items", "items", "confidence")
            ]

        for raw_key, value, confidence in field_items:
            normalized_key = raw_key.lower().strip().replace(" ", "_").replace("-", "_")
            canonical = _QUOTATION_FIELD_SYNONYMS.get(normalized_key)

            if not canonical:
                unmapped.append({"key": raw_key, "value": value, "confidence": confidence})
                continue

            if canonical in _HEADER_CORE:
                header_fields[canonical] = {
                    "value": str(value).strip() if value else "",
                    "confidence": confidence,
                }
            elif canonical in _COMMERCIAL_TERMS:
                commercial_terms.append({
                    "term": canonical,
                    "value": str(value).strip() if value else "",
                    "confidence": confidence,
                })
            else:
                unmapped.append({"key": raw_key, "value": value, "confidence": confidence})

        # Line items
        raw_items = extracted.get("line_items") or extracted.get("items") or []
        line_items = []
        for idx, item in enumerate(raw_items, start=1):
            line_items.append({
                "line_number": item.get("line_number", idx),
                "description": str(item.get("description", "")).strip(),
                "category_code": str(item.get("category_code") or item.get("category", "")).strip(),
                "quantity": AttributeMappingService._safe_number(item.get("quantity", 1)),
                "unit": str(item.get("unit") or item.get("uom", "EA")).strip(),
                "unit_rate": AttributeMappingService._safe_number(item.get("unit_rate") or item.get("unit_price", 0)),
                "total_amount": AttributeMappingService._safe_number(item.get("total_amount") or item.get("amount", 0)),
                "brand": str(item.get("brand", "")).strip(),
                "model": str(item.get("model", "")).strip(),
                "confidence": item.get("confidence", 0.5),
            })

        return {
            "header_fields": header_fields,
            "commercial_terms": commercial_terms,
            "line_items": line_items,
            "unmapped": unmapped,
        }

    @staticmethod
    def classify_confidence(fields: dict[str, dict]) -> dict:
        """Separate fields into high_confidence and low_confidence groups.

        Returns:
            {"high_confidence": [...], "low_confidence": [...]}
        """
        high = []
        low = []
        for field_name, info in fields.items():
            confidence = info.get("confidence", 0.5)
            entry = {"field": field_name, **info}
            if confidence >= HIGH_CONFIDENCE_THRESHOLD:
                high.append(entry)
            else:
                low.append(entry)
        return {"high_confidence": high, "low_confidence": low}

    @staticmethod
    def _infer_data_type(value: Any) -> str:
        if isinstance(value, bool):
            return "BOOLEAN"
        if isinstance(value, (int, float, Decimal)):
            return "NUMBER"
        if isinstance(value, dict):
            return "JSON"
        if isinstance(value, list):
            return "JSON"
        text = str(value).strip()
        try:
            Decimal(text.replace(",", ""))
            return "NUMBER"
        except (InvalidOperation, ValueError):
            pass
        return "TEXT"

    @staticmethod
    def _safe_number(value: Any) -> float:
        if value is None:
            return 0.0
        try:
            return float(str(value).replace(",", ""))
        except (ValueError, TypeError):
            return 0.0
