"""Service for managing ExtractionRuntimeSettings."""
from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.extraction_core.models import ExtractionRuntimeSettings
from apps.extraction_core.services.extraction_audit import ExtractionAuditService


class RuntimeSettingsService:
    """Stateless service for runtime settings CRUD and validation."""

    EDITABLE_FIELDS = [
        # Jurisdiction resolution
        "jurisdiction_mode",
        "default_country_code",
        "default_regime_code",
        "enable_jurisdiction_detection",
        "confidence_threshold_for_detection",
        "fallback_to_detection_on_schema_miss",
        "allow_manual_override",
        # Extraction runtime
        "ocr_enabled",
        "llm_extraction_enabled",
        "retry_count",
        "timeout_seconds",
        "max_pages",
        "multi_document_split_enabled",
        # Review
        "auto_approval_enabled",
        "auto_approval_threshold",
        "review_confidence_threshold",
        # Enrichment
        "vendor_matching_enabled",
        "vendor_fuzzy_threshold",
        "po_lookup_enabled",
        "contract_lookup_enabled",
        # Learning
        "correction_tracking_enabled",
        "analytics_enabled",
    ]

    @classmethod
    def get_active_settings(cls) -> ExtractionRuntimeSettings | None:
        return ExtractionRuntimeSettings.get_active()

    @classmethod
    def get_settings_by_id(cls, pk: int) -> ExtractionRuntimeSettings | None:
        return ExtractionRuntimeSettings.objects.filter(pk=pk).first()

    @classmethod
    def validate_settings(cls, data: dict) -> list[str]:
        """Return list of validation error messages."""
        errors = []
        mode = data.get("jurisdiction_mode", "")
        if mode in ("FIXED", "HYBRID"):
            if not data.get("default_country_code"):
                errors.append("default_country_code is required for FIXED/HYBRID mode.")
        threshold = data.get("confidence_threshold_for_detection")
        if threshold is not None and not (0.0 <= float(threshold) <= 1.0):
            errors.append("confidence_threshold_for_detection must be between 0.0 and 1.0.")
        auto_thresh = data.get("auto_approval_threshold")
        if auto_thresh is not None and not (0.0 <= float(auto_thresh) <= 1.0):
            errors.append("auto_approval_threshold must be between 0.0 and 1.0.")
        review_thresh = data.get("review_confidence_threshold")
        if review_thresh is not None and not (0.0 <= float(review_thresh) <= 1.0):
            errors.append("review_confidence_threshold must be between 0.0 and 1.0.")
        vendor_thresh = data.get("vendor_fuzzy_threshold")
        if vendor_thresh is not None and not (0.0 <= float(vendor_thresh) <= 1.0):
            errors.append("vendor_fuzzy_threshold must be between 0.0 and 1.0.")
        retry = data.get("retry_count")
        if retry is not None and int(retry) < 0:
            errors.append("retry_count must be non-negative.")
        timeout = data.get("timeout_seconds")
        if timeout is not None and int(timeout) < 1:
            errors.append("timeout_seconds must be at least 1.")
        max_pages = data.get("max_pages")
        if max_pages is not None and int(max_pages) < 1:
            errors.append("max_pages must be at least 1.")
        return errors

    @classmethod
    def update_settings(cls, settings_obj: ExtractionRuntimeSettings, data: dict, user) -> ExtractionRuntimeSettings:
        """Update settings and log audit event."""
        before = {}
        after = {}
        for field in cls.EDITABLE_FIELDS:
            if field in data:
                old_val = getattr(settings_obj, field)
                new_val = data[field]
                if str(old_val) != str(new_val):
                    before[field] = str(old_val)
                    after[field] = str(new_val)
                setattr(settings_obj, field, new_val)

        settings_obj.updated_by = user
        settings_obj.save()

        if before:
            ExtractionAuditService.log_settings_updated(
                entity_type="ExtractionRuntimeSettings",
                entity_id=settings_obj.pk,
                before=before,
                after=after,
                user=user,
            )

        return settings_obj

    @classmethod
    def get_settings_sections(cls, settings_obj: ExtractionRuntimeSettings) -> dict:
        """Group settings into UI sections."""
        return {
            "jurisdiction_resolution": {
                "label": "Jurisdiction Resolution",
                "icon": "bi-globe2",
                "fields": {
                    "jurisdiction_mode": {"label": "Mode", "type": "select", "choices": ["AUTO", "FIXED", "HYBRID"]},
                    "default_country_code": {"label": "Default Country Code", "type": "text"},
                    "default_regime_code": {"label": "Default Regime Code", "type": "text"},
                    "enable_jurisdiction_detection": {"label": "Enable Detection", "type": "bool"},
                    "confidence_threshold_for_detection": {"label": "Detection Confidence Threshold", "type": "float"},
                    "fallback_to_detection_on_schema_miss": {"label": "Fallback on Schema Miss", "type": "bool"},
                    "allow_manual_override": {"label": "Allow Manual Override", "type": "bool"},
                },
            },
            "extraction_runtime": {
                "label": "Extraction Runtime",
                "icon": "bi-gear",
                "fields": {
                    "ocr_enabled": {"label": "OCR Enabled", "type": "bool"},
                    "llm_extraction_enabled": {"label": "LLM Extraction Enabled", "type": "bool"},
                    "retry_count": {"label": "Retry Count", "type": "int"},
                    "timeout_seconds": {"label": "Timeout (seconds)", "type": "int"},
                    "max_pages": {"label": "Max Pages", "type": "int"},
                    "multi_document_split_enabled": {"label": "Multi-Document Split", "type": "bool"},
                },
            },
            "review_settings": {
                "label": "Review Settings",
                "icon": "bi-clipboard-check",
                "fields": {
                    "auto_approval_enabled": {"label": "Auto-Approval Enabled", "type": "bool"},
                    "auto_approval_threshold": {"label": "Auto-Approval Threshold", "type": "float"},
                    "review_confidence_threshold": {"label": "Review Confidence Threshold", "type": "float"},
                },
            },
            "enrichment_settings": {
                "label": "Enrichment Settings",
                "icon": "bi-puzzle",
                "fields": {
                    "vendor_matching_enabled": {"label": "Vendor Matching", "type": "bool"},
                    "vendor_fuzzy_threshold": {"label": "Vendor Fuzzy Threshold", "type": "float"},
                    "po_lookup_enabled": {"label": "PO Lookup", "type": "bool"},
                    "contract_lookup_enabled": {"label": "Contract Lookup", "type": "bool"},
                },
            },
            "learning_analytics": {
                "label": "Learning & Analytics",
                "icon": "bi-graph-up",
                "fields": {
                    "correction_tracking_enabled": {"label": "Correction Tracking", "type": "bool"},
                    "analytics_enabled": {"label": "Analytics Enabled", "type": "bool"},
                },
            },
        }
