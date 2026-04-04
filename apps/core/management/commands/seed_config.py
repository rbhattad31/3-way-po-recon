"""
Management command: seed_config

Seeds ONLY users and platform configuration:
  - Users (6 system accounts across all roles)
  - AgentDefinition records (7 agent types)
  - ToolDefinition records (6 tools)
  - ReconciliationConfig (default config with mode resolver)
  - ReconciliationPolicy rules (7 policies)

Does NOT create business/transactional data (vendors, POs, GRNs, invoices, etc.).

Usage:
    python manage.py seed_config
    python manage.py seed_config --flush   # delete config records and re-create
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.agents.models import AgentDefinition
from apps.core.enums import (
    AgentType,
    ReconciliationMode,
    UserRole,
)
from apps.reconciliation.models import ReconciliationConfig, ReconciliationPolicy
from apps.tools.models import ToolDefinition


# ===================================================================
#  USERS
# ===================================================================

USERS_DATA = [
    {
        "email": "admin@bradsol.com",
        "first_name": "System",
        "last_name": "Admin",
        "role": UserRole.ADMIN,
        "is_staff": True,
        "is_superuser": True,
        "department": "IT",
    },
    {
        "email": "approcessor@bradsol.com",
        "first_name": "AP",
        "last_name": "Processor",
        "role": UserRole.AP_PROCESSOR,
        "department": "Accounts Payable",
    },
    {
        "email": "reviewer@bradsol.com",
        "first_name": "Review",
        "last_name": "Manager",
        "role": UserRole.REVIEWER,
        "department": "Procurement",
    },
    {
        "email": "finance@bradsol.com",
        "first_name": "Finance",
        "last_name": "Manager",
        "role": UserRole.FINANCE_MANAGER,
        "department": "Finance",
    },
    {
        "email": "auditor@bradsol.com",
        "first_name": "Internal",
        "last_name": "Auditor",
        "role": UserRole.AUDITOR,
        "department": "Internal Audit",
    },
]


def create_users():
    """Create or retrieve system users."""
    users = {}
    for data in USERS_DATA:
        email = data["email"]
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "role": data["role"],
                "is_staff": data.get("is_staff", False),
                "is_superuser": data.get("is_superuser", False),
                "department": data.get("department", ""),
            },
        )
        if created:
            user.set_password("admin123")
            user.save()
        key = email.split("@")[0].replace(".", "_")
        users[key] = user
    return users


# ===================================================================
#  AGENT DEFINITIONS
# ===================================================================

def create_agent_definitions(admin):
    agents = [
        {
            "agent_type": AgentType.INVOICE_EXTRACTION,
            "name": "Invoice Extraction Agent",
            "description": (
                "Extracts structured invoice data from OCR text using GPT-4o. "
                "Runs immediately after Azure Document Intelligence OCR as step 2 "
                "of the extraction pipeline. Returns structured JSON with header "
                "fields and line items."
            ),
            "config_json": {
                "allowed_tools": [],
                "temperature": 0.0,
                "response_format": "json_object",
            },
        },
        {
            "agent_type": AgentType.INVOICE_UNDERSTANDING,
            "name": "Invoice Understanding Agent",
            "description": (
                "Validates extraction quality post-persistence. Uses tools to "
                "cross-check extracted fields against PO/vendor data, identifies "
                "OCR errors, and confirms field accuracy."
            ),
            "config_json": {
                "allowed_tools": ["invoice_details", "po_lookup", "vendor_search"],
                "confidence_threshold": 0.75,
            },
        },
        {
            "agent_type": AgentType.PO_RETRIEVAL,
            "name": "PO Retrieval Agent",
            "description": (
                "Attempts to find the correct Purchase Order when deterministic "
                "lookup fails. Uses normalized PO search, vendor-based discovery, "
                "and amount-based matching."
            ),
            "config_json": {
                "allowed_tools": ["po_lookup", "vendor_search", "invoice_details"],
                "max_candidates": 5,
            },
        },
        {
            "agent_type": AgentType.GRN_RETRIEVAL,
            "name": "GRN Specialist Agent",
            "description": (
                "Retrieves and analyzes GRN data for a PO. Handles multi-GRN "
                "aggregation, partial receipts, and missing GRN scenarios."
            ),
            "config_json": {
                "allowed_tools": ["grn_lookup", "po_lookup", "invoice_details"],
            },
        },
        {
            "agent_type": AgentType.RECONCILIATION_ASSIST,
            "name": "Reconciliation Assist Agent",
            "description": (
                "Provides detailed reconciliation analysis with line-by-line "
                "comparison and variance explanation."
            ),
            "config_json": {
                "allowed_tools": [
                    "reconciliation_summary", "invoice_details",
                    "po_lookup", "grn_lookup", "exception_list",
                ],
            },
        },
        {
            "agent_type": AgentType.EXCEPTION_ANALYSIS,
            "name": "Exception Analysis Agent",
            "description": (
                "Analyzes reconciliation exceptions, determines root causes, "
                "and recommends resolution actions."
            ),
            "config_json": {
                "allowed_tools": [
                    "exception_list", "invoice_details",
                    "po_lookup", "grn_lookup", "reconciliation_summary",
                ],
            },
        },
        {
            "agent_type": AgentType.REVIEW_ROUTING,
            "name": "Review Routing Agent",
            "description": (
                "Determines the optimal reviewer or team for a reconciliation "
                "case based on exception types, amounts, and complexity."
            ),
            "config_json": {
                "allowed_tools": ["exception_list", "reconciliation_summary"],
            },
        },
        {
            "agent_type": AgentType.CASE_SUMMARY,
            "name": "Case Summary Agent",
            "description": (
                "Generates a comprehensive summary of a reconciliation case "
                "including all findings, agent decisions, and recommendations."
            ),
            "config_json": {
                "allowed_tools": [
                    "reconciliation_summary", "exception_list",
                    "invoice_details", "po_lookup", "grn_lookup",
                ],
            },
        },
        # Deterministic system agents
        {
            "agent_type": AgentType.SYSTEM_REVIEW_ROUTING,
            "name": "System Review Routing Agent",
            "description": (
                "Deterministic system agent that applies rule-based review "
                "routing logic. Wraps the DeterministicResolver to produce "
                "routing recommendations without LLM calls."
            ),
            "config_json": {"allowed_tools": [], "execution_mode": "deterministic"},
        },
        {
            "agent_type": AgentType.SYSTEM_CASE_SUMMARY,
            "name": "System Case Summary Agent",
            "description": (
                "Deterministic system agent that generates case summaries "
                "from reconciliation data. Wraps the DeterministicResolver "
                "summary builder without LLM calls."
            ),
            "config_json": {"allowed_tools": [], "execution_mode": "deterministic"},
        },
        {
            "agent_type": AgentType.SYSTEM_BULK_EXTRACTION_INTAKE,
            "name": "System Bulk Extraction Intake Agent",
            "description": (
                "Deterministic system agent representing bulk extraction "
                "intake job orchestration. Records scan, register, and "
                "dispatch outcomes for governance visibility."
            ),
            "config_json": {"allowed_tools": [], "execution_mode": "deterministic"},
        },
        {
            "agent_type": AgentType.SYSTEM_CASE_INTAKE,
            "name": "System Case Intake Agent",
            "description": (
                "Deterministic system agent representing case creation and "
                "initialization. Records case shell creation, priority "
                "derivation, and stage initialization."
            ),
            "config_json": {"allowed_tools": [], "execution_mode": "deterministic"},
        },
        {
            "agent_type": AgentType.SYSTEM_POSTING_PREPARATION,
            "name": "System Posting Preparation Agent",
            "description": (
                "Deterministic system agent representing posting preparation "
                "and mapping orchestration. Records vendor/item resolution "
                "outcomes and posting readiness."
            ),
            "config_json": {"allowed_tools": [], "execution_mode": "deterministic"},
        },
    ]

    created = 0
    for agent_data in agents:
        _, was_created = AgentDefinition.objects.get_or_create(
            agent_type=agent_data["agent_type"],
            defaults={
                "name": agent_data["name"],
                "description": agent_data["description"],
                "enabled": True,
                "config_json": agent_data["config_json"],
                "created_by": admin,
            },
        )
        if was_created:
            created += 1
    return created


# ===================================================================
#  TOOL DEFINITIONS
# ===================================================================

def create_tool_definitions(admin):
    tools = [
        {
            "name": "po_lookup",
            "description": "Look up a Purchase Order by PO number. Returns header details and line items.",
            "module_path": "apps.tools.registry.tools.POLookupTool",
            "input_schema": {
                "type": "object",
                "properties": {"po_number": {"type": "string"}},
                "required": ["po_number"],
            },
        },
        {
            "name": "grn_lookup",
            "description": "Retrieve Goods Receipt Notes for a Purchase Order. Returns GRN details and line items.",
            "module_path": "apps.tools.registry.tools.GRNLookupTool",
            "input_schema": {
                "type": "object",
                "properties": {"po_number": {"type": "string"}},
                "required": ["po_number"],
            },
        },
        {
            "name": "vendor_search",
            "description": "Search for vendors by name, code, or alias. Returns matching vendors with similarity scores.",
            "module_path": "apps.tools.registry.tools.VendorSearchTool",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
        {
            "name": "invoice_details",
            "description": "Get full invoice details including line items, extraction data, and status.",
            "module_path": "apps.tools.registry.tools.InvoiceDetailsTool",
            "input_schema": {
                "type": "object",
                "properties": {"invoice_id": {"type": "integer"}},
                "required": ["invoice_id"],
            },
        },
        {
            "name": "exception_list",
            "description": "List all exceptions for a reconciliation result with type, severity, and details.",
            "module_path": "apps.tools.registry.tools.ExceptionListTool",
            "input_schema": {
                "type": "object",
                "properties": {"result_id": {"type": "integer"}},
                "required": ["result_id"],
            },
        },
        {
            "name": "reconciliation_summary",
            "description": "Get reconciliation summary for a result including match status, scores, and line comparisons.",
            "module_path": "apps.tools.registry.tools.ReconciliationSummaryTool",
            "input_schema": {
                "type": "object",
                "properties": {"result_id": {"type": "integer"}},
                "required": ["result_id"],
            },
        },
    ]

    created = 0
    for tool_data in tools:
        _, was_created = ToolDefinition.objects.get_or_create(
            name=tool_data["name"],
            defaults={
                "description": tool_data["description"],
                "module_path": tool_data["module_path"],
                "input_schema": tool_data["input_schema"],
                "enabled": True,
                "created_by": admin,
            },
        )
        if was_created:
            created += 1
    return created


# ===================================================================
#  RECONCILIATION CONFIG
# ===================================================================

def create_recon_config():
    config, _ = ReconciliationConfig.objects.get_or_create(
        is_default=True,
        defaults={"name": "Default"},
    )
    config.quantity_tolerance_pct = 2.0
    config.price_tolerance_pct = 1.0
    config.amount_tolerance_pct = 1.0
    config.auto_close_qty_tolerance_pct = 5.0
    config.auto_close_price_tolerance_pct = 3.0
    config.auto_close_amount_tolerance_pct = 3.0
    config.auto_close_on_match = True
    config.enable_agents = True
    config.extraction_confidence_threshold = 0.75
    config.enable_mode_resolver = True
    config.enable_two_way_for_services = True
    config.enable_grn_for_stock_items = True
    config.default_reconciliation_mode = ReconciliationMode.THREE_WAY
    config.ap_processor_sees_all_cases = False
    config.save()
    return config


# ===================================================================
#  RECONCILIATION POLICIES
# ===================================================================

def create_policies(admin):
    policies = [
        {
            "policy_code": "POL-SVC-VENDOR",
            "policy_name": "Service Vendor - 2-Way",
            "reconciliation_mode": ReconciliationMode.TWO_WAY,
            "is_service_invoice": True,
            "priority": 10,
            "notes": "Service vendor invoices skip GRN verification.",
        },
        {
            "policy_code": "POL-SVC-GLOBAL",
            "policy_name": "Service Invoices - 2-Way",
            "reconciliation_mode": ReconciliationMode.TWO_WAY,
            "is_service_invoice": True,
            "priority": 20,
            "notes": "Any invoice flagged as service -> 2-Way reconciliation.",
        },
        {
            "policy_code": "POL-STOCK-GLOBAL",
            "policy_name": "Stock/Inventory Invoices - 3-Way",
            "reconciliation_mode": ReconciliationMode.THREE_WAY,
            "is_stock_invoice": True,
            "priority": 30,
            "notes": "Any invoice flagged as stock/inventory -> 3-Way with GRN.",
        },
        {
            "policy_code": "POL-FOOD-3WAY",
            "policy_name": "Food Category - 3-Way",
            "reconciliation_mode": ReconciliationMode.THREE_WAY,
            "item_category": "Food",
            "priority": 40,
            "notes": "Food items always require GRN verification.",
        },
        {
            "policy_code": "POL-LOGISTICS-2WAY",
            "policy_name": "Logistics & Transport - 2-Way",
            "reconciliation_mode": ReconciliationMode.TWO_WAY,
            "item_category": "Logistics",
            "priority": 50,
            "notes": "Logistics/transport services - no GRN needed.",
        },
        {
            "policy_code": "POL-WH-RUH-3WAY",
            "policy_name": "Riyadh Warehouse - 3-Way",
            "reconciliation_mode": ReconciliationMode.THREE_WAY,
            "location_code": "WH-RUH-01",
            "priority": 60,
            "notes": "All shipments to Riyadh Central Warehouse require GRN.",
        },
        {
            "policy_code": "POL-BRANCH-2WAY",
            "policy_name": "Direct Branch Purchases - 2-Way",
            "reconciliation_mode": ReconciliationMode.TWO_WAY,
            "business_unit": "Branch Operations",
            "priority": 70,
            "notes": "Branch direct purchases (services/small items) - no GRN.",
        },
    ]

    created = 0
    for pdata in policies:
        _, was_created = ReconciliationPolicy.objects.get_or_create(
            policy_code=pdata["policy_code"],
            defaults={
                "policy_name": pdata["policy_name"],
                "reconciliation_mode": pdata["reconciliation_mode"],
                "is_service_invoice": pdata.get("is_service_invoice"),
                "is_stock_invoice": pdata.get("is_stock_invoice"),
                "item_category": pdata.get("item_category", ""),
                "location_code": pdata.get("location_code", ""),
                "business_unit": pdata.get("business_unit", ""),
                "priority": pdata["priority"],
                "notes": pdata["notes"],
                "is_active": True,
                "created_by": admin,
            },
        )
        if was_created:
            created += 1
    return created


# ===================================================================
#  COMMAND
# ===================================================================

class Command(BaseCommand):
    help = "Seed users and platform configuration (agents, tools, recon config, policies)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete config records and re-create",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["flush"]:
            self._flush()

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n=== Seed Config: Users & Platform Configuration ===\n"
        ))

        # 1. Users
        self.stdout.write("  Creating users...")
        users = create_users()
        admin = users["admin"]
        self.stdout.write(self.style.SUCCESS(f"    [OK] {len(users)} users ready"))

        # 2. Agent definitions
        self.stdout.write("  Creating agent definitions...")
        agent_count = create_agent_definitions(admin)
        self.stdout.write(self.style.SUCCESS(f"    [OK] {agent_count} new agent definitions"))

        # 3. Tool definitions
        self.stdout.write("  Creating tool definitions...")
        tool_count = create_tool_definitions(admin)
        self.stdout.write(self.style.SUCCESS(f"    [OK] {tool_count} new tool definitions"))

        # 4. Reconciliation config
        self.stdout.write("  Creating reconciliation config...")
        create_recon_config()
        self.stdout.write(self.style.SUCCESS("    [OK] Default config ready"))

        # 5. Reconciliation policies
        self.stdout.write("  Creating reconciliation policies...")
        policy_count = create_policies(admin)
        self.stdout.write(self.style.SUCCESS(f"    [OK] {policy_count} new policies"))

        # Summary
        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Summary ==="))
        self.stdout.write(f"  Users:              {User.objects.count()}")
        self.stdout.write(f"  Agent Definitions:  {AgentDefinition.objects.count()}")
        self.stdout.write(f"  Tool Definitions:   {ToolDefinition.objects.count()}")
        self.stdout.write(f"  Recon Configs:      {ReconciliationConfig.objects.count()}")
        self.stdout.write(f"  Recon Policies:     {ReconciliationPolicy.objects.count()}")
        self.stdout.write("")

    def _flush(self):
        self.stdout.write(self.style.WARNING("  Flushing config data..."))
        ReconciliationPolicy.objects.all().delete()
        ReconciliationConfig.objects.all().delete()
        ToolDefinition.objects.all().delete()
        AgentDefinition.objects.all().delete()
        # Don't delete users — they may be referenced by other records
        self.stdout.write(self.style.SUCCESS("    [OK] Config data flushed"))
