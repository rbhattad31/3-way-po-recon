"""Management command: seed_hvac_rules

Provisions HVAC domain ValidationRuleSets and ValidationRules in the database.

Usage:
    python manage.py seed_hvac_rules
    python manage.py seed_hvac_rules --flush   # Delete and re-seed HVAC rules
"""
from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from apps.core.enums import (
    ValidationType,
    ValidationRuleType,
    ValidationSeverity,
    ValidationEvaluationMode,
)
from apps.procurement.models import ValidationRule, ValidationRuleSet

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rule set definitions
# ---------------------------------------------------------------------------
RULE_SETS = [
    {
        "rule_set_code": "HVAC_ATTR_COMPLETENESS",
        "rule_set_name": "HVAC Attribute Completeness",
        "description": (
            "Validates that all mandatory store parameters are present before "
            "running the HVAC recommendation or benchmark analysis."
        ),
        "domain_code": "HVAC",
        "schema_code": "",
        "validation_type": ValidationType.ATTRIBUTE_COMPLETENESS,
        "priority": 10,
        "config_json": {
            "required_for_recommendation": [
                "store_type", "area_sqm", "zone_count",
                "ambient_temp_max", "chilled_water_available",
            ],
            "required_for_benchmarking": ["store_type", "area_sqm"],
            "expected_attribute_codes": [
                "store_type", "area_sqm", "cooling_load_tr", "zone_count",
                "ambient_temp_max", "chilled_water_available",
                "outdoor_unit_restriction", "budget_category",
                "efficiency_priority", "dust_level", "humidity_level",
            ],
        },
        "rules": [
            {
                "rule_code": "HVAC_ATTR_001",
                "rule_name": "Store Type Required",
                "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                "severity": ValidationSeverity.CRITICAL,
                "failure_message": "Store / Facility Type is required for HVAC recommendation.",
                "remediation_hint": "Set 'store_type' to MALL, STANDALONE, WAREHOUSE, OFFICE, DATA_CENTER, or RESTAURANT.",
                "condition_json": {"attribute_code": "store_type"},
                "display_order": 1,
            },
            {
                "rule_code": "HVAC_ATTR_002",
                "rule_name": "Conditioned Area Required",
                "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                "severity": ValidationSeverity.CRITICAL,
                "failure_message": "Conditioned area (sqm) is required for load estimation.",
                "remediation_hint": "Provide the total floor area to be cooled in square metres.",
                "condition_json": {"attribute_code": "area_sqm"},
                "display_order": 2,
            },
            {
                "rule_code": "HVAC_ATTR_003",
                "rule_name": "Zone Count Required",
                "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                "severity": ValidationSeverity.ERROR,
                "failure_message": "Number of independently controlled zones is required.",
                "remediation_hint": "Specify the number of thermal zones (e.g. 1 for single open space, 4+ for segmented floors).",
                "condition_json": {"attribute_code": "zone_count"},
                "display_order": 3,
            },
            {
                "rule_code": "HVAC_ATTR_004",
                "rule_name": "Ambient Temperature Required",
                "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                "severity": ValidationSeverity.ERROR,
                "failure_message": "Maximum outdoor / ambient temperature is required for GCC system selection.",
                "remediation_hint": "Provide max_ambient_temp in °C. GCC typical range: 46–52°C.",
                "condition_json": {"attribute_code": "ambient_temp_max"},
                "display_order": 4,
            },
            {
                "rule_code": "HVAC_ATTR_005",
                "rule_name": "Chilled Water Availability Required",
                "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                "severity": ValidationSeverity.ERROR,
                "failure_message": "Chilled water availability is a key differentiator for system selection.",
                "remediation_hint": "Set 'chilled_water_available' to YES, NO, or UNKNOWN.",
                "condition_json": {"attribute_code": "chilled_water_available"},
                "display_order": 5,
            },
            {
                "rule_code": "HVAC_ATTR_006",
                "rule_name": "Budget Category Recommended",
                "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "Budget category not specified — benchmark may use default tier.",
                "remediation_hint": "Set 'budget_category' to LOW, MEDIUM, HIGH, or UNCONSTRAINED.",
                "condition_json": {"attribute_code": "budget_category"},
                "display_order": 6,
            },
            {
                "rule_code": "HVAC_ATTR_007",
                "rule_name": "Efficiency Priority Recommended",
                "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                "severity": ValidationSeverity.INFO,
                "failure_message": "Energy efficiency priority not specified.",
                "remediation_hint": "Set 'efficiency_priority' to YES or NO.",
                "condition_json": {"attribute_code": "efficiency_priority"},
                "display_order": 7,
            },
            {
                "rule_code": "HVAC_ATTR_008",
                "rule_name": "Dust Level Recommended",
                "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                "severity": ValidationSeverity.INFO,
                "failure_message": "Dust level not specified — filtration requirements cannot be determined.",
                "remediation_hint": "Set 'dust_level' to LOW, MEDIUM, or HIGH.",
                "condition_json": {"attribute_code": "dust_level"},
                "display_order": 8,
            },
            {
                "rule_code": "HVAC_ATTR_009",
                "rule_name": "Outdoor Unit Restriction Check",
                "rule_type": ValidationRuleType.REQUIRED_ATTRIBUTE,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "Outdoor unit restriction not specified — may affect recommended system type.",
                "remediation_hint": "Specify 'outdoor_unit_restriction' (YES/NO). Some malls prohibit outdoor condensers.",
                "condition_json": {"attribute_code": "outdoor_unit_restriction"},
                "display_order": 9,
            },
        ],
    },
    {
        "rule_set_code": "HVAC_DOC_COMPLETENESS",
        "rule_set_name": "HVAC Document Completeness",
        "description": "Validates that supplier quotation documents are present for HVAC benchmark analysis.",
        "domain_code": "HVAC",
        "schema_code": "",
        "validation_type": ValidationType.DOCUMENT_COMPLETENESS,
        "priority": 20,
        "config_json": {
            "required_docs": ["SUPPLIER_QUOTATION"],
            "optional_docs": ["TECHNICAL_SPECIFICATION", "FLOOR_PLAN", "SITE_SURVEY_REPORT"],
        },
        "rules": [
            {
                "rule_code": "HVAC_DOC_001",
                "rule_name": "Supplier Quotation Required for Benchmark",
                "rule_type": ValidationRuleType.REQUIRED_DOCUMENT,
                "severity": ValidationSeverity.ERROR,
                "failure_message": "No supplier quotation uploaded. Cannot run benchmarking without quotation data.",
                "remediation_hint": "Upload the supplier quotation PDF or BOQ Excel file.",
                "condition_json": {"document_type": "SUPPLIER_QUOTATION", "min_count": 1},
                "display_order": 1,
            },
            {
                "rule_code": "HVAC_DOC_002",
                "rule_name": "Technical Specification Recommended",
                "rule_type": ValidationRuleType.REQUIRED_DOCUMENT,
                "severity": ValidationSeverity.INFO,
                "failure_message": "Technical specification not uploaded — brand/model validation limited.",
                "remediation_hint": "Upload equipment data sheets or project specification.",
                "condition_json": {"document_type": "TECHNICAL_SPECIFICATION", "min_count": 1},
                "display_order": 2,
            },
        ],
    },
    {
        "rule_set_code": "HVAC_SCOPE_COVERAGE",
        "rule_set_name": "HVAC Scope Coverage",
        "description": (
            "Validates that the quotation covers the expected HVAC scope categories "
            "based on store type and project requirements."
        ),
        "domain_code": "HVAC",
        "schema_code": "",
        "validation_type": ValidationType.SCOPE_COVERAGE,
        "priority": 30,
        "config_json": {
            "expected_categories_by_store_type": {
                "MALL": ["SPLIT_AC", "FCU", "VRF", "GI_DUCTWORK", "SUPPLY_DIFFUSER", "THERMOSTAT"],
                "STANDALONE": ["SPLIT_AC", "VRF", "GI_DUCTWORK", "SUPPLY_DIFFUSER", "EXHAUST_FAN"],
                "WAREHOUSE": ["PACKAGED_DX", "GI_DUCTWORK", "SUPPLY_DIFFUSER", "EXHAUST_FAN"],
                "OFFICE": ["SPLIT_AC", "FCU", "VRF", "GI_DUCTWORK", "SUPPLY_DIFFUSER"],
                "DATA_CENTER": ["CHILLER", "AHU", "FCU", "PRECISION_COOLING"],
            },
            "always_expected": ["TESTING_COMMISSIONING"],
        },
        "rules": [
            {
                "rule_code": "HVAC_SCOPE_001",
                "rule_name": "Cooling Equipment Category Present",
                "rule_type": ValidationRuleType.REQUIRED_CATEGORY,
                "severity": ValidationSeverity.ERROR,
                "failure_message": "No cooling equipment line items found in the quotation.",
                "remediation_hint": "Ensure the quotation includes line items for split ACs, FCUs, VRF units, or chillers.",
                "condition_json": {
                    "category_codes": ["SPLIT_AC", "FCU", "VRF", "CHILLER", "PACKAGED_DX", "CASSETTE_AC"],
                    "min_match": 1,
                },
                "display_order": 1,
            },
            {
                "rule_code": "HVAC_SCOPE_002",
                "rule_name": "Air Distribution Category Present",
                "rule_type": ValidationRuleType.REQUIRED_CATEGORY,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "No air distribution items (ductwork, diffusers, grilles) found.",
                "remediation_hint": "Check if ductwork, supply/return diffusers and grilles are included in scope.",
                "condition_json": {
                    "category_codes": ["GI_DUCTWORK", "FLEXIBLE_DUCT", "SUPPLY_DIFFUSER", "RETURN_DIFFUSER", "LINEAR_BAR_GRILLE"],
                    "min_match": 1,
                },
                "display_order": 2,
            },
            {
                "rule_code": "HVAC_SCOPE_003",
                "rule_name": "Controls Category Present",
                "rule_type": ValidationRuleType.REQUIRED_CATEGORY,
                "severity": ValidationSeverity.INFO,
                "failure_message": "No controls/thermostat items found in quotation.",
                "remediation_hint": "Verify thermostat, BMS connection, or room temperature controller is included.",
                "condition_json": {
                    "category_codes": ["THERMOSTAT", "BMS_INTEGRATION", "CONTROLLER"],
                    "min_match": 1,
                },
                "display_order": 3,
            },
            {
                "rule_code": "HVAC_SCOPE_004",
                "rule_name": "Testing & Commissioning Present",
                "rule_type": ValidationRuleType.REQUIRED_CATEGORY,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "Testing & commissioning (T&C) not found in quotation scope.",
                "remediation_hint": "T&C should be included per NEBB standards. Add T&C line item or confirm it is included in system price.",
                "condition_json": {
                    "category_codes": ["TESTING_COMMISSIONING"],
                    "min_match": 1,
                },
                "display_order": 4,
            },
        ],
    },
    {
        "rule_set_code": "HVAC_COMMERCIAL_TERMS",
        "rule_set_name": "HVAC Commercial Terms Completeness",
        "description": (
            "Validates that supplier quotation documents include all required commercial terms "
            "for HVAC procurement approval."
        ),
        "domain_code": "HVAC",
        "schema_code": "",
        "validation_type": ValidationType.COMMERCIAL_COMPLETENESS,
        "priority": 40,
        "config_json": {
            "required_terms": [
                "WARRANTY",
                "DELIVERY",
                "PAYMENT",
                "TAXES",
                "INSTALLATION",
                "SUPPORT",
                "LEAD_TIME",
                "TESTING",
            ],
            "hvac_specific_terms": ["WARRANTY_PERIOD", "AMC", "REFRIGERANT_WARRANTY", "COMPRESSOR_WARRANTY"],
        },
        "rules": [
            {
                "rule_code": "HVAC_COM_001",
                "rule_name": "Warranty Terms Present",
                "rule_type": ValidationRuleType.COMMERCIAL_CHECK,
                "severity": ValidationSeverity.ERROR,
                "failure_message": "Warranty terms not found in quotation.",
                "remediation_hint": "Request supplier to specify equipment warranty period (min 12 months compressor, 24 months parts).",
                "condition_json": {"check_type": "keyword", "keywords": ["WARRANTY", "GUARANTEE"]},
                "display_order": 1,
            },
            {
                "rule_code": "HVAC_COM_002",
                "rule_name": "Payment Terms Present",
                "rule_type": ValidationRuleType.COMMERCIAL_CHECK,
                "severity": ValidationSeverity.ERROR,
                "failure_message": "Payment terms not specified in quotation.",
                "remediation_hint": "Ensure quotation includes payment schedule (advance %, delivery %, commissioning %).",
                "condition_json": {"check_type": "keyword", "keywords": ["PAYMENT", "ADVANCE", "CREDIT"]},
                "display_order": 2,
            },
            {
                "rule_code": "HVAC_COM_003",
                "rule_name": "Delivery / Lead Time Specified",
                "rule_type": ValidationRuleType.COMMERCIAL_CHECK,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "Delivery lead time not stated.",
                "remediation_hint": "Request supplier to confirm delivery timeline in weeks from order placement.",
                "condition_json": {"check_type": "keyword", "keywords": ["DELIVERY", "LEAD TIME", "LEAD_TIME", "WEEKS"]},
                "display_order": 3,
            },
            {
                "rule_code": "HVAC_COM_004",
                "rule_name": "Tax Inclusions Specified",
                "rule_type": ValidationRuleType.COMMERCIAL_CHECK,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "VAT / tax treatment not stated in quotation.",
                "remediation_hint": "Confirm whether prices include or exclude 5% UAE VAT / KSA VAT.",
                "condition_json": {"check_type": "keyword", "keywords": ["VAT", "TAX", "EXCLUSIVE", "INCLUSIVE"]},
                "display_order": 4,
            },
            {
                "rule_code": "HVAC_COM_005",
                "rule_name": "Installation Scope Clear",
                "rule_type": ValidationRuleType.COMMERCIAL_CHECK,
                "severity": ValidationSeverity.ERROR,
                "failure_message": "Installation scope not clearly defined.",
                "remediation_hint": "Confirm whether quotation is supply-only or supply + installation (turnkey).",
                "condition_json": {"check_type": "keyword", "keywords": ["INSTALLATION", "INSTALL", "TURNKEY", "SUPPLY ONLY"]},
                "display_order": 5,
            },
        ],
    },
    {
        "rule_set_code": "HVAC_COMPLIANCE_READINESS",
        "rule_set_name": "HVAC Compliance Readiness",
        "description": (
            "Validates that the procurement specification meets GCC compliance standards "
            "(ASHRAE 90.1, UAE ESMA, SASO, ISO 50001, CIBSE)."
        ),
        "domain_code": "HVAC",
        "schema_code": "",
        "validation_type": ValidationType.COMPLIANCE_READINESS,
        "priority": 50,
        "config_json": {
            "applicable_standards": {
                "UAE": ["ASHRAE 90.1-2019", "UAE ESMA Standard", "ASHRAE 55-2020", "ASHRAE 62.1-2019"],
                "KSA": ["ASHRAE 90.1-2019", "SASO 2870", "SASO 4820", "Saudi Building Code"],
                "DEFAULT": ["ASHRAE 90.1-2019", "ISO 16813:2006"],
            },
            "minimum_efficiency": {
                "SPLIT_AC": {"min_seer": 5.0, "standard": "UAE ESMA 5-star"},
                "VRF_SYSTEM": {"min_iplv": 4.5, "standard": "ASHRAE 90.1 Table 6.8.1"},
                "FCU_CHILLED_WATER": {"min_cop": 3.5, "standard": "ASHRAE 90.1"},
            },
        },
        "rules": [
            {
                "rule_code": "HVAC_COMP_001",
                "rule_name": "Geography Specified (for Standards)",
                "rule_type": ValidationRuleType.COMPLIANCE_CHECK,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "Geography not specified — cannot determine applicable compliance standards.",
                "remediation_hint": "Set the country (UAE, KSA, Oman, Qatar, etc.) to enable standards-based compliance checks.",
                "condition_json": {"check_type": "attribute", "attribute_code": "geography_country"},
                "display_order": 1,
            },
            {
                "rule_code": "HVAC_COMP_002",
                "rule_name": "Refrigerant Compliance (Low GWP)",
                "rule_type": ValidationRuleType.COMPLIANCE_CHECK,
                "severity": ValidationSeverity.INFO,
                "failure_message": "Refrigerant type not specified — confirm compliance with F-Gas / Kigali Amendment.",
                "remediation_hint": "Prefer R32 (GWP=675) or R454B (GWP=466) over R410A (GWP=2,088).",
                "condition_json": {"check_type": "attribute", "attribute_code": "refrigerant_preference"},
                "display_order": 2,
            },
            {
                "rule_code": "HVAC_COMP_003",
                "rule_name": "Energy Efficiency Standard Reference",
                "rule_type": ValidationRuleType.COMPLIANCE_CHECK,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "No efficiency rating specified — ESMA/ASHRAE compliance cannot be verified.",
                "remediation_hint": (
                    "Request supplier to confirm: Split AC ≥ 5-star ESMA, VRF IPLV ≥ 4.5, "
                    "or COP per ASHRAE 90.1 Table 6.8.1."
                ),
                "condition_json": {"check_type": "keyword", "keywords": ["SEER", "IPLV", "COP", "EFFICIENCY", "STAR"]},
                "display_order": 3,
            },
            {
                "rule_code": "HVAC_COMP_004",
                "rule_name": "Fresh Air / Ventilation Requirements",
                "rule_type": ValidationRuleType.COMPLIANCE_CHECK,
                "severity": ValidationSeverity.INFO,
                "failure_message": "Fresh air / ventilation rate not addressed (ASHRAE 62.1 requirement).",
                "remediation_hint": "Confirm ventilation rates per ASHRAE 62.1 Table 6.2.2 for occupancy type.",
                "condition_json": {"check_type": "keyword", "keywords": ["FRESH AIR", "VENTILATION", "OA", "OUTDOOR AIR"]},
                "display_order": 4,
            },
        ],
    },
    {
        "rule_set_code": "HVAC_AMBIGUITY",
        "rule_set_name": "HVAC Ambiguous Description Check",
        "description": (
            "Detects vague or ambiguous language in HVAC quotation descriptions "
            "that could lead to scope disputes."
        ),
        "domain_code": "HVAC",
        "schema_code": "",
        "validation_type": ValidationType.AMBIGUITY_CHECK,
        "priority": 60,
        "config_json": {
            "hvac_ambiguous_patterns": [
                r"\bapprox(?:imately)?\b",
                r"\bas required\b",
                r"\bsimilar\b",
                r"\bor equivalent\b",
                r"\bstandard\b",
                r"\bappropriate\b",
                r"\bvarious\b",
                r"\bif needed\b",
                r"\bto be confirmed\b",
                r"\btbc\b",
                r"\blump\s?sum\b(?!.*testing)",
                r"\bmiscellaneous\b",
            ],
        },
        "rules": [
            {
                "rule_code": "HVAC_AMB_001",
                "rule_name": "Vague Quantity Language",
                "rule_type": ValidationRuleType.AMBIGUITY_PATTERN,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "Ambiguous quantity/scope language detected (e.g. 'as required', 'approximately').",
                "remediation_hint": "Replace vague quantities with specific numbers, sizes, or conditions.",
                "condition_json": {"pattern": r"\b(as required|approx|approximately|to be confirmed|tbc)\b"},
                "display_order": 1,
            },
            {
                "rule_code": "HVAC_AMB_002",
                "rule_name": "Undefined Specification References",
                "rule_type": ValidationRuleType.AMBIGUITY_PATTERN,
                "severity": ValidationSeverity.WARNING,
                "failure_message": "Description uses 'or equivalent' or 'similar' without baseline specification.",
                "remediation_hint": "Specify the baseline brand/model before allowing 'or equivalent' references.",
                "condition_json": {"pattern": r"\b(or equivalent|similar|similar type|equivalent)\b"},
                "display_order": 2,
            },
            {
                "rule_code": "HVAC_AMB_003",
                "rule_name": "Lump Sum Without Breakdown",
                "rule_type": ValidationRuleType.AMBIGUITY_PATTERN,
                "severity": ValidationSeverity.INFO,
                "failure_message": "Lump-sum prices without breakdown make benchmarking difficult.",
                "remediation_hint": "Request itemised pricing breakdown for lump-sum items where feasible.",
                "condition_json": {"pattern": r"\blump\s?sum\b"},
                "display_order": 3,
            },
        ],
    },
]


