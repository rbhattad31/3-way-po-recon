"""HVAC domain schema registry for ProcurementRequestAttribute fields."""
from __future__ import annotations

from typing import Any, Dict, List

HVAC_DOMAIN_CODE = "HVAC"
HVAC_SCHEMA_CODE = "HVAC_PRODUCT_SELECTION_V1"

_HVAC_GROUPS: List[Dict[str, Any]] = [
    {
        "group_code": "identification",
        "group_label": "Identification",
        "fields": [
            {"attribute_code": "store_id", "label": "Store ID", "data_type": "TEXT", "required": True},
            {"attribute_code": "brand", "label": "Brand", "data_type": "TEXT", "required": True},
        ],
    },
    {
        "group_code": "location",
        "group_label": "Location",
        "fields": [
            {
                "attribute_code": "country",
                "label": "Country",
                "data_type": "SELECT",
                "required": True,
                "options": ["UAE", "KSA", "QATAR"],
            },
            {"attribute_code": "city", "label": "City", "data_type": "TEXT", "required": True},
        ],
    },
    {
        "group_code": "store",
        "group_label": "Store",
        "fields": [
            {
                "attribute_code": "store_type",
                "label": "Store Type",
                "data_type": "SELECT",
                "required": True,
                "options": ["MALL", "STANDALONE"],
            },
            {
                "attribute_code": "store_format",
                "label": "Store Format",
                "data_type": "SELECT",
                "required": True,
                "options": ["RETAIL", "HYPERMARKET", "FURNITURE", "OTHER"],
            },
            {"attribute_code": "operating_hours", "label": "Operating Hours", "data_type": "TEXT", "required": False},
            {
                "attribute_code": "footfall_category",
                "label": "Footfall Category",
                "data_type": "SELECT",
                "required": False,
                "options": ["LOW", "MEDIUM", "HIGH"],
            },
        ],
    },
    {
        "group_code": "physical",
        "group_label": "Physical",
        "fields": [
            {"attribute_code": "area_sq_ft", "label": "Area (sq.ft)", "data_type": "NUMBER", "required": True},
            {"attribute_code": "ceiling_height_ft", "label": "Ceiling Height (ft)", "data_type": "NUMBER", "required": True},
        ],
    },
    {
        "group_code": "environment",
        "group_label": "Environment",
        "fields": [
            {"attribute_code": "ambient_temp_max_c", "label": "Ambient Temp Max (°C)", "data_type": "NUMBER", "required": True},
            {
                "attribute_code": "humidity_level",
                "label": "Humidity Level",
                "data_type": "SELECT",
                "required": True,
                "options": ["LOW", "MEDIUM", "HIGH"],
            },
            {
                "attribute_code": "dust_exposure",
                "label": "Dust Exposure",
                "data_type": "SELECT",
                "required": True,
                "options": ["LOW", "MEDIUM", "HIGH"],
            },
            {
                "attribute_code": "heat_load_category",
                "label": "Heat Load Category",
                "data_type": "SELECT",
                "required": True,
                "options": ["LOW", "MEDIUM", "HIGH"],
            },
            {
                "attribute_code": "fresh_air_requirement",
                "label": "Fresh Air Requirement",
                "data_type": "SELECT",
                "required": False,
                "options": ["LOW", "MEDIUM", "HIGH"],
            },
        ],
    },
    {
        "group_code": "hvac_context",
        "group_label": "HVAC Context",
        "fields": [
            {"attribute_code": "existing_hvac_type", "label": "Existing HVAC Type", "data_type": "TEXT", "required": False},
            {
                "attribute_code": "energy_efficiency_priority",
                "label": "Energy Efficiency Priority",
                "data_type": "SELECT",
                "required": False,
                "options": ["LOW", "MEDIUM", "HIGH"],
            },
            {
                "attribute_code": "maintenance_priority",
                "label": "Maintenance Priority",
                "data_type": "SELECT",
                "required": False,
                "options": ["LOW", "MEDIUM", "HIGH"],
            },
            {"attribute_code": "preferred_oems", "label": "Preferred OEMs", "data_type": "TEXT", "required": False},
        ],
    },
    {
        "group_code": "constraints",
        "group_label": "Constraints",
        "fields": [
            {"attribute_code": "landlord_constraints", "label": "Landlord Constraints", "data_type": "TEXT", "required": True},
            {
                "attribute_code": "required_standards_local_notes",
                "label": "Required Standards / Local Notes",
                "data_type": "TEXT",
                "required": False,
            },
        ],
    },
    {
        "group_code": "business",
        "group_label": "Business",
        "fields": [
            {
                "attribute_code": "budget_level",
                "label": "Budget Level",
                "data_type": "SELECT",
                "required": True,
                "options": ["LOW", "MEDIUM", "HIGH"],
            },
        ],
    },
]


def get_hvac_grouped_schema() -> List[Dict[str, Any]]:
    return _HVAC_GROUPS


def get_hvac_attribute_definitions() -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for group in _HVAC_GROUPS:
        for field in group.get("fields", []):
            result[field["attribute_code"]] = {
                **field,
                "group_code": group["group_code"],
                "group_label": group["group_label"],
            }
    return result
