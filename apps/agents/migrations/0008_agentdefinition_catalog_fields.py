from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('agents', '0007_add_recommendation_unique_constraint'),
    ]

    operations = [
        # Catalog / contract fields
        migrations.AddField(
            model_name='agentdefinition',
            name='purpose',
            field=models.TextField(blank=True, default='', help_text='What this agent does and why it exists'),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='entry_conditions',
            field=models.TextField(blank=True, default='', help_text='When this agent should be invoked'),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='success_criteria',
            field=models.TextField(blank=True, default='', help_text='What a successful run looks like'),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='prohibited_actions',
            field=models.JSONField(blank=True, null=True, help_text="List of actions this agent must never take, e.g. ['AUTO_CLOSE']"),
        ),
        # Tool grounding
        migrations.AddField(
            model_name='agentdefinition',
            name='requires_tool_grounding',
            field=models.BooleanField(default=False, help_text='If True, at least one tool call must succeed before a recommendation is made'),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='min_tool_calls',
            field=models.PositiveIntegerField(default=0, help_text='Minimum number of successful tool calls required'),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='tool_failure_confidence_cap',
            field=models.FloatField(blank=True, null=True, help_text='Maximum confidence allowed when any tool fails. Overrides the platform default of 0.5'),
        ),
        # Recommendation contract
        migrations.AddField(
            model_name='agentdefinition',
            name='allowed_recommendation_types',
            field=models.JSONField(blank=True, null=True, help_text='List of RecommendationType values this agent is allowed to emit. Null = all allowed'),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='default_fallback_recommendation',
            field=models.CharField(blank=True, default='', max_length=60, help_text='Recommendation to use when output is invalid or suppressed. Must be a valid RecommendationType value'),
        ),
        # Output schema
        migrations.AddField(
            model_name='agentdefinition',
            name='output_schema_name',
            field=models.CharField(blank=True, default='', max_length=100, help_text='Name of the output schema this agent targets, e.g. AgentOutputSchema'),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='output_schema_version',
            field=models.CharField(blank=True, default='', max_length=20, help_text='Version of the output schema, e.g. v1'),
        ),
        # Lifecycle and governance
        migrations.AddField(
            model_name='agentdefinition',
            name='lifecycle_status',
            field=models.CharField(
                choices=[('draft', 'Draft'), ('active', 'Active'), ('deprecated', 'Deprecated')],
                default='active',
                db_index=True,
                max_length=20,
                help_text='Operational lifecycle of this agent definition',
            ),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='owner_team',
            field=models.CharField(blank=True, default='', max_length=100, help_text='Team responsible for this agent, e.g. AP Automation'),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='capability_tags',
            field=models.JSONField(blank=True, null=True, help_text="Primary and secondary capabilities, e.g. ['retrieval', 'routing']"),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='domain_tags',
            field=models.JSONField(blank=True, null=True, help_text="Business domain tags, e.g. ['po', 'grn', 'vendor']"),
        ),
        migrations.AddField(
            model_name='agentdefinition',
            name='human_review_required_conditions',
            field=models.TextField(blank=True, default='', help_text='Conditions under which a human reviewer must be assigned'),
        ),
    ]
