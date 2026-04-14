"""Attribute mapping helpers for request and quotation prefill."""
from __future__ import annotations

from typing import Any, Dict, List


class AttributeMappingService:
    @staticmethod
    def map_request_fields(extracted: Dict[str, Any]) -> Dict[str, Any]:
        fields = extracted.get("fields") if isinstance(extracted.get("fields"), dict) else extracted
        if not isinstance(fields, dict):
            fields = {}

        core_fields: Dict[str, Dict[str, Any]] = {}
        attributes: List[Dict[str, Any]] = []
        unmapped: List[Dict[str, Any]] = []

        core_keys = {"title", "description", "domain_code", "geography_country", "geography_city", "currency"}

        for key, val in fields.items():
            value = val.get("value") if isinstance(val, dict) else val
            confidence = float(val.get("confidence", 0.5)) if isinstance(val, dict) else 0.5
            if value in (None, ""):
                continue
            if key in core_keys:
                core_fields[key] = {"value": str(value), "confidence": confidence}
            else:
                attributes.append(
                    {
                        "attribute_code": str(key),
                        "attribute_label": str(key).replace("_", " ").title(),
                        "value": value,
                        "data_type": "TEXT",
                        "confidence": confidence,
                    },
                )

        for entry in extracted.get("requirements") or []:
            if isinstance(entry, dict):
                attributes.append(
                    {
                        "attribute_code": str(entry.get("key") or "requirements"),
                        "attribute_label": "Requirements",
                        "value": str(entry.get("value") or ""),
                        "data_type": "TEXT",
                        "confidence": float(entry.get("confidence", 0.5)),
                    },
                )

        return {"core_fields": core_fields, "attributes": attributes, "unmapped": unmapped}

    @staticmethod
    def map_quotation_fields(extracted: Dict[str, Any]) -> Dict[str, Any]:
        fields = extracted.get("fields") if isinstance(extracted.get("fields"), dict) else extracted.get("header", {})
        if not isinstance(fields, dict):
            fields = {}

        header_fields: Dict[str, Dict[str, Any]] = {}
        unmapped: List[Dict[str, Any]] = []

        for key, val in fields.items():
            value = val.get("value") if isinstance(val, dict) else val
            confidence = float(val.get("confidence", 0.5)) if isinstance(val, dict) else 0.5
            if value in (None, ""):
                continue
            header_fields[str(key)] = {"value": value, "confidence": confidence}

        line_items = extracted.get("line_items") if isinstance(extracted.get("line_items"), list) else []
        commercial_terms = extracted.get("commercial_terms") if isinstance(extracted.get("commercial_terms"), list) else []

        return {
            "header_fields": header_fields,
            "commercial_terms": commercial_terms,
            "line_items": line_items,
            "unmapped": unmapped,
        }

    @staticmethod
    def classify_confidence(fields: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        high: List[str] = []
        medium: List[str] = []
        low: List[str] = []

        for key, data in (fields or {}).items():
            conf = float(data.get("confidence", 0.5) or 0.5)
            if conf >= 0.75:
                high.append(key)
            elif conf >= 0.4:
                medium.append(key)
            else:
                low.append(key)

        return {
            "high_confidence": high,
            "medium_confidence": medium,
            "low_confidence": low,
        }
