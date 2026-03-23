"""Service for schema registry UI operations."""
from __future__ import annotations

import difflib
import json
from typing import Any

from django.db.models import QuerySet

from apps.extraction_core.models import ExtractionPromptTemplate, ExtractionSchemaDefinition
from apps.extraction_core.services.extraction_audit import ExtractionAuditService


class SchemaRegistryUIService:
    """UI-oriented service for schema management."""

    @classmethod
    def list_schemas(cls, filters: dict | None = None) -> QuerySet:
        qs = ExtractionSchemaDefinition.objects.select_related("jurisdiction").all()
        if not filters:
            return qs
        if filters.get("jurisdiction_id"):
            qs = qs.filter(jurisdiction_id=filters["jurisdiction_id"])
        if filters.get("document_type"):
            qs = qs.filter(document_type__iexact=filters["document_type"])
        if filters.get("is_active") is not None:
            qs = qs.filter(is_active=filters["is_active"])
        if filters.get("search"):
            qs = qs.filter(name__icontains=filters["search"])
        return qs

    @classmethod
    def get_schema_detail(cls, pk: int) -> ExtractionSchemaDefinition | None:
        return ExtractionSchemaDefinition.objects.select_related("jurisdiction").filter(pk=pk).first()

    @classmethod
    def get_linked_prompts(cls, schema: ExtractionSchemaDefinition) -> QuerySet:
        """Get prompts associated with this schema's jurisdiction + doc type."""
        return ExtractionPromptTemplate.objects.filter(
            country_code=schema.jurisdiction.country_code,
            document_type=schema.document_type,
        ).order_by("-version")

    @classmethod
    def get_version_history(cls, schema: ExtractionSchemaDefinition) -> QuerySet:
        return ExtractionSchemaDefinition.objects.filter(
            jurisdiction=schema.jurisdiction,
            document_type=schema.document_type,
        ).order_by("-schema_version")

    @classmethod
    def clone_schema(cls, pk: int, user) -> ExtractionSchemaDefinition | None:
        source = cls.get_schema_detail(pk)
        if not source:
            return None
        versions = (
            ExtractionSchemaDefinition.objects.filter(
                jurisdiction=source.jurisdiction,
                document_type=source.document_type,
            )
            .order_by("-schema_version")
            .values_list("schema_version", flat=True)
        )
        max_ver = "1.0"
        for v in versions:
            try:
                parts = v.split(".")
                major = int(parts[0])
                minor = int(parts[1]) if len(parts) > 1 else 0
                max_ver = f"{major}.{minor + 1}"
                break
            except (ValueError, IndexError):
                pass

        clone = ExtractionSchemaDefinition(
            jurisdiction=source.jurisdiction,
            document_type=source.document_type,
            schema_version=max_ver,
            name=f"{source.name} (v{max_ver})",
            description=source.description,
            header_fields_json=source.header_fields_json,
            line_item_fields_json=source.line_item_fields_json,
            tax_fields_json=source.tax_fields_json,
            config_json=source.config_json,
            is_active=False,
            created_by=user,
            updated_by=user,
        )
        clone.save()
        return clone

    @classmethod
    def validate_schema_json(cls, schema: ExtractionSchemaDefinition) -> list[str]:
        """Validate schema JSON fields and return error messages."""
        errors = []
        for field_name in ["header_fields_json", "line_item_fields_json", "tax_fields_json"]:
            val = getattr(schema, field_name, None)
            if val is not None and not isinstance(val, list):
                errors.append(f"{field_name} must be a list.")
        if schema.config_json and not isinstance(schema.config_json, dict):
            errors.append("config_json must be a dictionary.")
        return errors

    @classmethod
    def get_output_contract_preview(cls, schema: ExtractionSchemaDefinition) -> dict:
        """Build a preview of the expected output contract from schema fields."""
        contract = {"header": {}, "line_items": [{}], "tax": {}}
        for field_key in (schema.header_fields_json or []):
            contract["header"][field_key] = "<value>"
        for field_key in (schema.line_item_fields_json or []):
            contract["line_items"][0][field_key] = "<value>"
        for field_key in (schema.tax_fields_json or []):
            contract["tax"][field_key] = "<value>"
        return contract


class SchemaCompareService:
    """Compare two schema versions."""

    @classmethod
    def compare(cls, id1: int, id2: int) -> dict | None:
        s1 = ExtractionSchemaDefinition.objects.select_related("jurisdiction").filter(pk=id1).first()
        s2 = ExtractionSchemaDefinition.objects.select_related("jurisdiction").filter(pk=id2).first()
        if not s1 or not s2:
            return None

        def json_lines(obj):
            return json.dumps(obj, indent=2, default=str).splitlines(keepends=True)

        header_diff = list(difflib.unified_diff(
            json_lines(s1.header_fields_json),
            json_lines(s2.header_fields_json),
            fromfile=f"v{s1.schema_version}",
            tofile=f"v{s2.schema_version}",
        ))
        line_item_diff = list(difflib.unified_diff(
            json_lines(s1.line_item_fields_json),
            json_lines(s2.line_item_fields_json),
            fromfile=f"v{s1.schema_version}",
            tofile=f"v{s2.schema_version}",
        ))
        tax_diff = list(difflib.unified_diff(
            json_lines(s1.tax_fields_json),
            json_lines(s2.tax_fields_json),
            fromfile=f"v{s1.schema_version}",
            tofile=f"v{s2.schema_version}",
        ))

        return {
            "schema_1": s1,
            "schema_2": s2,
            "header_diff": header_diff,
            "line_item_diff": line_item_diff,
            "tax_diff": tax_diff,
            "metadata_diff": {
                "name": (s1.name, s2.name),
                "document_type": (s1.document_type, s2.document_type),
                "is_active": (s1.is_active, s2.is_active),
            },
        }
