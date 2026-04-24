from django.db import migrations


def add_system_export_mapping_permission(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")
    RolePermission = apps.get_model("accounts", "RolePermission")

    perm, _ = Permission.objects.update_or_create(
        code="agents.run_system_export_field_mapping",
        defaults={
            "name": "Run System Export Field Mapping Agent",
            "module": "agents",
            "action": "run_system_export_field_mapping",
            "description": "Allows execution of deterministic system export field mapping agent.",
            "is_active": True,
        },
    )

    role_codes = ["SUPER_ADMIN", "ADMIN", "SYSTEM_AGENT"]
    roles = Role.objects.filter(code__in=role_codes, is_active=True)
    for role in roles:
        RolePermission.objects.update_or_create(
            role=role,
            permission=perm,
            defaults={"is_allowed": True},
        )


def remove_system_export_mapping_permission(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    perm = Permission.objects.filter(code="agents.run_system_export_field_mapping").first()
    if not perm:
        return

    RolePermission.objects.filter(permission=perm).delete()
    perm.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_add_is_platform_admin"),
    ]

    operations = [
        migrations.RunPython(
            add_system_export_mapping_permission,
            remove_system_export_mapping_permission,
        ),
    ]
