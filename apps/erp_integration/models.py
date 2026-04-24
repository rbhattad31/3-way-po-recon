"""ERP Integration models — connection config, cache, resolution/submission logs."""
from django.conf import settings
from django.db import models

from apps.core.models import BaseModel, TimestampMixin
from apps.erp_integration.enums import (
    ERPAuthType,
    ERPConnectionStatus,
    ERPConnectorType,
    ERPResolutionType,
    ERPSourceType,
    ERPSubmissionStatus,
    ERPSubmissionType,
)


class ERPConnection(BaseModel):
    """Configuration record for an ERP connection.

    auth_config_json stores secret *references* (env var names), never raw secrets.
    """

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    name = models.CharField(max_length=200, db_index=True)
    connector_type = models.CharField(
        max_length=20,
        choices=ERPConnectorType.choices,
        default=ERPConnectorType.CUSTOM,
        db_index=True,
    )
    base_url = models.URLField(max_length=500, blank=True, default="")
    auth_config_json = models.JSONField(
        default=dict,
        blank=True,
        help_text="Legacy JSON auth config. Prefer the typed fields below.",
    )
    status = models.CharField(
        max_length=20,
        choices=ERPConnectionStatus.choices,
        default=ERPConnectionStatus.ACTIVE,
        db_index=True,
    )
    is_active = models.BooleanField(default=True, db_index=True)
    timeout_seconds = models.PositiveIntegerField(default=30)
    is_default = models.BooleanField(default=False)
    metadata_json = models.JSONField(default=dict, blank=True)

    # ── REST API fields (CUSTOM, DYNAMICS, ZOHO, SALESFORCE) ──
    auth_type = models.CharField(
        max_length=20,
        choices=ERPAuthType.choices,
        default=ERPAuthType.BEARER,
        blank=True,
    )
    api_key_env = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Env var name holding the API key / bearer token",
    )

    # ── SQL Server fields (SQLSERVER) ──
    connection_string_env = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Env var name holding the ODBC connection string (advanced)",
    )
    database_name = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Database name",
    )
    db_host = models.CharField(
        max_length=300, blank=True, default="",
        help_text="Database server hostname or IP",
    )
    db_port = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Database port (default 1433 for SQL Server)",
    )
    db_username = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Database login username",
    )
    db_password_encrypted = models.TextField(
        blank=True, default="",
        help_text="Fernet-encrypted database password",
    )
    db_driver = models.CharField(
        max_length=200, blank=True, default="ODBC Driver 17 for SQL Server",
        help_text="ODBC driver name",
    )
    db_trust_cert = models.BooleanField(
        default=False,
        help_text="Append TrustServerCertificate=yes and Encrypt=yes "
                  "(common for on-prem servers with self-signed certs)",
    )

    # ── OAuth fields (DYNAMICS, ZOHO, SALESFORCE) ──
    erp_tenant_id = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Tenant / Org ID for cloud ERP (e.g. Azure AD tenant ID)",
    )
    client_id_env = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Env var name holding the OAuth client ID",
    )
    client_secret_env = models.CharField(
        max_length=200, blank=True, default="",
        help_text="Env var name holding the OAuth client secret",
    )

    class Meta:
        db_table = "erp_integration_connection"
        ordering = ["-is_default", "name"]
        verbose_name = "ERP Connection"
        verbose_name_plural = "ERP Connections"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "tenant"],
                name="uq_erp_connection_name_tenant",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.connector_type}] ({self.status})"


class ERPReferenceCacheRecord(TimestampMixin):
    """Optional TTL-based cache for ERP reference lookups."""

    cache_key = models.CharField(max_length=255, unique=True, db_index=True)
    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    resolution_type = models.CharField(
        max_length=30,
        choices=ERPResolutionType.choices,
    )
    connector_name = models.CharField(max_length=200, blank=True, default="")
    value_json = models.JSONField(default=dict)
    expires_at = models.DateTimeField(db_index=True)
    source_type = models.CharField(
        max_length=20,
        choices=ERPSourceType.choices,
        default=ERPSourceType.API,
    )

    class Meta:
        db_table = "erp_integration_cache"
        ordering = ["-created_at"]
        verbose_name = "ERP Reference Cache Record"
        verbose_name_plural = "ERP Reference Cache Records"

    def __str__(self) -> str:
        return f"Cache [{self.resolution_type}] {self.cache_key[:60]}"


class ERPResolutionLog(BaseModel):
    """Audit log for every ERP lookup resolution attempt."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    resolution_type = models.CharField(
        max_length=30,
        choices=ERPResolutionType.choices,
        db_index=True,
    )
    lookup_key = models.CharField(max_length=500, db_index=True)
    source_type = models.CharField(
        max_length=20,
        choices=ERPSourceType.choices,
    )
    resolved = models.BooleanField(default=False)
    fallback_used = models.BooleanField(default=False)
    confidence = models.FloatField(null=True, blank=True)
    connector_name = models.CharField(max_length=200, blank=True, default="")
    reason = models.TextField(blank=True, default="")
    value_json = models.JSONField(default=dict, blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)
    freshness_timestamp = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    # Cross-references
    related_invoice = models.ForeignKey(
        "documents.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="erp_resolution_logs",
    )
    related_reconciliation_result = models.ForeignKey(
        "reconciliation.ReconciliationResult",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="erp_resolution_logs",
    )
    related_posting_run = models.ForeignKey(
        "posting_core.PostingRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="erp_resolution_logs",
    )

    class Meta:
        db_table = "erp_integration_resolution_log"
        ordering = ["-created_at"]
        verbose_name = "ERP Resolution Log"
        verbose_name_plural = "ERP Resolution Logs"
        indexes = [
            models.Index(
                fields=["resolution_type", "created_at"],
                name="idx_erp_reslog_type_date",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"ResLog [{self.resolution_type}] {self.lookup_key[:40]} "
            f"→ {self.source_type} (resolved={self.resolved})"
        )


class ERPSubmissionLog(BaseModel):
    """Audit log for every ERP submission attempt (create/park invoice)."""

    tenant = models.ForeignKey(
        "accounts.CompanyProfile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        db_index=True,
        related_name="+",
    )
    submission_type = models.CharField(
        max_length=30,
        choices=ERPSubmissionType.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=ERPSubmissionStatus.choices,
    )
    connector_name = models.CharField(max_length=200, blank=True, default="")
    request_payload_json = models.JSONField(default=dict, blank=True)
    response_json = models.JSONField(default=dict, blank=True)
    erp_document_number = models.CharField(max_length=200, blank=True, default="")
    error_code = models.CharField(max_length=100, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    # Cross-references
    related_invoice = models.ForeignKey(
        "documents.Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="erp_submission_logs",
    )
    related_posting_run = models.ForeignKey(
        "posting_core.PostingRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="erp_submission_logs",
    )

    class Meta:
        db_table = "erp_integration_submission_log"
        ordering = ["-created_at"]
        verbose_name = "ERP Submission Log"
        verbose_name_plural = "ERP Submission Logs"
        indexes = [
            models.Index(
                fields=["submission_type", "created_at"],
                name="idx_erp_sublog_type_date",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"SubLog [{self.submission_type}] {self.status} "
            f"doc={self.erp_document_number or 'N/A'}"
        )
