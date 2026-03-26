"""Management command: seed AgentDefinition records with catalog/contract metadata.

Usage:
    python manage.py seed_agent_contracts
    python manage.py seed_agent_contracts --dry-run

Only the new contract fields are written. The following fields are never
overwritten: name, description, enabled, llm_model, system_prompt,
max_retries, timeout_seconds.
"""
from django.core.management.base import BaseCommand

from apps.agents.models import AgentDefinition
from apps.core.enums import AgentType

# ---------------------------------------------------------------------------
# Contract data keyed by AgentType value
# ---------------------------------------------------------------------------
CONTRACTS = [
    {
        "agent_type": AgentType.INVOICE_EXTRACTION,
        "purpose": "Extract structured invoice data from OCR text using LLM",
        "entry_conditions": "Called immediately after OCR completes on a new invoice document",
        "success_criteria": "Returns full JSON with vendor, PO number, line items, and confidence >= 0.7",
        "prohibited_actions": ["AUTO_CLOSE", "ESCALATE_TO_MANAGER"],
        "requires_tool_grounding": False,
        "min_tool_calls": 0,
        "tool_failure_confidence_cap": None,
        "allowed_recommendation_types": None,
        "default_fallback_recommendation": "REPROCESS_EXTRACTION",
        "output_schema_name": "AgentOutputSchema",
        "output_schema_version": "v1",
        "lifecycle_status": "active",
        "owner_team": "AP Automation",
        "capability_tags": ["extraction"],
        "domain_tags": ["invoice", "ocr"],
        "human_review_required_conditions": "confidence < 0.6 or key fields missing",
    },
    {
        "agent_type": AgentType.INVOICE_UNDERSTANDING,
        "purpose": "Validate and clarify invoice extraction quality when confidence is low",
        "entry_conditions": "extraction_confidence < threshold OR match_status shows ambiguity",
        "success_criteria": "Determines whether extraction is reliable or must be reprocessed",
        "prohibited_actions": ["AUTO_CLOSE"],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.5,
        "allowed_recommendation_types": ["REPROCESS_EXTRACTION", "SEND_TO_AP_REVIEW"],
        "default_fallback_recommendation": "REPROCESS_EXTRACTION",
        "output_schema_name": "AgentOutputSchema",
        "output_schema_version": "v1",
        "lifecycle_status": "active",
        "owner_team": "AP Automation",
        "capability_tags": ["understanding", "validation"],
        "domain_tags": ["invoice", "extraction"],
        "human_review_required_conditions": "confidence < 0.5 after tool grounding",
    },
    {
        "agent_type": AgentType.PO_RETRIEVAL,
        "purpose": "Find the correct Purchase Order when deterministic lookup failed",
        "entry_conditions": "match_status = PO_NOT_FOUND or po_number missing on invoice",
        "success_criteria": "PO number confirmed via tool call and present in evidence",
        "prohibited_actions": ["AUTO_CLOSE"],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.4,
        "allowed_recommendation_types": ["SEND_TO_AP_REVIEW", "SEND_TO_PROCUREMENT"],
        "default_fallback_recommendation": "SEND_TO_AP_REVIEW",
        "output_schema_name": "AgentOutputSchema",
        "output_schema_version": "v1",
        "lifecycle_status": "active",
        "owner_team": "AP Automation",
        "capability_tags": ["retrieval"],
        "domain_tags": ["po", "invoice"],
        "human_review_required_conditions": "no PO found after all search strategies exhausted",
    },
    {
        "agent_type": AgentType.GRN_RETRIEVAL,
        "purpose": "Investigate goods receipt status when GRN is missing or partial",
        "entry_conditions": "reconciliation_mode = THREE_WAY AND exception_type = GRN_NOT_FOUND or GRN_PARTIAL",
        "success_criteria": "GRN status confirmed via tool call with quantity comparison",
        "prohibited_actions": ["AUTO_CLOSE"],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.4,
        "allowed_recommendation_types": ["SEND_TO_PROCUREMENT", "SEND_TO_AP_REVIEW"],
        "default_fallback_recommendation": "SEND_TO_PROCUREMENT",
        "output_schema_name": "AgentOutputSchema",
        "output_schema_version": "v1",
        "lifecycle_status": "active",
        "owner_team": "AP Automation",
        "capability_tags": ["retrieval"],
        "domain_tags": ["grn", "procurement"],
        "human_review_required_conditions": "goods not yet received or quantity rejected",
    },
    {
        "agent_type": AgentType.RECONCILIATION_ASSIST,
        "purpose": "Investigate partial match discrepancies at line level",
        "entry_conditions": "match_status = PARTIAL_MATCH with qty/price/amount discrepancies",
        "success_criteria": "Explains root cause of discrepancies and recommends resolution",
        "prohibited_actions": [],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.5,
        "allowed_recommendation_types": [
            "AUTO_CLOSE",
            "SEND_TO_AP_REVIEW",
            "SEND_TO_PROCUREMENT",
            "SEND_TO_VENDOR_CLARIFICATION",
        ],
        "default_fallback_recommendation": "SEND_TO_AP_REVIEW",
        "output_schema_name": "AgentOutputSchema",
        "output_schema_version": "v1",
        "lifecycle_status": "active",
        "owner_team": "AP Automation",
        "capability_tags": ["assist", "understanding"],
        "domain_tags": ["po", "invoice", "reconciliation"],
        "human_review_required_conditions": "discrepancy > tolerance AND confidence < 0.7",
    },
    {
        "agent_type": AgentType.EXCEPTION_ANALYSIS,
        "purpose": "Analyse reconciliation exceptions, determine root causes, recommend resolution",
        "entry_conditions": "exceptions present on result after matching",
        "success_criteria": "All exceptions categorised with root cause and recommendation",
        "prohibited_actions": [],
        "requires_tool_grounding": True,
        "min_tool_calls": 1,
        "tool_failure_confidence_cap": 0.5,
        "allowed_recommendation_types": [
            "AUTO_CLOSE",
            "SEND_TO_AP_REVIEW",
            "SEND_TO_PROCUREMENT",
            "SEND_TO_VENDOR_CLARIFICATION",
            "REPROCESS_EXTRACTION",
            "ESCALATE_TO_MANAGER",
        ],
        "default_fallback_recommendation": "SEND_TO_AP_REVIEW",
        "output_schema_name": "AgentOutputSchema",
        "output_schema_version": "v1",
        "lifecycle_status": "active",
        "owner_team": "AP Automation",
        "capability_tags": ["understanding", "routing"],
        "domain_tags": ["exceptions", "reconciliation"],
        "human_review_required_conditions": "HIGH severity exceptions or ESCALATE_TO_MANAGER recommendation",
    },
    {
        "agent_type": AgentType.REVIEW_ROUTING,
        "purpose": "Determine correct review queue, team, and priority for the case",
        "entry_conditions": "exception analysis complete, routing decision needed",
        "success_criteria": "Routing decision made with high confidence based on prior analysis",
        "prohibited_actions": ["AUTO_CLOSE", "REPROCESS_EXTRACTION"],
        "requires_tool_grounding": False,
        "min_tool_calls": 0,
        "tool_failure_confidence_cap": None,
        "allowed_recommendation_types": [
            "SEND_TO_AP_REVIEW",
            "SEND_TO_PROCUREMENT",
            "SEND_TO_VENDOR_CLARIFICATION",
            "ESCALATE_TO_MANAGER",
        ],
        "default_fallback_recommendation": "SEND_TO_AP_REVIEW",
        "output_schema_name": "AgentOutputSchema",
        "output_schema_version": "v1",
        "lifecycle_status": "active",
        "owner_team": "AP Automation",
        "capability_tags": ["routing"],
        "domain_tags": ["review", "case"],
        "human_review_required_conditions": "always - this agent assigns human review",
    },
    {
        "agent_type": AgentType.CASE_SUMMARY,
        "purpose": "Produce human-readable case summary for AP reviewers",
        "entry_conditions": "all preceding agents have completed for this pipeline run",
        "success_criteria": "Clear summary produced covering invoice, PO, GRN, exceptions, recommendation",
        "prohibited_actions": ["AUTO_CLOSE", "REPROCESS_EXTRACTION"],
        "requires_tool_grounding": False,
        "min_tool_calls": 0,
        "tool_failure_confidence_cap": None,
        "allowed_recommendation_types": [
            "SEND_TO_AP_REVIEW",
            "SEND_TO_PROCUREMENT",
            "SEND_TO_VENDOR_CLARIFICATION",
            "ESCALATE_TO_MANAGER",
        ],
        "default_fallback_recommendation": "SEND_TO_AP_REVIEW",
        "output_schema_name": "AgentOutputSchema",
        "output_schema_version": "v1",
        "lifecycle_status": "active",
        "owner_team": "AP Automation",
        "capability_tags": ["summary"],
        "domain_tags": ["case", "review"],
        "human_review_required_conditions": "always - summary is produced for human reviewer",
    },
]

