"""ERP Integration forms -- ERPConnection create / edit."""
from __future__ import annotations

from django import forms

from apps.erp_integration.models import ERPConnection

_FC = "form-control"
_FC_MONO = "form-control font-monospace"
_FS = "form-select"

# Common ODBC driver choices for SQL Server.
DRIVER_CHOICES = [
    ("ODBC Driver 17 for SQL Server", "ODBC Driver 17 for SQL Server"),
    ("ODBC Driver 18 for SQL Server", "ODBC Driver 18 for SQL Server"),
    ("SQL Server", "SQL Server (legacy)"),
]


class ERPConnectionForm(forms.ModelForm):
    """Create or edit an ERP Connection.

    Fields are grouped by connector type.  The template uses JS to
    show/hide the relevant sections based on the selected connector_type.
    """

    # Extra non-model field: plaintext password (never persisted as-is).
    db_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": _FC,
            "placeholder": "Database password",
            "autocomplete": "new-password",
        }),
        help_text="Password is encrypted before storage. Leave blank to keep existing.",
    )

    class Meta:
        model = ERPConnection
        fields = [
            # -- Common --
            "name",
            "connector_type",
            "status",
            "timeout_seconds",
            "is_default",
            # -- REST API (CUSTOM, DYNAMICS, ZOHO, SALESFORCE) --
            "base_url",
            "auth_type",
            "api_key_env",
            # -- SQL Server (env var mode) --
            "connection_string_env",
            # -- SQL Server (builder mode) --
            "db_host",
            "db_port",
            "database_name",
            "db_username",
            "db_driver",
            "db_trust_cert",
            # -- OAuth (DYNAMICS, ZOHO, SALESFORCE) --
            "tenant_id",
            "client_id_env",
            "client_secret_env",
            # -- Advanced --
            "metadata_json",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": _FC, "placeholder": "e.g. dynamics-prod"}),
            "connector_type": forms.Select(attrs={"class": _FS, "id": "id_connector_type"}),
            "status": forms.Select(attrs={"class": _FS}),
            "timeout_seconds": forms.NumberInput(attrs={"class": _FC, "min": 1, "max": 300}),
            "is_default": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            # REST
            "base_url": forms.URLInput(attrs={"class": _FC, "placeholder": "https://your-erp.com/api/v1"}),
            "auth_type": forms.Select(attrs={"class": _FS}),
            "api_key_env": forms.TextInput(attrs={"class": _FC, "placeholder": "ERP_API_KEY"}),
            # SQL Server (env var mode)
            "connection_string_env": forms.TextInput(attrs={
                "class": _FC, "placeholder": "ERP_SQL_CONNECTION_STRING",
            }),
            # SQL Server (builder mode)
            "db_host": forms.TextInput(attrs={"class": _FC, "placeholder": "erp-db.company.com"}),
            "db_port": forms.NumberInput(attrs={"class": _FC, "placeholder": "1433", "min": 1, "max": 65535}),
            "database_name": forms.TextInput(attrs={"class": _FC, "placeholder": "ERP_PROD"}),
            "db_username": forms.TextInput(attrs={"class": _FC, "placeholder": "sa"}),
            "db_driver": forms.Select(
                choices=DRIVER_CHOICES,
                attrs={"class": _FS},
            ),
            "db_trust_cert": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            # OAuth
            "tenant_id": forms.TextInput(attrs={"class": _FC, "placeholder": "your-tenant-id"}),
            "client_id_env": forms.TextInput(attrs={"class": _FC, "placeholder": "ERP_CLIENT_ID"}),
            "client_secret_env": forms.TextInput(attrs={"class": _FC, "placeholder": "ERP_CLIENT_SECRET"}),
            # Advanced
            "metadata_json": forms.Textarea(attrs={
                "class": _FC_MONO,
                "rows": 6,
                "placeholder": "{}",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # All type-specific fields are optional at the form level.
        for field_name in [
            "base_url", "auth_type", "api_key_env",
            "connection_string_env", "database_name",
            "db_host", "db_port", "db_username", "db_driver", "db_trust_cert",
            "tenant_id", "client_id_env", "client_secret_env",
        ]:
            self.fields[field_name].required = False

        # Show password hint on edit (has password? / no password).
        if self.instance and self.instance.pk and self.instance.db_password_encrypted:
            self.fields["db_password"].help_text = (
                "Password is stored (encrypted). Leave blank to keep current, "
                "or enter a new value to change it."
            )

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Connection name is required.")
        qs = ERPConnection.objects.filter(name=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A connection with this name already exists.")
        return name

    def clean_metadata_json(self):
        value = self.cleaned_data.get("metadata_json")
        if value is None:
            return {}
        if isinstance(value, str):
            import json
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                raise forms.ValidationError("Invalid JSON. Enter a valid JSON object.")
        if not isinstance(value, dict):
            raise forms.ValidationError("Metadata must be a JSON object (dict).")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)

        # Encrypt password if provided; keep existing if blank.
        raw_password = self.cleaned_data.get("db_password", "")
        if raw_password:
            from apps.erp_integration.crypto import encrypt_value
            instance.db_password_encrypted = encrypt_value(raw_password)

        # Enforce single-default: unset the previous default when this one is checked.
        if instance.is_default:
            qs = ERPConnection.objects.filter(is_default=True)
            if instance.pk:
                qs = qs.exclude(pk=instance.pk)
            qs.update(is_default=False)
        if commit:
            instance.save()
        return instance
