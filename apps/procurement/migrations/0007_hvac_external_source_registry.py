# Rewritten to be an ALTER migration on top of 0007_flow_a_models.
# The original version tried to CREATE the table, but that table was already
# created by 0007_flow_a_models. This migration now aligns the schema with
# the current models.py definition instead.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('procurement', '0007_flow_a_models'),
        ('procurement', '0006_merge_20260407_1404'),
    ]

    operations = [
        # Add the missing is_active column (the immediate cause of the error)
        migrations.AddField(
            model_name='externalsourceregistry',
            name='is_active',
            field=models.BooleanField(default=True, db_index=True),
        ),
        # Align source_name: max_length 300 -> 200, remove db_index
        migrations.AlterField(
            model_name='externalsourceregistry',
            name='source_name',
            field=models.CharField(
                max_length=200,
                help_text="Display name e.g. 'Daikin MEA Official'",
            ),
        ),
        # Align domain: max_length 100 -> 300, remove db_index, remove default
        migrations.AlterField(
            model_name='externalsourceregistry',
            name='domain',
            field=models.CharField(
                max_length=300,
                help_text='Root domain e.g. daikinmea.com',
            ),
        ),
        # Remove base_url (not in the current model)
        migrations.RemoveField(
            model_name='externalsourceregistry',
            name='base_url',
        ),
        # Remove system_family_scope (not in the current model)
        migrations.RemoveField(
            model_name='externalsourceregistry',
            name='system_family_scope',
        ),
        # Remove oem_brand (not in the current model)
        migrations.RemoveField(
            model_name='externalsourceregistry',
            name='oem_brand',
        ),
        # Align source_type choices
        migrations.AlterField(
            model_name='externalsourceregistry',
            name='source_type',
            field=models.CharField(
                max_length=40,
                choices=[
                    ('OEM_OFFICIAL', 'OEM Official'),
                    ('OEM_REGIONAL', 'OEM Regional'),
                    ('AUTHORIZED_DISTRIBUTOR', 'Authorized Distributor'),
                    ('TECHNICAL_DATASHEET', 'Technical Datasheet'),
                    ('STANDARD_REGULATORY', 'Standard / Regulatory'),
                    ('LANDLORD_GUIDE', 'Landlord Guide'),
                    ('INTERNAL_HISTORICAL', 'Internal Historical'),
                ],
                default='OEM_OFFICIAL',
                db_index=True,
            ),
        ),
        # Align country_scope: TextField -> JSONField
        migrations.AlterField(
            model_name='externalsourceregistry',
            name='country_scope',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of country codes this source covers e.g. ['UAE','KSA']",
            ),
        ),
        # Align fetch_mode: max_length 20 -> 10, choices WEB_PAGE->PAGE
        migrations.AlterField(
            model_name='externalsourceregistry',
            name='fetch_mode',
            field=models.CharField(
                max_length=10,
                choices=[
                    ('PAGE', 'Web Page'),
                    ('PDF', 'PDF Download'),
                    ('API', 'API Endpoint'),
                ],
                default='PAGE',
            ),
        ),
        # Align priority default: 50 -> 10
        migrations.AlterField(
            model_name='externalsourceregistry',
            name='priority',
            field=models.PositiveIntegerField(
                default=10,
                help_text='Lower number = higher priority',
            ),
        ),
        # Remove old indexes from 0007_flow_a_models (they reference removed fields)
        migrations.RemoveIndex(
            model_name='externalsourceregistry',
            name='procurement_domain_64f374_idx',
        ),
        migrations.RemoveIndex(
            model_name='externalsourceregistry',
            name='procurement_allowed_7f80e7_idx',
        ),
        # Update verbose_name on the model meta
        migrations.AlterModelOptions(
            name='externalsourceregistry',
            options={
                'verbose_name': 'External Source Registry',
                'verbose_name_plural': 'External Source Registry',
                'db_table': 'procurement_external_source_registry',
                'ordering': ['priority', 'source_name'],
            },
        ),
    ]
