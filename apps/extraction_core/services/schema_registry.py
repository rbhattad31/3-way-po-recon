"""
SchemaRegistryService — Cached, version-aware schema lookup.

Provides the single entry point for resolving which ExtractionSchemaDefinition
applies to a given jurisdiction + document type combination.  Supports:

    • Latest-version lookup (default)
    • Specific-version lookup
    • Version listing / comparison
    • Django cache-backed memoisation (configurable TTL)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from django.core.cache import cache

from apps.extraction_core.models import ExtractionSchemaDefinition, TaxJurisdictionProfile

logger = logging.getLogger(__name__)

# Cache key prefix and TTL (seconds) — override via Django settings if needed
_CACHE_PREFIX = "schema_registry"
_CACHE_TTL = 300  # 5 minutes


@dataclass
class SchemaLookupResult:
    """Wraps a schema lookup with metadata about how it was resolved."""

    schema: ExtractionSchemaDefinition | None = None
    resolved: bool = False
    from_cache: bool = False
    available_versions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "resolved": self.resolved,
            "from_cache": self.from_cache,
            "schema_id": self.schema.pk if self.schema else None,
            "schema_name": str(self.schema) if self.schema else None,
            "schema_version": self.schema.schema_version if self.schema else None,
            "available_versions": self.available_versions,
        }


class SchemaRegistryService:
    """Cached, version-aware extraction schema registry."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def get_schema(
        cls,
        *,
        country_code: str,
        document_type: str,
        version: str | None = None,
        tax_regime: str | None = None,
    ) -> SchemaLookupResult:
        """
        Resolve the extraction schema for a country + document type.

        Args:
            country_code: ISO country code (e.g. IN, AE, SA).
            document_type: Document type string (e.g. INVOICE).
            version: Optional specific schema version. When ``None``
                     the latest active version is returned.
            tax_regime: Optional tax regime filter. Useful when a country
                        has multiple regimes (future-proof).

        Returns:
            SchemaLookupResult containing the matched schema or ``None``.
        """
        cache_key = cls._cache_key(country_code, document_type, version, tax_regime)

        # Try cache first
        cached = cache.get(cache_key)
        if cached is not None:
            return SchemaLookupResult(
                schema=cached["schema"],
                resolved=cached["schema"] is not None,
                from_cache=True,
                available_versions=cached.get("versions", []),
            )

        # Resolve jurisdiction
        jurisdiction = cls._resolve_jurisdiction(country_code, tax_regime)
        if not jurisdiction:
            logger.info(
                "Schema lookup failed: no jurisdiction for country=%s regime=%s",
                country_code,
                tax_regime,
            )
            return SchemaLookupResult()

        # Fetch schema
        schema = cls._fetch_schema(jurisdiction, document_type, version)
        versions = cls._list_versions(jurisdiction, document_type)

        # Populate cache
        cache.set(
            cache_key,
            {"schema": schema, "versions": versions},
            _CACHE_TTL,
        )

        return SchemaLookupResult(
            schema=schema,
            resolved=schema is not None,
            from_cache=False,
            available_versions=versions,
        )

    @classmethod
    def get_schema_by_jurisdiction(
        cls,
        jurisdiction: TaxJurisdictionProfile,
        document_type: str,
        version: str | None = None,
    ) -> SchemaLookupResult:
        """
        Shortcut when the caller already has a resolved jurisdiction object.
        """
        cache_key = cls._cache_key(
            jurisdiction.country_code, document_type, version, jurisdiction.tax_regime
        )

        cached = cache.get(cache_key)
        if cached is not None:
            return SchemaLookupResult(
                schema=cached["schema"],
                resolved=cached["schema"] is not None,
                from_cache=True,
                available_versions=cached.get("versions", []),
            )

        schema = cls._fetch_schema(jurisdiction, document_type, version)
        versions = cls._list_versions(jurisdiction, document_type)

        cache.set(
            cache_key,
            {"schema": schema, "versions": versions},
            _CACHE_TTL,
        )

        return SchemaLookupResult(
            schema=schema,
            resolved=schema is not None,
            from_cache=False,
            available_versions=versions,
        )

    @classmethod
    def list_versions(
        cls,
        *,
        country_code: str,
        document_type: str,
        tax_regime: str | None = None,
    ) -> list[str]:
        """Return all active schema versions for a country + document type."""
        jurisdiction = cls._resolve_jurisdiction(country_code, tax_regime)
        if not jurisdiction:
            return []
        return cls._list_versions(jurisdiction, document_type)

    @classmethod
    def get_all_schemas(
        cls,
        *,
        active_only: bool = True,
    ) -> list[ExtractionSchemaDefinition]:
        """Return all schemas, optionally filtered to active only."""
        cache_key = f"{_CACHE_PREFIX}:all:active={active_only}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        qs = ExtractionSchemaDefinition.objects.select_related("jurisdiction")
        if active_only:
            qs = qs.filter(is_active=True, jurisdiction__is_active=True)
        result = list(qs)
        cache.set(cache_key, result, _CACHE_TTL)
        return result

    @classmethod
    def invalidate_cache(
        cls,
        country_code: str | None = None,
        document_type: str | None = None,
    ) -> None:
        """
        Invalidate cached schema entries.

        Call this after schema CRUD operations to ensure freshness.
        When called without arguments, deletes the "all schemas" cache key.
        """
        if country_code and document_type:
            # Delete all version variants for this combo
            for version_suffix in [None, ""]:
                key = cls._cache_key(country_code, document_type, version_suffix, None)
                cache.delete(key)
        # Always clear the "all" key
        cache.delete(f"{_CACHE_PREFIX}:all:active=True")
        cache.delete(f"{_CACHE_PREFIX}:all:active=False")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_jurisdiction(
        cls,
        country_code: str,
        tax_regime: str | None,
    ) -> TaxJurisdictionProfile | None:
        qs = TaxJurisdictionProfile.objects.filter(
            country_code__iexact=country_code,
            is_active=True,
        )
        if tax_regime:
            qs = qs.filter(tax_regime__iexact=tax_regime)
        return qs.first()

    @classmethod
    def _fetch_schema(
        cls,
        jurisdiction: TaxJurisdictionProfile,
        document_type: str,
        version: str | None,
    ) -> ExtractionSchemaDefinition | None:
        qs = ExtractionSchemaDefinition.objects.filter(
            jurisdiction=jurisdiction,
            document_type__iexact=document_type,
            is_active=True,
        )
        if version:
            qs = qs.filter(schema_version=version)
        # Order by version descending so .first() gives the latest
        return qs.order_by("-schema_version").first()

    @classmethod
    def _list_versions(
        cls,
        jurisdiction: TaxJurisdictionProfile,
        document_type: str,
    ) -> list[str]:
        return list(
            ExtractionSchemaDefinition.objects.filter(
                jurisdiction=jurisdiction,
                document_type__iexact=document_type,
                is_active=True,
            )
            .order_by("-schema_version")
            .values_list("schema_version", flat=True)
        )

    @classmethod
    def _cache_key(
        cls,
        country_code: str,
        document_type: str,
        version: str | None,
        tax_regime: str | None,
    ) -> str:
        parts = [
            _CACHE_PREFIX,
            country_code.upper(),
            document_type.upper(),
            version or "latest",
        ]
        if tax_regime:
            parts.append(tax_regime.upper())
        return ":".join(parts)
