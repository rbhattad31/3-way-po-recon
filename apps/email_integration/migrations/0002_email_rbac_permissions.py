from django.db import migrations


EMAIL_PERMISSIONS = [
    ("email.view", "View email integration", "email", "view"),
    ("email.manage", "Manage email integration", "email", "manage"),
    ("email.read_thread", "Read email threads", "email", "read_thread"),
    ("email.read_attachment", "Read email attachments", "email", "read_attachment"),
    ("email.send", "Send email", "email", "send"),
    ("email.manage_mailboxes", "Manage mailboxes", "email", "manage_mailboxes"),
    ("email.route", "Route email messages", "email", "route"),
    ("email.triage", "Triage email messages", "email", "triage"),
]


ROLE_PERMISSIONS = {
    "SUPER_ADMIN": {code for code, _, _, _ in EMAIL_PERMISSIONS},
    "ADMIN": {code for code, _, _, _ in EMAIL_PERMISSIONS},
    "SYSTEM_AGENT": {code for code, _, _, _ in EMAIL_PERMISSIONS},
    "AP_PROCESSOR": {"email.view", "email.read_thread", "email.read_attachment", "email.send", "email.route", "email.triage"},
    "FINANCE_MANAGER": {"email.view", "email.read_thread", "email.read_attachment", "email.send", "email.route", "email.triage"},
    "REVIEWER": {"email.view", "email.read_thread", "email.read_attachment"},
    "AUDITOR": {"email.view", "email.read_thread", "email.read_attachment"},
    "PROCUREMENT": {"email.view", "email.read_thread", "email.read_attachment", "email.send", "email.route", "email.triage"},
}


def seed_email_permissions(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")
    RolePermission = apps.get_model("accounts", "RolePermission")

    permission_by_code = {}
    for code, name, module, action in EMAIL_PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "module": module,
                "action": action,
                "description": name,
                "is_active": True,
            },
        )
        permission_by_code[code] = permission

    for role_code, permission_codes in ROLE_PERMISSIONS.items():
        role = Role.objects.filter(code=role_code).first()
        if role is None:
            continue
        for permission_code in permission_codes:
            permission = permission_by_code.get(permission_code)
            if permission is None:
                continue
            RolePermission.objects.update_or_create(
                role=role,
                permission=permission,
                defaults={"is_allowed": True},
            )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_add_is_platform_admin"),
        ("email_integration", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_email_permissions, migrations.RunPython.noop),
    ]