"""HVAC validation rule bootstrap for ValidationRuleSet system."""
from __future__ import annotations

from django.db import transaction

from apps.core.enums import (
    ValidationRuleType,
    ValidationSeverity,
    ValidationType,
)
from apps.procurement.domain.hvac.schema import (
    HVAC_DOMAIN_CODE,
    HVAC_SCHEMA_CODE,
    get_hvac_attribute_definitions,
)
from apps.procurement.models import ValidationRule, ValidationRuleSet


class HVACRuleSeedService:
    """Ensures HVAC ValidationRuleSet/ValidationRule entries exist."""

    RULE_SET_CODE = "HVAC_PRODUCT_SELECTION_V1_REQUIRED_ATTRS"

    @staticmethod
    def ensure_rules() -> None:
        with transaction.atomic():
            rule_set, _ = ValidationRuleSet.objects.update_or_create(
                rule_set_code=HVACRuleSeedService.RULE_SET_CODE,
                defaults={
                    "rule_set_name": "HVAC Product Selection Required Attributes",
                    "description": "Required HVAC schema fields for request readiness.",
                    "domain_code": HVAC_DOMAIN_CODE,
                    "schema_code": HVAC_SCHEMA_CODE,
                    "validation_type": ValidationType.ATTRIBUTE_COMPLETENESS,
                    "is_active": True,
                    "priority": 10,
                    "config_json": {"schema_code": HVAC_SCHEMA_CODE},
                },
            )

            defs = get_hvac_attribute_definitions()
            display_order = 1
            for code, meta in defs.items():
                if not meta.get("required"):
                    continue
                ValidationRule.objects.update_or_create(
                    rule_set=rule_set,
                    rule_code=f"HVAC_REQ_{code.upper()}",
                    defaults={
                        "rule_name": f"{meta.get('label', code)} is required",
                        "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                        "severity": ValidationSeverity.ERROR,
                        "is_active": True,
                        "condition_json": {
                            "attribute_code": code,
                            "expected_type": meta.get("data_type", "TEXT"),
                        },
                        "failure_message": f"Required HVAC field '{code}' is missing.",
                        "display_order": display_order,
                    },
                )
                display_order += 1
