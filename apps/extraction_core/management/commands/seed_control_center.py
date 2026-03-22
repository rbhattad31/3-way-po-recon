"""
Seed the Extraction Control Center with initial data.

Populates:
  - ExtractionRuntimeSettings (singleton)
  - ExtractionPromptTemplate (12 prompts matching core prompt registry)
  - CountryPack (one per jurisdiction)
  - ReviewRoutingRule (7 rules)
  - EntityExtractionProfile (for top vendors)

Prerequisites:
  - seed_extraction_config must have run first (jurisdictions + schemas)
  - seed_ap_data or seed_config must have run (vendors)

Usage:
    python manage.py seed_control_center
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.core.prompt_registry import PromptRegistry
from apps.extraction_core.models import (
    CountryPack,
    EntityExtractionProfile,
    ExtractionPromptTemplate,
    ExtractionRuntimeSettings,
    ReviewRoutingRule,
    TaxJurisdictionProfile,
)


# ─────────────────────────────────────────────────────────────────────────
# Prompt templates — mirrors the prompts in apps/core/prompt_registry.py
# ─────────────────────────────────────────────────────────────────────────

PROMPT_TEMPLATES = [
    # ── Extraction prompts ──────────────────────────────────────────────
    {
        "prompt_code": "extraction.invoice_system",
        "prompt_category": "extraction",
        "document_type": "INVOICE",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["ocr_text"],
    },
    # ── Agent prompts ───────────────────────────────────────────────────
    {
        "prompt_code": "agent.exception_analysis",
        "prompt_category": "agent",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["exceptions", "match_status", "reconciliation_mode"],
    },
    {
        "prompt_code": "agent.invoice_understanding",
        "prompt_category": "agent",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["invoice_data", "match_status"],
    },
    {
        "prompt_code": "agent.po_retrieval",
        "prompt_category": "agent",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["po_number", "vendor_name"],
    },
    {
        "prompt_code": "agent.grn_retrieval",
        "prompt_category": "agent",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["po_number", "grn_data"],
    },
    {
        "prompt_code": "agent.review_routing",
        "prompt_category": "agent",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["exceptions", "severity"],
    },
    {
        "prompt_code": "agent.case_summary",
        "prompt_category": "agent",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["case_data", "reconciliation_mode"],
    },
    {
        "prompt_code": "agent.reconciliation_assist",
        "prompt_category": "agent",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["line_items", "discrepancies", "reconciliation_mode"],
    },
    # ── Case prompts ───────────────────────────────────────────────────
    {
        "prompt_code": "case.reviewer_copilot",
        "prompt_category": "case",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["case_data", "user_question"],
    },
    {
        "prompt_code": "case.non_po_validation",
        "prompt_category": "validation",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["invoice_data", "validation_results"],
    },
    {
        "prompt_code": "case.exception_analysis",
        "prompt_category": "case",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["exceptions", "processing_path"],
    },
    {
        "prompt_code": "case.case_summary",
        "prompt_category": "case",
        "version": 1,
        "status": "ACTIVE",
        "variables_json": ["case_data"],
    },
]


# ─────────────────────────────────────────────────────────────────────────
# Review routing rules
# ─────────────────────────────────────────────────────────────────────────

ROUTING_RULES = [
    {
        "name": "Low Confidence → Exception Ops",
        "rule_code": "low_confidence_to_exception_ops",
        "condition_type": "low_confidence",
        "condition_config_json": {"threshold": 0.60},
        "target_queue": "EXCEPTION_OPS",
        "priority": 10,
        "description": "Route extractions with confidence below 60% to exception operations for manual review.",
    },
    {
        "name": "Tax Issues → Tax Review",
        "rule_code": "tax_issues_to_tax_review",
        "condition_type": "tax_issues",
        "condition_config_json": {"issue_types": ["tax_mismatch", "missing_tax_id", "invalid_tax_rate"]},
        "target_queue": "TAX_REVIEW",
        "priority": 20,
        "description": "Route extractions with tax-related issues to specialized tax review queue.",
    },
    {
        "name": "Vendor Mismatch → Exception Ops",
        "rule_code": "vendor_mismatch_to_exception",
        "condition_type": "vendor_mismatch",
        "condition_config_json": {"fuzzy_threshold": 0.70},
        "target_queue": "EXCEPTION_OPS",
        "priority": 30,
        "description": "Route extractions where vendor matching confidence is below 70% for manual verification.",
    },
    {
        "name": "Schema Missing → Senior Review",
        "rule_code": "schema_missing_to_senior",
        "condition_type": "schema_missing",
        "condition_config_json": {},
        "target_queue": "SENIOR_REVIEW",
        "priority": 40,
        "description": "Route documents with no matching extraction schema to senior reviewers.",
    },
    {
        "name": "Jurisdiction Mismatch → Senior Review",
        "rule_code": "jurisdiction_mismatch_to_senior",
        "condition_type": "jurisdiction_mismatch",
        "condition_config_json": {"detected_vs_expected": True},
        "target_queue": "SENIOR_REVIEW",
        "priority": 50,
        "description": "Route cases where detected jurisdiction differs from expected jurisdiction.",
    },
    {
        "name": "Duplicate Suspicion → Exception Ops",
        "rule_code": "duplicate_to_exception",
        "condition_type": "duplicate_suspicion",
        "condition_config_json": {"similarity_threshold": 0.90},
        "target_queue": "EXCEPTION_OPS",
        "priority": 15,
        "description": "Route suspected duplicate invoices for manual deduplication review.",
    },
    {
        "name": "Unsupported Document → Senior Review",
        "rule_code": "unsupported_doc_to_senior",
        "condition_type": "unsupported_document_type",
        "condition_config_json": {},
        "target_queue": "SENIOR_REVIEW",
        "priority": 60,
        "description": "Route documents of unrecognised type to senior review for classification.",
    },
]


class Command(BaseCommand):
    help = "Seed Extraction Control Center data (settings, prompts, country packs, routing rules, entity profiles)."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Seeding Extraction Control Center...")

        self._seed_runtime_settings()
        self._seed_prompt_templates()
        self._seed_country_packs()
        self._seed_routing_rules()
        self._seed_entity_profiles()

        self.stdout.write(self.style.SUCCESS("Extraction Control Center seeded successfully!"))

    # ── Runtime Settings ────────────────────────────────────────────────

    def _seed_runtime_settings(self):
        self.stdout.write("  Seeding runtime settings...")
        _, created = ExtractionRuntimeSettings.objects.update_or_create(
            name="Default",
            defaults={
                "jurisdiction_mode": "AUTO",
                "default_country_code": "IN",
                "default_regime_code": "GST",
                "enable_jurisdiction_detection": True,
                "allow_manual_override": True,
                "confidence_threshold_for_detection": 0.70,
                "fallback_to_detection_on_schema_miss": True,
                "ocr_enabled": True,
                "llm_extraction_enabled": True,
                "retry_count": 2,
                "timeout_seconds": 120,
                "max_pages": 50,
                "multi_document_split_enabled": False,
                "auto_approval_enabled": False,
                "auto_approval_threshold": 0.95,
                "review_confidence_threshold": 0.70,
                "vendor_matching_enabled": True,
                "vendor_fuzzy_threshold": 0.80,
                "po_lookup_enabled": True,
                "contract_lookup_enabled": False,
                "correction_tracking_enabled": True,
                "analytics_enabled": True,
                "is_active": True,
            },
        )
        tag = "CREATED" if created else "UPDATED"
        self.stdout.write(f"    [{tag}] Runtime Settings: Default")

    # ── Prompt Templates ────────────────────────────────────────────────

    def _seed_prompt_templates(self):
        self.stdout.write("  Seeding prompt templates...")
        count = 0
        for tpl_data in PROMPT_TEMPLATES:
            prompt_code = tpl_data["prompt_code"]

            # Pull the actual prompt text from the core prompt registry
            prompt_text = PromptRegistry.get(prompt_code)
            if not prompt_text:
                self.stdout.write(
                    self.style.WARNING(f"    [SKIP] No prompt text found for {prompt_code}")
                )
                continue

            _, created = ExtractionPromptTemplate.objects.update_or_create(
                prompt_code=prompt_code,
                version=tpl_data["version"],
                defaults={
                    "prompt_category": tpl_data.get("prompt_category", "extraction"),
                    "country_code": tpl_data.get("country_code", ""),
                    "regime_code": tpl_data.get("regime_code", ""),
                    "document_type": tpl_data.get("document_type", ""),
                    "schema_code": tpl_data.get("schema_code", ""),
                    "status": tpl_data["status"],
                    "prompt_text": prompt_text,
                    "variables_json": tpl_data.get("variables_json", []),
                    "effective_from": timezone.now(),
                    "is_active": True,
                },
            )
            tag = "CREATED" if created else "UPDATED"
            self.stdout.write(f"    [{tag}] Prompt: {prompt_code} v{tpl_data['version']}")
            count += 1
        self.stdout.write(f"    Total prompts: {count}")

    # ── Country Packs ──────────────────────────────────────────────────

    def _seed_country_packs(self):
        self.stdout.write("  Seeding country packs...")
        jurisdictions = TaxJurisdictionProfile.objects.filter(is_active=True)
        if not jurisdictions.exists():
            self.stdout.write(
                self.style.WARNING(
                    "    No jurisdictions found. Run seed_extraction_config first."
                )
            )
            return

        for j in jurisdictions:
            pack, created = CountryPack.objects.update_or_create(
                jurisdiction=j,
                defaults={
                    "pack_status": "ACTIVE",
                    "schema_version": "1.0",
                    "validation_profile_version": "1.0",
                    "normalization_profile_version": "1.0",
                    "activated_at": timezone.now(),
                    "notes": f"Auto-seeded country pack for {j.country_name} ({j.tax_regime})",
                },
            )
            tag = "CREATED" if created else "UPDATED"
            self.stdout.write(f"    [{tag}] Country Pack: {j.country_name} ({j.country_code})")

    # ── Routing Rules ──────────────────────────────────────────────────

    def _seed_routing_rules(self):
        self.stdout.write("  Seeding routing rules...")
        for rule_data in ROUTING_RULES:
            _, created = ReviewRoutingRule.objects.update_or_create(
                rule_code=rule_data["rule_code"],
                defaults={
                    "name": rule_data["name"],
                    "condition_type": rule_data["condition_type"],
                    "condition_config_json": rule_data["condition_config_json"],
                    "target_queue": rule_data["target_queue"],
                    "priority": rule_data["priority"],
                    "description": rule_data.get("description", ""),
                    "is_active": True,
                },
            )
            tag = "CREATED" if created else "UPDATED"
            self.stdout.write(f"    [{tag}] Rule: {rule_data['name']}")

    # ── Entity Extraction Profiles ─────────────────────────────────────

    def _seed_entity_profiles(self):
        self.stdout.write("  Seeding entity extraction profiles...")
        from apps.vendors.models import Vendor

        vendors = Vendor.objects.filter(is_active=True)
        if not vendors.exists():
            self.stdout.write(
                self.style.WARNING("    No vendors found. Run seed_ap_data first.")
            )
            return

        # Country-code mapping from vendor country field
        COUNTRY_REGIME = {
            "IN": ("IN", "GST"),
            "India": ("IN", "GST"),
            "AE": ("AE", "VAT"),
            "UAE": ("AE", "VAT"),
            "United Arab Emirates": ("AE", "VAT"),
            "SA": ("SA", "VAT"),
            "Saudi Arabia": ("SA", "VAT"),
        }

        count = 0
        for vendor in vendors:
            country_raw = getattr(vendor, "country", "") or ""
            cc, regime = COUNTRY_REGIME.get(country_raw, ("", ""))

            _, created = EntityExtractionProfile.objects.update_or_create(
                entity=vendor,
                defaults={
                    "default_country_code": cc,
                    "default_regime_code": regime,
                    "default_document_language": "en",
                    "jurisdiction_mode": "AUTO",
                    "is_active": True,
                },
            )
            tag = "CREATED" if created else "UPDATED"
            self.stdout.write(f"    [{tag}] Profile: {vendor.name} → {cc}/{regime}")
            count += 1
        self.stdout.write(f"    Total profiles: {count}")
