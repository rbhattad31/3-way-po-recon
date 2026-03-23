"""Seed the 'extraction.correct' permission and assign to roles that currently
have 'invoices.create' — ensures the permission cleanup (invoices.create →
extraction.correct for edit-values views) has no disruption."""

from django.db import migrations


def seed_permission(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    perm, _ = Permission.objects.get_or_create(
        code="extraction.correct",
        defaults={
            "name": "Correct extracted values",
            "module": "extraction",
            "action": "correct",
            "description": "Allows editing / correcting extracted invoice field values.",
            "is_active": True,
        },
    )

    # Mirror extraction.correct to every role that already has invoices.create
    invoices_create = Permission.objects.filter(code="invoices.create").first()
    if invoices_create:
        for rp in RolePermission.objects.filter(permission=invoices_create):
            RolePermission.objects.get_or_create(
                role=rp.role, permission=perm,
            )


def reverse_permission(apps, schema_editor):
    Permission = apps.get_model("accounts", "Permission")
    Permission.objects.filter(code="extraction.correct").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("extraction", "0006_add_extraction_run_fk"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_permission, reverse_permission),
    ]
