"""
Seed RBAC roles, permissions, role-permission matrix, and sync existing users.

Usage:
    python manage.py seed_rbac
    python manage.py seed_rbac --sync-users
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.rbac_models import Role, Permission, RolePermission, UserRole
from apps.accounts.models import User


# ---------------------------------------------------------------------------
# Default role definitions
# ---------------------------------------------------------------------------
ROLES = [
    {"code": "ADMIN", "name": "Admin", "description": "Full system access", "is_system_role": True, "rank": 10},
    {"code": "AP_PROCESSOR", "name": "AP Processor", "description": "Accounts payable processor – manage invoices, run reconciliation", "is_system_role": True, "rank": 50},
    {"code": "REVIEWER", "name": "Reviewer", "description": "Review reconciliation results and make decisions", "is_system_role": True, "rank": 40},
    {"code": "FINANCE_MANAGER", "name": "Finance Manager", "description": "Manage reviews, override reconciliation, supervise AP operations", "is_system_role": True, "rank": 20},
    {"code": "AUDITOR", "name": "Auditor", "description": "Read-only access for audit and compliance review", "is_system_role": True, "rank": 30},
    {"code": "SYSTEM_AGENT", "name": "System Agent", "description": "Dedicated least-privilege identity for autonomous agent operations", "is_system_role": True, "rank": 100},
    # Procurement roles
    {"code": "PROCUREMENT_MANAGER", "name": "Procurement Manager", "description": "Supervise procurement operations, review high-risk results, full control", "is_system_role": True, "rank": 25},
    {"code": "CATEGORY_MANAGER", "name": "Category Manager", "description": "Domain expert — manage category-specific rules, benchmarks, review results", "is_system_role": True, "rank": 35},
    {"code": "PROCUREMENT_BUYER", "name": "Procurement Buyer", "description": "Create requests, upload quotations, trigger analysis — operational buyer", "is_system_role": True, "rank": 55},
]

# ---------------------------------------------------------------------------
# Default permission catalog
# ---------------------------------------------------------------------------
PERMISSIONS = [
    # Invoices
    {"code": "invoices.view", "name": "View Invoices", "module": "invoices", "action": "view", "description": "View invoice list and details"},
    {"code": "invoices.create", "name": "Create Invoices", "module": "invoices", "action": "create", "description": "Upload and create new invoices"},
    {"code": "invoices.edit", "name": "Edit Invoices", "module": "invoices", "action": "edit", "description": "Edit invoice data and metadata"},
    {"code": "invoices.delete", "name": "Delete Invoices", "module": "invoices", "action": "delete", "description": "Soft-delete invoices"},
    {"code": "invoices.trigger_reconciliation", "name": "Trigger Reconciliation", "module": "invoices", "action": "trigger_reconciliation", "description": "Initiate reconciliation for invoices"},
    # Reconciliation
    {"code": "reconciliation.view", "name": "View Reconciliation", "module": "reconciliation", "action": "view", "description": "View reconciliation runs and results"},
    {"code": "reconciliation.run", "name": "Run Reconciliation", "module": "reconciliation", "action": "run", "description": "Execute reconciliation matching"},
    {"code": "reconciliation.override", "name": "Override Reconciliation", "module": "reconciliation", "action": "override", "description": "Override reconciliation decisions and thresholds"},
    # Cases
    {"code": "cases.view", "name": "View Cases", "module": "cases", "action": "view", "description": "View AP cases"},
    {"code": "cases.edit", "name": "Edit Cases", "module": "cases", "action": "edit", "description": "Edit AP case data"},
    {"code": "cases.add_comment", "name": "Add Case Comment", "module": "cases", "action": "add_comment", "description": "Add comments to AP cases"},
    {"code": "cases.assign", "name": "Assign Cases", "module": "cases", "action": "assign", "description": "Assign AP cases to users"},
    # Reviews
    {"code": "reviews.view", "name": "View Reviews", "module": "reviews", "action": "view", "description": "View review assignments and decisions"},
    {"code": "reviews.decide", "name": "Decide Reviews", "module": "reviews", "action": "decide", "description": "Approve or reject review assignments"},
    {"code": "reviews.assign", "name": "Assign Reviews", "module": "reviews", "action": "assign", "description": "Create and assign review assignments"},
    # Governance
    {"code": "governance.view", "name": "View Governance", "module": "governance", "action": "view", "description": "View audit logs and governance dashboard"},
    # Agents
    {"code": "agents.view", "name": "View Agents", "module": "agents", "action": "view", "description": "View agent definitions and monitoring"},
    {"code": "agents.use_copilot", "name": "Use Copilot", "module": "agents", "action": "use_copilot", "description": "Interact with AI copilot agents"},
    {"code": "agents.orchestrate", "name": "Orchestrate Agents", "module": "agents", "action": "orchestrate", "description": "Trigger the agent pipeline for reconciliation results"},
    {"code": "agents.run_extraction", "name": "Run Extraction Agent", "module": "agents", "action": "run_extraction", "description": "Execute invoice extraction / understanding agents"},
    {"code": "agents.run_po_retrieval", "name": "Run PO Retrieval Agent", "module": "agents", "action": "run_po_retrieval", "description": "Execute PO retrieval agent"},
    {"code": "agents.run_grn_retrieval", "name": "Run GRN Retrieval Agent", "module": "agents", "action": "run_grn_retrieval", "description": "Execute GRN retrieval agent"},
    {"code": "agents.run_reconciliation_assist", "name": "Run Reconciliation Assist Agent", "module": "agents", "action": "run_reconciliation_assist", "description": "Execute reconciliation assist agent"},
    {"code": "agents.run_exception_analysis", "name": "Run Exception Analysis Agent", "module": "agents", "action": "run_exception_analysis", "description": "Execute exception analysis agent"},
    {"code": "agents.run_review_routing", "name": "Run Review Routing Agent", "module": "agents", "action": "run_review_routing", "description": "Execute review routing agent"},
    {"code": "agents.run_case_summary", "name": "Run Case Summary Agent", "module": "agents", "action": "run_case_summary", "description": "Execute case summary agent"},
    # Recommendations
    {"code": "recommendations.auto_close", "name": "Accept Auto-Close", "module": "recommendations", "action": "auto_close", "description": "Accept or trigger auto-close recommendations"},
    {"code": "recommendations.route_review", "name": "Route to Review", "module": "recommendations", "action": "route_review", "description": "Accept send-to-review recommendations"},
    {"code": "recommendations.escalate", "name": "Escalate", "module": "recommendations", "action": "escalate", "description": "Accept escalation recommendations"},
    {"code": "recommendations.reprocess", "name": "Reprocess Extraction", "module": "recommendations", "action": "reprocess", "description": "Accept reprocess-extraction recommendations"},
    {"code": "recommendations.route_procurement", "name": "Route to Procurement", "module": "recommendations", "action": "route_procurement", "description": "Accept send-to-procurement recommendations"},
    {"code": "recommendations.vendor_clarification", "name": "Vendor Clarification", "module": "recommendations", "action": "vendor_clarification", "description": "Accept vendor clarification recommendations"},
    # Protected actions
    {"code": "cases.escalate", "name": "Escalate Cases", "module": "cases", "action": "escalate", "description": "Escalate AP cases to higher authority"},
    {"code": "extraction.reprocess", "name": "Reprocess Extraction", "module": "extraction", "action": "reprocess", "description": "Re-trigger invoice extraction"},
    {"code": "extraction.approve", "name": "Approve Extraction", "module": "extraction", "action": "approve", "description": "Approve extracted invoice data before reconciliation"},
    {"code": "extraction.reject", "name": "Reject Extraction", "module": "extraction", "action": "reject", "description": "Reject extracted data and request re-extraction"},
    # Document scoping
    {"code": "purchase_orders.view", "name": "View Purchase Orders", "module": "purchase_orders", "action": "view", "description": "View purchase order data"},
    {"code": "grns.view", "name": "View GRNs", "module": "grns", "action": "view", "description": "View goods receipt note data"},
    {"code": "vendors.view", "name": "View Vendors", "module": "vendors", "action": "view", "description": "View vendor data"},
    # Configuration
    {"code": "config.manage", "name": "Manage Configuration", "module": "config", "action": "manage", "description": "Manage system configuration and settings"},
    # User management
    {"code": "users.manage", "name": "Manage Users", "module": "users", "action": "manage", "description": "Create, edit, and deactivate user accounts"},
    # Role management
    {"code": "roles.manage", "name": "Manage Roles", "module": "roles", "action": "manage", "description": "Create, edit roles and manage role-permission matrix"},
    # Procurement
    {"code": "procurement.view", "name": "View Procurement Requests", "module": "procurement", "action": "view", "description": "View procurement requests, attributes, and quotations"},
    {"code": "procurement.create", "name": "Create Procurement Requests", "module": "procurement", "action": "create", "description": "Create new procurement requests"},
    {"code": "procurement.edit", "name": "Edit Procurement Requests", "module": "procurement", "action": "edit", "description": "Edit requests and manage attributes"},
    {"code": "procurement.delete", "name": "Delete Procurement Requests", "module": "procurement", "action": "delete", "description": "Delete procurement requests"},
    {"code": "procurement.run_analysis", "name": "Run Procurement Analysis", "module": "procurement", "action": "run_analysis", "description": "Trigger recommendation and benchmark analysis runs"},
    {"code": "procurement.manage_quotations", "name": "Manage Quotations", "module": "procurement", "action": "manage_quotations", "description": "Upload and manage supplier quotations"},
    {"code": "procurement.view_results", "name": "View Analysis Results", "module": "procurement", "action": "view_results", "description": "View recommendation, benchmark, and compliance results"},
    # Credits
    {"code": "credits.view", "name": "View Credits", "module": "credits", "action": "view", "description": "View credit accounts and balances"},
    {"code": "credits.manage", "name": "Manage Credits", "module": "credits", "action": "manage", "description": "Allocate, adjust, and manage user credit accounts"},
    # Bulk Extraction
    {"code": "extraction.bulk_view", "name": "View Bulk Extraction", "module": "extraction", "action": "bulk_view", "description": "View bulk extraction jobs and items"},
    {"code": "extraction.bulk_create", "name": "Create Bulk Extraction", "module": "extraction", "action": "bulk_create", "description": "Start new bulk extraction jobs"},
]

# ---------------------------------------------------------------------------
# Default role-permission matrix
# ---------------------------------------------------------------------------
# ADMIN gets everything (handled in code: admin bypass), but we also
# explicitly grant all permissions for visibility in the matrix UI.
ROLE_MATRIX = {
    "ADMIN": [p["code"] for p in PERMISSIONS],  # everything
    "AP_PROCESSOR": [
        "invoices.view", "invoices.create", "invoices.edit",
        "invoices.trigger_reconciliation",
        "reconciliation.view", "reconciliation.run",
        "cases.view", "cases.edit",
        "cases.add_comment",
        "reviews.view",
        "agents.view", "agents.use_copilot",
        "purchase_orders.view", "grns.view", "vendors.view",
        "extraction.reject", "extraction.reprocess",
        "extraction.bulk_view", "extraction.bulk_create",
    ],
    "REVIEWER": [
        "invoices.view", "invoices.create",
        "reconciliation.view",
        "cases.view",
        "cases.add_comment",
        "reviews.view", "reviews.decide",
        "agents.view", "agents.use_copilot",
        "governance.view",
        "purchase_orders.view", "grns.view", "vendors.view",
        "recommendations.route_review",
        "extraction.approve", "extraction.reject",
    ],
    "FINANCE_MANAGER": [
        "invoices.view",
        "reconciliation.view", "reconciliation.override",
        "cases.view", "cases.assign", "cases.escalate",
        "cases.add_comment",
        "reviews.view", "reviews.assign", "reviews.decide",
        "governance.view",
        "agents.view", "agents.orchestrate",
        "users.manage", "roles.manage",
        "purchase_orders.view", "grns.view", "vendors.view",
        "recommendations.auto_close", "recommendations.route_review",
        "recommendations.escalate", "recommendations.reprocess",
        "recommendations.route_procurement", "recommendations.vendor_clarification",
        "extraction.approve", "extraction.reject", "extraction.reprocess",
        "extraction.bulk_view", "extraction.bulk_create",
        # Procurement oversight
        "procurement.view", "procurement.view_results",
        # Credits
        "credits.view", "credits.manage",
    ],
    "AUDITOR": [
        "invoices.view",
        "reconciliation.view",
        "cases.view",
        "reviews.view",
        "governance.view",
        "agents.view",
        "purchase_orders.view", "grns.view", "vendors.view",
        # Procurement read-only
        "procurement.view", "procurement.view_results",
        # Bulk extraction read-only
        "extraction.bulk_view",
    ],
    "SYSTEM_AGENT": [
        # Scoped agent orchestration + execution
        "agents.orchestrate",
        "agents.run_extraction", "agents.run_po_retrieval",
        "agents.run_grn_retrieval", "agents.run_reconciliation_assist",
        "agents.run_exception_analysis", "agents.run_review_routing",
        "agents.run_case_summary",
        # Read access for tools
        "invoices.view", "reconciliation.view",
        "purchase_orders.view", "grns.view", "vendors.view",
        # Protected actions agents are allowed to take
        "recommendations.auto_close", "recommendations.route_review",
        "recommendations.escalate", "recommendations.reprocess",
        "recommendations.route_procurement", "recommendations.vendor_clarification",
        "cases.escalate", "extraction.reprocess",
        "extraction.approve", "extraction.reject",
        "extraction.bulk_view", "extraction.bulk_create",
        "reviews.assign",
        # Procurement (automated pipeline)
        "procurement.view", "procurement.run_analysis", "procurement.view_results",
    ],
    # --- Procurement roles ---
    "PROCUREMENT_MANAGER": [
        "procurement.view", "procurement.create", "procurement.edit",
        "procurement.delete", "procurement.run_analysis",
        "procurement.manage_quotations", "procurement.view_results",
    ],
    "CATEGORY_MANAGER": [
        "procurement.view", "procurement.create", "procurement.edit",
        "procurement.run_analysis", "procurement.view_results",
    ],
    "PROCUREMENT_BUYER": [
        "procurement.view", "procurement.create", "procurement.edit",
        "procurement.run_analysis", "procurement.manage_quotations",
        "procurement.view_results",
    ],
}


class Command(BaseCommand):
    help = "Seed RBAC roles, permissions, and role-permission matrix"

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync-users", action="store_true",
            help="Also sync existing users from legacy User.role into UserRole table",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("Seeding RBAC data..."))

        # 1. Create roles
        role_objs = {}
        for role_data in ROLES:
            obj, created = Role.objects.update_or_create(
                code=role_data["code"],
                defaults=role_data,
            )
            role_objs[obj.code] = obj
            status = "CREATED" if created else "EXISTS"
            self.stdout.write(f"  Role {obj.code}: {status}")

        # 2. Create permissions
        perm_objs = {}
        for perm_data in PERMISSIONS:
            obj, created = Permission.objects.update_or_create(
                code=perm_data["code"],
                defaults=perm_data,
            )
            perm_objs[obj.code] = obj
            status = "CREATED" if created else "EXISTS"
            self.stdout.write(f"  Permission {obj.code}: {status}")

        # 3. Create role-permission matrix
        rp_created = 0
        for role_code, perm_codes in ROLE_MATRIX.items():
            role = role_objs.get(role_code)
            if not role:
                continue
            for perm_code in perm_codes:
                perm = perm_objs.get(perm_code)
                if not perm:
                    continue
                _, created = RolePermission.objects.get_or_create(
                    role=role, permission=perm,
                    defaults={"is_allowed": True},
                )
                if created:
                    rp_created += 1
        self.stdout.write(f"  RolePermission mappings created: {rp_created}")

        # 4. Sync existing users (optional)
        if options["sync_users"]:
            self._sync_users(role_objs)

        self.stdout.write(self.style.SUCCESS("RBAC seed complete."))

    def _sync_users(self, role_objs):
        """Create UserRole records for existing users based on their legacy role field."""
        users = User.objects.all()
        synced = 0
        for user in users:
            role = role_objs.get(user.role)
            if not role:
                self.stdout.write(
                    self.style.WARNING(f"  Skipping {user.email}: no matching role for '{user.role}'")
                )
                continue
            _, created = UserRole.objects.get_or_create(
                user=user, role=role,
                defaults={"is_primary": True, "is_active": True},
            )
            if created:
                synced += 1
                self.stdout.write(f"  Synced {user.email} → {role.code} (primary)")
            else:
                # Ensure it's marked primary if not already
                UserRole.objects.filter(user=user, role=role).update(is_primary=True)
        self.stdout.write(f"  User sync complete: {synced} new, {users.count()} total checked")
