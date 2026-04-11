"""
Initial migration for apps.benchmarking.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BenchmarkRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_updated", to=settings.AUTH_USER_MODEL)),
                ("title", models.CharField(max_length=255)),
                ("project_name", models.CharField(blank=True, default="", max_length=255)),
                ("geography", models.CharField(
                    choices=[("UAE", "UAE (United Arab Emirates)"), ("KSA", "KSA (Kingdom of Saudi Arabia)"), ("QATAR", "Qatar")],
                    db_index=True, default="UAE", max_length=20)),
                ("scope_type", models.CharField(
                    choices=[("SITC", "SITC (Supply, Install, Test & Commission)"), ("ITC", "ITC (Install, Test & Commission only)"), ("EQUIPMENT_ONLY", "Equipment Only")],
                    db_index=True, default="SITC", max_length=30)),
                ("store_type", models.CharField(blank=True, default="", max_length=100)),
                ("status", models.CharField(
                    choices=[("PENDING", "Pending"), ("PROCESSING", "Processing"), ("COMPLETED", "Completed"), ("FAILED", "Failed")],
                    db_index=True, default="PENDING", max_length=20)),
                ("notes", models.TextField(blank=True, default="")),
                ("error_message", models.TextField(blank=True, default="")),
                ("submitted_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="benchmark_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Benchmark Request", "verbose_name_plural": "Benchmark Requests"},
        ),
        migrations.CreateModel(
            name="BenchmarkCorridorRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_updated", to=settings.AUTH_USER_MODEL)),
                ("rule_code", models.CharField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("category", models.CharField(
                    choices=[("EQUIPMENT", "Equipment"), ("CONTROLS", "Controls"), ("DUCTING", "Ducting"),
                             ("INSULATION", "Insulation"), ("ACCESSORIES", "Accessories"),
                             ("INSTALLATION", "Installation"), ("TC", "Testing & Commissioning"),
                             ("UNCATEGORIZED", "Uncategorized")],
                    db_index=True, max_length=30)),
                ("scope_type", models.CharField(db_index=True, default="ALL", max_length=30)),
                ("geography", models.CharField(db_index=True, default="ALL", max_length=20)),
                ("uom", models.CharField(blank=True, default="", max_length=50)),
                ("min_rate", models.DecimalField(decimal_places=2, max_digits=14)),
                ("mid_rate", models.DecimalField(decimal_places=2, max_digits=14)),
                ("max_rate", models.DecimalField(decimal_places=2, max_digits=14)),
                ("currency", models.CharField(default="AED", max_length=10)),
                ("keywords", models.TextField(blank=True, default="")),
                ("notes", models.TextField(blank=True, default="")),
                ("priority", models.PositiveIntegerField(default=100)),
            ],
            options={"ordering": ["category", "geography", "priority"], "verbose_name": "Benchmark Corridor Rule", "verbose_name_plural": "Benchmark Corridor Rules"},
        ),
        migrations.CreateModel(
            name="BenchmarkQuotation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_updated", to=settings.AUTH_USER_MODEL)),
                ("request", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="quotations", to="benchmarking.benchmarkrequest")),
                ("supplier_name", models.CharField(blank=True, default="", max_length=255)),
                ("quotation_ref", models.CharField(blank=True, default="", max_length=100)),
                ("document", models.FileField(upload_to="benchmarking/quotations/%Y/%m/")),
                ("extracted_text", models.TextField(blank=True, default="")),
                ("extraction_status", models.CharField(
                    choices=[("PENDING", "Pending"), ("DONE", "Done"), ("FAILED", "Failed")],
                    default="PENDING", max_length=20)),
                ("extraction_error", models.TextField(blank=True, default="")),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Benchmark Quotation", "verbose_name_plural": "Benchmark Quotations"},
        ),
        migrations.CreateModel(
            name="BenchmarkLineItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_updated", to=settings.AUTH_USER_MODEL)),
                ("quotation", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="line_items", to="benchmarking.benchmarkquotation")),
                ("description", models.TextField()),
                ("uom", models.CharField(blank=True, default="", max_length=50)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12, null=True, blank=True)),
                ("quoted_unit_rate", models.DecimalField(decimal_places=2, max_digits=14, null=True, blank=True)),
                ("line_amount", models.DecimalField(decimal_places=2, max_digits=16, null=True, blank=True)),
                ("line_number", models.PositiveIntegerField(default=0)),
                ("extraction_confidence", models.FloatField(default=0.0)),
                ("category", models.CharField(
                    choices=[("EQUIPMENT", "Equipment"), ("CONTROLS", "Controls"), ("DUCTING", "Ducting"),
                             ("INSULATION", "Insulation"), ("ACCESSORIES", "Accessories"),
                             ("INSTALLATION", "Installation"), ("TC", "Testing & Commissioning"),
                             ("UNCATEGORIZED", "Uncategorized")],
                    db_index=True, default="UNCATEGORIZED", max_length=30)),
                ("classification_confidence", models.FloatField(default=0.0)),
                ("benchmark_min", models.DecimalField(decimal_places=2, max_digits=14, null=True, blank=True)),
                ("benchmark_mid", models.DecimalField(decimal_places=2, max_digits=14, null=True, blank=True)),
                ("benchmark_max", models.DecimalField(decimal_places=2, max_digits=14, null=True, blank=True)),
                ("corridor_rule_code", models.CharField(blank=True, default="", max_length=50)),
                ("variance_pct", models.FloatField(null=True, blank=True)),
                ("variance_status", models.CharField(
                    choices=[("WITHIN_RANGE", "Within Range (<5%)"), ("MODERATE", "Moderate (5-15%)"),
                             ("HIGH", "High (>15%)"), ("NEEDS_REVIEW", "Needs Review (no benchmark)")],
                    db_index=True, default="NEEDS_REVIEW", max_length=20)),
                ("variance_note", models.TextField(blank=True, default="")),
            ],
            options={"ordering": ["line_number"], "verbose_name": "Benchmark Line Item", "verbose_name_plural": "Benchmark Line Items"},
        ),
        migrations.CreateModel(
            name="BenchmarkResult",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(class)s_updated", to=settings.AUTH_USER_MODEL)),
                ("request", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="result", to="benchmarking.benchmarkrequest")),
                ("total_quoted", models.DecimalField(decimal_places=2, max_digits=18, null=True, blank=True)),
                ("total_benchmark_mid", models.DecimalField(decimal_places=2, max_digits=18, null=True, blank=True)),
                ("overall_deviation_pct", models.FloatField(null=True, blank=True)),
                ("overall_status", models.CharField(
                    choices=[("WITHIN_RANGE", "Within Range (<5%)"), ("MODERATE", "Moderate (5-15%)"),
                             ("HIGH", "High (>15%)"), ("NEEDS_REVIEW", "Needs Review (no benchmark)")],
                    default="NEEDS_REVIEW", max_length=20)),
                ("category_summary_json", models.JSONField(blank=True, default=dict)),
                ("negotiation_notes_json", models.JSONField(blank=True, default=list)),
                ("lines_within_range", models.PositiveIntegerField(default=0)),
                ("lines_moderate", models.PositiveIntegerField(default=0)),
                ("lines_high", models.PositiveIntegerField(default=0)),
                ("lines_needs_review", models.PositiveIntegerField(default=0)),
            ],
            options={"verbose_name": "Benchmark Result", "verbose_name_plural": "Benchmark Results"},
        ),
    ]