# Fields this command manages. Fields NOT in this list are never touched.
CONTRACT_FIELDS = [
    "purpose",
    "entry_conditions",
    "success_criteria",
    "prohibited_actions",
    "requires_tool_grounding",
    "min_tool_calls",
    "tool_failure_confidence_cap",
    "allowed_recommendation_types",
    "default_fallback_recommendation",
    "output_schema_name",
    "output_schema_version",
    "lifecycle_status",
    "owner_team",
    "capability_tags",
    "domain_tags",
    "human_review_required_conditions",
]


class Command(BaseCommand):
    help = "Seed AgentDefinition records with catalog/contract metadata (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Print what would be changed without saving to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] No changes will be saved.\n"))

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for contract in CONTRACTS:
            agent_type = contract["agent_type"]
            defaults = {field: contract[field] for field in CONTRACT_FIELDS}

            existing = AgentDefinition.objects.filter(agent_type=agent_type).first()

            if existing is None:
                if dry_run:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  [CREATE] {agent_type} -- record does not exist, would create with contract."
                        )
                    )
                else:
                    AgentDefinition.objects.update_or_create(
                        agent_type=agent_type,
                        defaults=defaults,
                    )
                    self.stdout.write(self.style.SUCCESS(f"  [CREATED] {agent_type}"))
                created_count += 1
            else:
                changed_fields = []
                for field in CONTRACT_FIELDS:
                    current_value = getattr(existing, field)
                    new_value = defaults[field]
                    if current_value != new_value:
                        changed_fields.append(field)

                if changed_fields:
                    if dry_run:
                        self.stdout.write(
                            self.style.WARNING(
                                f"  [UPDATE] {agent_type} -- would update: {', '.join(changed_fields)}"
                            )
                        )
                    else:
                        for field in CONTRACT_FIELDS:
                            setattr(existing, field, defaults[field])
                        existing.save(update_fields=CONTRACT_FIELDS)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"  [UPDATED] {agent_type} -- fields: {', '.join(changed_fields)}"
                            )
                        )
                    updated_count += 1
                else:
                    self.stdout.write(f"  [OK]      {agent_type} -- already up to date")
                    skipped_count += 1

        action = "Would affect" if dry_run else "Done."
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{action} {created_count} created, {updated_count} updated, {skipped_count} unchanged."
            )
        )
