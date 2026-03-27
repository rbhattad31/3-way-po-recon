"""
CountryPackService — Governance for multi-country extraction support.

Manages:
- Schema versioning per jurisdiction
- Validation profile versioning
- Normalization profile versioning
- Activation flags

Supported country packs:
- India GST
- UAE VAT
- Saudi VAT
"""
from __future__ import annotations

import logging
from typing import Optional

from django.utils import timezone

from apps.core.enums import CountryPackStatus
from apps.extraction_core.models import CountryPack, TaxJurisdictionProfile

logger = logging.getLogger(__name__)


class CountryPackService:
    """
    Manages country pack lifecycle (draft → active → deprecated).
    """

    @classmethod
    def get_active_packs(cls) -> list[CountryPack]:
        """Return all active country packs."""
        return list(
            CountryPack.objects
            .filter(pack_status=CountryPackStatus.ACTIVE)
            .select_related("jurisdiction")
            .order_by("jurisdiction__country_code")
        )

    @classmethod
    def get_pack_for_country(
        cls,
        country_code: str,
        regime_code: str = "",
    ) -> CountryPack | None:
        """Look up country pack by country + optional regime."""
        qs = CountryPack.objects.filter(
            jurisdiction__country_code=country_code.upper(),
            pack_status=CountryPackStatus.ACTIVE,
        )
        if regime_code:
            qs = qs.filter(jurisdiction__tax_regime=regime_code)
        return qs.select_related("jurisdiction").first()

    @classmethod
    def activate_pack(
        cls,
        pack: CountryPack,
        user=None,
    ) -> CountryPack:
        """Activate a country pack."""
        pack.pack_status = CountryPackStatus.ACTIVE
        pack.activated_at = timezone.now()
        pack.deactivated_at = None
        if user:
            pack.updated_by = user
        pack.save(update_fields=[
            "pack_status", "activated_at", "deactivated_at",
            "updated_by", "updated_at",
        ])
        logger.info(
            "Country pack activated: %s", pack.jurisdiction,
        )
        return pack

    @classmethod
    def deprecate_pack(
        cls,
        pack: CountryPack,
        user=None,
    ) -> CountryPack:
        """Deprecate a country pack."""
        pack.pack_status = CountryPackStatus.DEPRECATED
        pack.deactivated_at = timezone.now()
        if user:
            pack.updated_by = user
        pack.save(update_fields=[
            "pack_status", "deactivated_at", "updated_by", "updated_at",
        ])
        logger.info(
            "Country pack deprecated: %s", pack.jurisdiction,
        )
        return pack

    @classmethod
    def update_version(
        cls,
        pack: CountryPack,
        *,
        schema_version: str = "",
        validation_version: str = "",
        normalization_version: str = "",
        user=None,
    ) -> CountryPack:
        """Update version numbers for a country pack."""
        update_fields = ["updated_at"]
        if schema_version:
            pack.schema_version = schema_version
            update_fields.append("schema_version")
        if validation_version:
            pack.validation_profile_version = validation_version
            update_fields.append("validation_profile_version")
        if normalization_version:
            pack.normalization_profile_version = normalization_version
            update_fields.append("normalization_profile_version")
        if user:
            pack.updated_by = user
            update_fields.append("updated_by")
        pack.save(update_fields=update_fields)
        return pack

    @classmethod
    def is_country_supported(
        cls,
        country_code: str,
        regime_code: str = "",
    ) -> bool:
        """Check if a country pack is active."""
        pack = cls.get_pack_for_country(country_code, regime_code)
        return pack is not None

    @classmethod
    def get_pack_summary(cls) -> list[dict]:
        """Return summary of all country packs for governance UI."""
        packs = (
            CountryPack.objects
            .select_related("jurisdiction")
            .order_by("jurisdiction__country_code")
        )
        return [
            {
                "id": p.pk,
                "country_code": p.jurisdiction.country_code,
                "country_name": p.jurisdiction.country_name,
                "regime": p.jurisdiction.tax_regime,
                "status": p.pack_status,
                "schema_version": p.schema_version,
                "validation_version": p.validation_profile_version,
                "normalization_version": p.normalization_profile_version,
                "activated_at": p.activated_at.isoformat() if p.activated_at else None,
            }
            for p in packs
        ]
