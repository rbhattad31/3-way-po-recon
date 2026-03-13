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
    # Configuration
    {"code": "config.manage", "name": "Manage Configuration", "module": "config", "action": "manage", "description": "Manage system configuration and settings"},
    # User management
    {"code": "users.manage", "name": "Manage Users", "module": "users", "action": "manage", "description": "Create, edit, and deactivate user accounts"},
    # Role management
    {"code": "roles.manage", "name": "Manage Roles", "module": "roles", "action": "manage", "description": "Create, edit roles and manage role-permission matrix"},
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
        "reviews.view",
        "agents.view", "agents.use_copilot",
    ],
    "REVIEWER": [
        "invoices.view",
        "reconciliation.view",
        "cases.view",
        "reviews.view", "reviews.decide",
        "agents.view", "agents.use_copilot",
        "governance.view",
    ],
    "FINANCE_MANAGER": [
        "invoices.view",
        "reconciliation.view", "reconciliation.override",
        "cases.view", "cases.assign",
        "reviews.view", "reviews.assign", "reviews.decide",
        "governance.view",
        "agents.view",
        "users.manage", "roles.manage",
    ],
    "AUDITOR": [
        "invoices.view",
        "reconciliation.view",
        "cases.view",
        "reviews.view",
        "governance.view",
        "agents.view",
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
