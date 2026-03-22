"""Service for managing EntityExtractionProfile."""
from __future__ import annotations

from typing import Any

from django.db.models import QuerySet

from apps.extraction_core.models import EntityExtractionProfile, ExtractionRuntimeSettings
from apps.extraction_core.services.extraction_audit import ExtractionAuditService


class EntityProfileService:
    """Stateless service for entity extraction profile management."""

    @classmethod
    def list_profiles(cls, filters: dict | None = None) -> QuerySet:
        qs = EntityExtractionProfile.objects.select_related("entity").all()
        if not filters:
            return qs
        if filters.get("country_code"):
            qs = qs.filter(default_country_code__iexact=filters["country_code"])
        if filters.get("regime_code"):
            qs = qs.filter(default_regime_code__iexact=filters["regime_code"])
        if filters.get("jurisdiction_mode"):
            qs = qs.filter(jurisdiction_mode__iexact=filters["jurisdiction_mode"])
        if filters.get("is_active") is not None:
            qs = qs.filter(is_active=filters["is_active"])
        if filters.get("search"):
            qs = qs.filter(entity__name__icontains=filters["search"])
        return qs

    @classmethod
    def get_profile(cls, pk: int) -> EntityExtractionProfile | None:
        return EntityExtractionProfile.objects.select_related("entity").filter(pk=pk).first()

    @classmethod
    def update_profile(cls, profile: EntityExtractionProfile, data: dict, user) -> EntityExtractionProfile:
        editable = [
            "default_country_code", "default_regime_code", "default_document_language",
            "jurisdiction_mode", "schema_override_code",
            "validation_profile_override_code", "normalization_profile_override_code",
            "is_active",
        ]
        before = {}
        after = {}
        for field in editable:
            if field in data:
                old_val = getattr(profile, field)
                new_val = data[field]
                if str(old_val) != str(new_val):
                    before[field] = str(old_val)
                    after[field] = str(new_val)
                setattr(profile, field, new_val)

        profile.updated_by = user
        profile.save()

        if before:
            ExtractionAuditService.log_settings_updated(
                entity_type="EntityExtractionProfile",
                entity_id=profile.pk,
                before=before,
                after=after,
                user=user,
            )
        return profile


class SettingsResolutionService:
    """Resolves effective settings for a given entity, merging profile overrides with system defaults."""

    @classmethod
    def get_effective_settings(cls, profile: EntityExtractionProfile) -> dict:
        """Return dict showing system defaults vs entity overrides."""
        system = ExtractionRuntimeSettings.get_active()
        result = {}
        compare_fields = [
            ("default_country_code", "Default Country Code"),
            ("default_regime_code", "Default Regime Code"),
            ("jurisdiction_mode", "Jurisdiction Mode"),
        ]
        for field, label in compare_fields:
            system_val = getattr(system, field, "") if system else ""
            entity_val = getattr(profile, field, "")
            result[field] = {
                "label": label,
                "system_value": system_val,
                "entity_value": entity_val,
                "is_overridden": bool(entity_val) and entity_val != system_val,
                "effective_value": entity_val if entity_val else system_val,
            }
        # Schema/validation/normalization overrides
        for field, label in [
            ("schema_override_code", "Schema Override"),
            ("validation_profile_override_code", "Validation Profile Override"),
            ("normalization_profile_override_code", "Normalization Profile Override"),
        ]:
            entity_val = getattr(profile, field, "")
            result[field] = {
                "label": label,
                "system_value": "(system default)",
                "entity_value": entity_val or "(none)",
                "is_overridden": bool(entity_val),
                "effective_value": entity_val or "(system default)",
            }
        return result
