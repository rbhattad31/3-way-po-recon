"""Integration contracts and placeholder models."""
from django.db import models

from apps.core.models import BaseModel


class IntegrationConfig(BaseModel):
    """Configuration for an external integration endpoint."""

    name = models.CharField(max_length=100, unique=True)
    integration_type = models.CharField(
        max_length=30,
        choices=[
            ("PO_API", "PO Ingestion API"),
            ("GRN_API", "GRN Ingestion API"),
            ("PO_RPA", "PO Ingestion RPA"),
            ("GRN_RPA", "GRN Ingestion RPA"),
        ],
        db_index=True,
    )
    endpoint_url = models.URLField(blank=True, default="")
    auth_method = models.CharField(max_length=30, blank=True, default="none")
    enabled = models.BooleanField(default=False)
    config_json = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "integrations_config"
        ordering = ["name"]
        verbose_name = "Integration Config"
        verbose_name_plural = "Integration Configs"

    def __str__(self) -> str:
        return f"{self.name} ({self.integration_type})"


class IntegrationLog(BaseModel):
    """Audit log for integration calls."""

    integration = models.ForeignKey(IntegrationConfig, on_delete=models.CASCADE, related_name="logs")
    direction = models.CharField(max_length=10, choices=[("INBOUND", "Inbound"), ("OUTBOUND", "Outbound")])
    status = models.CharField(max_length=20, default="SUCCESS")
    request_payload = models.JSONField(null=True, blank=True)
    response_payload = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "integrations_log"
        ordering = ["-created_at"]
        verbose_name = "Integration Log"
        verbose_name_plural = "Integration Logs"

    def __str__(self) -> str:
        return f"IntLog #{self.pk} – {self.integration.name} – {self.status}"
