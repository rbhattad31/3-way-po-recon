"""
Move ReviewAssignment, ReviewComment, ManualReviewAction, ReviewDecision
from the ``reviews`` app into ``cases``.

All four tables keep their original ``db_table`` names so **zero** DDL is
executed.  Only Django's internal state (``django_content_type``) is updated.

Companion migration: ``reviews.0005_transfer_to_cases`` (deletes models
from the reviews state).
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def update_content_types(apps, schema_editor):
    """Move ContentType rows from app_label='reviews' to 'cases'."""
    ContentType = apps.get_model("contenttypes", "ContentType")
    for model_name in (
        "reviewassignment",
        "reviewcomment",
        "manualreviewaction",
        "reviewdecision",
    ):
        # Delete any stale 'cases' CT that makemigrations may have created
        ContentType.objects.filter(app_label="cases", model=model_name).delete()
        ContentType.objects.filter(app_label="reviews", model=model_name).update(
            app_label="cases"
        )


def revert_content_types(apps, schema_editor):
    """Reverse: move ContentType rows back to 'reviews'."""
    ContentType = apps.get_model("contenttypes", "ContentType")
    for model_name in (
        "reviewassignment",
        "reviewcomment",
        "manualreviewaction",
        "reviewdecision",
    ):
        ContentType.objects.filter(app_label="cases", model=model_name).update(
            app_label="reviews"
        )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_add_is_platform_admin"),
        ("cases", "0009_add_compound_tenant_status_indexes"),
        ("reconciliation", "0015_receipt_availability_fields"),
        ("reviews", "0005_transfer_to_cases"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ------------------------------------------------------------------
        # 1. State-only: create the 4 review models in the cases app.
        #    No database changes -- tables already exist with correct names.
        # ------------------------------------------------------------------
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="ReviewAssignment",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL)),
                        ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_updated", to=settings.AUTH_USER_MODEL)),
                        ("reconciliation_result", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_assignments", to="reconciliation.reconciliationresult")),
                        ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="accounts.companyprofile")),
                        ("assigned_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="review_assignments", to=settings.AUTH_USER_MODEL)),
                        ("status", models.CharField(choices=[("PENDING", "Pending"), ("ASSIGNED", "Assigned"), ("IN_REVIEW", "In Review"), ("APPROVED", "Approved"), ("REJECTED", "Rejected"), ("REPROCESSED", "Reprocessed")], db_index=True, default="PENDING", max_length=20)),
                        ("priority", models.PositiveSmallIntegerField(default=5, help_text="1=highest, 10=lowest")),
                        ("due_date", models.DateTimeField(blank=True, null=True)),
                        ("notes", models.TextField(blank=True, default="")),
                        ("reviewer_summary", models.TextField(blank=True, default="")),
                        ("reviewer_risk_level", models.CharField(blank=True, choices=[("LOW", "Low"), ("MEDIUM", "Medium"), ("HIGH", "High")], default="", max_length=10)),
                        ("reviewer_confidence", models.FloatField(blank=True, null=True)),
                        ("reviewer_recommendation", models.CharField(blank=True, default="", max_length=30)),
                        ("reviewer_suggested_actions", models.JSONField(default=list)),
                        ("reviewer_summary_generated_at", models.DateTimeField(blank=True, null=True)),
                        ("reviewer_agreed_with_agent", models.BooleanField(blank=True, null=True)),
                    ],
                    options={
                        "verbose_name": "Review Assignment",
                        "verbose_name_plural": "Review Assignments",
                        "db_table": "reviews_assignment",
                        "ordering": ["priority", "-created_at"],
                    },
                ),
                migrations.CreateModel(
                    name="ReviewComment",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="comments", to="cases.reviewassignment")),
                        ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="accounts.companyprofile")),
                        ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                        ("body", models.TextField()),
                        ("is_internal", models.BooleanField(default=True, help_text="Internal vs. visible to vendor")),
                    ],
                    options={
                        "verbose_name": "Review Comment",
                        "verbose_name_plural": "Review Comments",
                        "db_table": "reviews_comment",
                        "ordering": ["created_at"],
                    },
                ),
                migrations.CreateModel(
                    name="ManualReviewAction",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="actions", to="cases.reviewassignment")),
                        ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="accounts.companyprofile")),
                        ("performed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                        ("action_type", models.CharField(choices=[("APPROVE", "Approve"), ("REJECT", "Reject"), ("REQUEST_INFO", "Request Info"), ("REPROCESS", "Reprocess"), ("ESCALATE", "Escalate"), ("CORRECT_FIELD", "Correct Field"), ("ADD_COMMENT", "Add Comment")], db_index=True, max_length=30)),
                        ("field_name", models.CharField(blank=True, default="", help_text="Field corrected, if applicable", max_length=100)),
                        ("old_value", models.TextField(blank=True, default="")),
                        ("new_value", models.TextField(blank=True, default="")),
                        ("reason", models.TextField(blank=True, default="")),
                    ],
                    options={
                        "verbose_name": "Manual Review Action",
                        "verbose_name_plural": "Manual Review Actions",
                        "db_table": "reviews_action",
                        "ordering": ["-created_at"],
                    },
                ),
                migrations.CreateModel(
                    name="ReviewDecision",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                        ("assignment", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="decision", to="cases.reviewassignment")),
                        ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="accounts.companyprofile")),
                        ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                        ("decision", models.CharField(choices=[("PENDING", "Pending"), ("ASSIGNED", "Assigned"), ("IN_REVIEW", "In Review"), ("APPROVED", "Approved"), ("REJECTED", "Rejected"), ("REPROCESSED", "Reprocessed")], max_length=20)),
                        ("reason", models.TextField(blank=True, default="")),
                        ("decided_at", models.DateTimeField(auto_now_add=True)),
                    ],
                    options={
                        "verbose_name": "Review Decision",
                        "verbose_name_plural": "Review Decisions",
                        "db_table": "reviews_decision",
                        "ordering": ["-decided_at"],
                    },
                ),

                # --- Indexes ---
                migrations.AddIndex(
                    model_name="reviewassignment",
                    index=models.Index(fields=["status"], name="idx_revassign_status"),
                ),
                migrations.AddIndex(
                    model_name="reviewassignment",
                    index=models.Index(fields=["assigned_to"], name="idx_revassign_user"),
                ),
                migrations.AddIndex(
                    model_name="reviewassignment",
                    index=models.Index(fields=["priority"], name="idx_revassign_priority"),
                ),
                migrations.AddIndex(
                    model_name="reviewassignment",
                    index=models.Index(fields=["tenant", "status"], name="idx_revassign_tenant_status"),
                ),
                migrations.AddIndex(
                    model_name="manualreviewaction",
                    index=models.Index(fields=["action_type"], name="idx_revaction_type"),
                ),

                # --- Fix the APCase FK to point at cases.ReviewAssignment ---
                migrations.AlterField(
                    model_name="apcase",
                    name="review_assignment",
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ap_cases",
                        to="cases.reviewassignment",
                    ),
                ),
            ],
            database_operations=[],
        ),

        # ------------------------------------------------------------------
        # 2. Update django_content_type rows so admin/permissions work.
        # ------------------------------------------------------------------
        migrations.RunPython(update_content_types, revert_content_types),
    ]
