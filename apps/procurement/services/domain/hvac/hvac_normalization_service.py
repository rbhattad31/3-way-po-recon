"""HVACNormalizationService — normalize HVAC request attributes."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from apps.procurement.domain.hvac.schema import get_hvac_attribute_definitions


class HVACNormalizationService:
    """Normalize categorical and textual HVAC attributes into stable values."""

    _COUNTRY_MAP = {
        "uae": "UAE",
        "united arab emirates": "UAE",
        "ksa": "KSA",
        "saudi": "KSA",
        "saudi arabia": "KSA",
        "qatar": "QATAR",
    }

    _TRI_LEVEL_FIELDS = {
        "footfall_category",
        "humidity_level",
        "dust_exposure",
        "heat_load_category",
        "fresh_air_requirement",
        "budget_level",
        "energy_efficiency_priority",
        "maintenance_priority",
    }

    _TRI_LEVEL_VALUES = {"LOW", "MEDIUM", "HIGH"}

    @staticmethod
    def normalize(attrs: Dict[str, Any]) -> Dict[str, Any]:
        definitions = get_hvac_attribute_definitions()
        normalized: Dict[str, Any] = {}
        issues: List[str] = []

        for code, definition in definitions.items():
            raw = attrs.get(code)
            if raw is None:
                continue

            data_type = definition.get("data_type")
            if data_type == "NUMBER":
                value = HVACNormalizationService._to_float(raw)
                if value is None:
                    issues.append(f"{code}: invalid numeric value")
                else:
                    normalized[code] = value
                continue

            text = HVACNormalizationService._clean_text(raw)
            if not text:
                continue

            if code == "country":
                key = text.lower()
                normalized_country = HVACNormalizationService._COUNTRY_MAP.get(key)
                if normalized_country:
                    normalized[code] = normalized_country
                else:
                    issues.append("country: unsupported value, expected UAE/KSA/QATAR")
                    normalized[code] = text.upper()
                continue

            if code in HVACNormalizationService._TRI_LEVEL_FIELDS:
                upper = text.upper()
                if upper not in HVACNormalizationService._TRI_LEVEL_VALUES:
                    issues.append(f"{code}: expected LOW/MEDIUM/HIGH")
                normalized[code] = upper
                continue

            if code in {"store_type", "store_format"}:
                normalized[code] = text.upper().replace(" ", "_")
                continue

            normalized[code] = text

        normalized["normalization_issues"] = issues
        return normalized

    @staticmethod
    def _clean_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            cleaned = str(value).replace(",", "").strip()
            return float(cleaned)
        except (TypeError, ValueError):
            return None