class Command(BaseCommand):
    help = "Seed HVAC domain ValidationRuleSets and ValidationRules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all existing HVAC rule sets before seeding.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== Seeding HVAC Validation Rules ==="))

        if options["flush"]:
            deleted, _ = ValidationRuleSet.objects.filter(domain_code="HVAC").delete()
            self.stdout.write(self.style.WARNING(f"  Deleted {deleted} existing HVAC rule sets."))

        created_sets = 0
        created_rules = 0
        updated_sets = 0
        updated_rules = 0

        for rs_data in RULE_SETS:
            rules = rs_data.pop("rules", [])
            rs, created = ValidationRuleSet.objects.update_or_create(
                rule_set_code=rs_data["rule_set_code"],
                defaults=rs_data,
            )
            if created:
                created_sets += 1
                self.stdout.write(f"  ✓ Created rule set: {rs.rule_set_code}")
            else:
                updated_sets += 1
                self.stdout.write(f"  ↻ Updated rule set: {rs.rule_set_code}")

            for rule_data in rules:
                rule_code = rule_data.pop("rule_code")
                _, r_created = ValidationRule.objects.update_or_create(
                    rule_set=rs,
                    rule_code=rule_code,
                    defaults={**rule_data, "evaluation_mode": ValidationEvaluationMode.DETERMINISTIC},
                )
                if r_created:
                    created_rules += 1
                else:
                    updated_rules += 1

            rs_data["rules"] = rules  # restore for next iteration

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done. Rule sets: {created_sets} created, {updated_sets} updated. "
            f"Rules: {created_rules} created, {updated_rules} updated."
        ))
