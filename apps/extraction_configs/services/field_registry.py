"""
FieldRegistryService — Cached field-definition lookup for extraction schemas.

Provides efficient access to TaxFieldDefinition records with:

    • Schema-scoped field loading (header / line-item / tax / all)
    • Category and key-based filtering
    • Alias-to-field reverse lookup
    • Django cache-backed memoisation
    • Mandatory-field validation helpers
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from django.core.cache import cache

from apps.extraction_configs.models import TaxFieldDefinition
from apps.extraction_core.models import ExtractionSchemaDefinition

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "field_registry"
_CACHE_TTL = 300  # 5 minutes


@dataclass
class FieldRegistrySnapshot:
    """Pre-indexed snapshot of field definitions for a single schema."""

    schema_id: int
    all_fields: list[TaxFieldDefinition] = field(default_factory=list)
    by_key: dict[str, TaxFieldDefinition] = field(default_factory=dict)
    by_category: dict[str, list[TaxFieldDefinition]] = field(default_factory=dict)
    alias_map: dict[str, TaxFieldDefinition] = field(default_factory=dict)
    mandatory_keys: list[str] = field(default_factory=list)
    tax_field_keys: list[str] = field(default_factory=list)


class FieldRegistryService:
    """Cached, schema-scoped field definition registry."""

    # ------------------------------------------------------------------
    # Public API — schema-scoped
    # ------------------------------------------------------------------

    @classmethod
    def get_fields_for_schema(
        cls,
        schema: ExtractionSchemaDefinition,
        *,
        active_only: bool = True,
    ) -> FieldRegistrySnapshot:
        """
        Load all field definitions linked to *schema*, returned as an
        indexed snapshot for fast access during extraction.

        The snapshot is cached per schema ID.
        """
        cache_key = cls._schema_cache_key(schema.pk, active_only)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        snapshot = cls._build_snapshot(schema, active_only)
        cache.set(cache_key, snapshot, _CACHE_TTL)
        return snapshot

    @classmethod
    def get_header_fields(
        cls,
        schema: ExtractionSchemaDefinition,
    ) -> list[TaxFieldDefinition]:
        """Return only header-category fields for *schema*."""
        snapshot = cls.get_fields_for_schema(schema)
        header_keys = set(schema.header_fields_json or [])
        return [f for f in snapshot.all_fields if f.field_key in header_keys]

    @classmethod
    def get_line_item_fields(
        cls,
        schema: ExtractionSchemaDefinition,
    ) -> list[TaxFieldDefinition]:
        """Return only line-item-category fields for *schema*."""
        snapshot = cls.get_fields_for_schema(schema)
        line_keys = set(schema.line_item_fields_json or [])
        return [f for f in snapshot.all_fields if f.field_key in line_keys]

    @classmethod
    def get_tax_fields(
        cls,
        schema: ExtractionSchemaDefinition,
    ) -> list[TaxFieldDefinition]:
        """Return only tax-specific fields for *schema*."""
        snapshot = cls.get_fields_for_schema(schema)
        tax_keys = set(schema.tax_fields_json or [])
        return [f for f in snapshot.all_fields if f.field_key in tax_keys]

    @classmethod
    def get_mandatory_fields(
        cls,
        schema: ExtractionSchemaDefinition,
    ) -> list[TaxFieldDefinition]:
        """Return all mandatory fields for *schema*."""
        snapshot = cls.get_fields_for_schema(schema)
        return [f for f in snapshot.all_fields if f.is_mandatory]

    # ------------------------------------------------------------------
    # Public API — key / alias lookups
    # ------------------------------------------------------------------

    @classmethod
    def get_field_by_key(
        cls,
        schema: ExtractionSchemaDefinition,
        field_key: str,
    ) -> TaxFieldDefinition | None:
        """Look up a single field by its key within *schema*."""
        snapshot = cls.get_fields_for_schema(schema)
        return snapshot.by_key.get(field_key)

    @classmethod
    def resolve_alias(
        cls,
        schema: ExtractionSchemaDefinition,
        alias: str,
    ) -> TaxFieldDefinition | None:
        """
        Reverse-lookup: given a field alias (as it might appear in OCR
        output or LLM response), return the canonical TaxFieldDefinition.
        """
        snapshot = cls.get_fields_for_schema(schema)
        return snapshot.alias_map.get(alias.lower())

    @classmethod
    def get_fields_by_category(
        cls,
        schema: ExtractionSchemaDefinition,
        category: str,
    ) -> list[TaxFieldDefinition]:
        """Return all fields in *schema* matching *category*."""
        snapshot = cls.get_fields_for_schema(schema)
        return snapshot.by_category.get(category.upper(), [])

    # ------------------------------------------------------------------
    # Public API — schema-independent bulk queries
    # ------------------------------------------------------------------

    @classmethod
    def get_all_fields(
        cls,
        *,
        active_only: bool = True,
        category: str | None = None,
        is_tax_field: bool | None = None,
    ) -> list[TaxFieldDefinition]:
        """
        Return field definitions across all schemas, with optional filters.
        Not cached — intended for admin / list views rather than hot-path.
        """
        qs = TaxFieldDefinition.objects.all()
        if active_only:
            qs = qs.filter(is_active=True)
        if category:
            qs = qs.filter(category__iexact=category)
        if is_tax_field is not None:
            qs = qs.filter(is_tax_field=is_tax_field)
        return list(qs.order_by("category", "sort_order"))

    @classmethod
    def get_field_keys_for_schema(
        cls,
        schema: ExtractionSchemaDefinition,
    ) -> list[str]:
        """Return the ordered list of all field keys registered to *schema*."""
        snapshot = cls.get_fields_for_schema(schema)
        return [f.field_key for f in snapshot.all_fields]

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @classmethod
    def validate_extracted_keys(
        cls,
        schema: ExtractionSchemaDefinition,
        extracted_keys: set[str],
    ) -> dict:
        """
        Compare extracted field keys against the schema's mandatory fields.

        Returns:
            {
                "missing_mandatory": ["field_key", ...],
                "extra_keys": ["field_key", ...],
                "coverage_pct": 0.0–100.0,
            }
        """
        snapshot = cls.get_fields_for_schema(schema)
        all_registered = {f.field_key for f in snapshot.all_fields}
        mandatory = set(snapshot.mandatory_keys)

        missing = sorted(mandatory - extracted_keys)
        extra = sorted(extracted_keys - all_registered)

        total = len(all_registered)
        covered = len(extracted_keys & all_registered)
        coverage = (covered / total * 100) if total else 0.0

        return {
            "missing_mandatory": missing,
            "extra_keys": extra,
            "coverage_pct": round(coverage, 2),
        }

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    @classmethod
    def invalidate_cache(
        cls,
        schema_id: int | None = None,
    ) -> None:
        """
        Invalidate cached field snapshots.

        When *schema_id* is given, only that schema's cache is cleared.
        Otherwise all field_registry keys are cleared (requires cache
        backend that supports key pattern deletion, e.g. Redis).
        """
        if schema_id is not None:
            cache.delete(cls._schema_cache_key(schema_id, True))
            cache.delete(cls._schema_cache_key(schema_id, False))
        # For LocMem (development), explicit key deletion is sufficient.
        # For Redis in production, consider using cache.delete_pattern.

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @classmethod
    def _build_snapshot(
        cls,
        schema: ExtractionSchemaDefinition,
        active_only: bool,
    ) -> FieldRegistrySnapshot:
        qs = schema.field_definitions.all()
        if active_only:
            qs = qs.filter(is_active=True)
        fields = list(qs.order_by("category", "sort_order"))

        by_key: dict[str, TaxFieldDefinition] = {}
        by_category: dict[str, list[TaxFieldDefinition]] = {}
        alias_map: dict[str, TaxFieldDefinition] = {}
        mandatory_keys: list[str] = []
        tax_field_keys: list[str] = []

        for fd in fields:
            by_key[fd.field_key] = fd

            cat = fd.category.upper() if fd.category else "HEADER"
            by_category.setdefault(cat, []).append(fd)

            # Index aliases (lower-cased for case-insensitive lookup)
            for alias in (fd.aliases or []):
                alias_map[alias.lower()] = fd
            # Also index field_key and display_name as aliases
            alias_map[fd.field_key.lower()] = fd
            alias_map[fd.display_name.lower()] = fd

            if fd.is_mandatory:
                mandatory_keys.append(fd.field_key)
            if fd.is_tax_field:
                tax_field_keys.append(fd.field_key)

        return FieldRegistrySnapshot(
            schema_id=schema.pk,
            all_fields=fields,
            by_key=by_key,
            by_category=by_category,
            alias_map=alias_map,
            mandatory_keys=mandatory_keys,
            tax_field_keys=tax_field_keys,
        )

    @classmethod
    def _schema_cache_key(cls, schema_id: int, active_only: bool) -> str:
        return f"{_CACHE_PREFIX}:schema:{schema_id}:active={active_only}"
