"""
BaseExtractionService — Schema-driven extraction orchestrator.

Provides the core extraction pipeline interface that:
1. Resolves jurisdiction from OCR text
2. Selects the appropriate schema
3. Runs deterministic extraction first (regex / rules)
4. Falls back to LLM extraction for unresolved fields (future)
5. Returns structured results with per-field evidence

This base service is designed to be extended by jurisdiction-specific
or document-type-specific subclasses.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from django.utils import timezone

from apps.extraction_configs.models import TaxFieldDefinition
from apps.extraction_configs.services.field_registry import FieldRegistryService
from apps.extraction_core.models import ExtractionSchemaDefinition, TaxJurisdictionProfile
from apps.extraction_core.services.resolution_service import (
    JurisdictionResolutionService,
    ResolutionResult,
)
from apps.extraction_core.services.schema_registry import SchemaRegistryService

logger = logging.getLogger(__name__)


@dataclass
class FieldExtractionResult:
    """Result of extracting a single field."""
    field_key: str
    raw_value: str = ""
    normalized_value: str = ""
    confidence: float = 0.0
    method: str = "DETERMINISTIC"
    source_snippet: str = ""
    page_number: int | None = None
    line_item_index: int | None = None
    is_valid: bool | None = None
    validation_message: str = ""


@dataclass
class ExtractionOutput:
    """Aggregate result from the extraction pipeline."""
    jurisdiction_resolution: ResolutionResult | None = None
    schema: ExtractionSchemaDefinition | None = None
    header_fields: list[FieldExtractionResult] = field(default_factory=list)
    line_items: list[list[FieldExtractionResult]] = field(default_factory=list)
    tax_fields: list[FieldExtractionResult] = field(default_factory=list)
    overall_confidence: float = 0.0
    extraction_method: str = "DETERMINISTIC"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_ms: int | None = None

    def to_dict(self) -> dict:
        return {
            "jurisdiction": (
                self.jurisdiction_resolution.to_dict()
                if self.jurisdiction_resolution
                else None
            ),
            "schema": {
                "id": self.schema.pk,
                "name": self.schema.name,
                "version": self.schema.schema_version,
            } if self.schema else None,
            "header_fields": {f.field_key: f.normalized_value or f.raw_value for f in self.header_fields},
            "line_items": [
                {f.field_key: f.normalized_value or f.raw_value for f in line}
                for line in self.line_items
            ],
            "tax_fields": {f.field_key: f.normalized_value or f.raw_value for f in self.tax_fields},
            "overall_confidence": round(self.overall_confidence, 4),
            "extraction_method": self.extraction_method,
            "errors": self.errors,
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
        }


class BaseExtractionService:
    """
    Schema-driven extraction pipeline.

    Deterministic-first: tries regex / rule-based extraction for every
    field defined in the schema before considering LLM fallback.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def extract(
        cls,
        ocr_text: str,
        *,
        declared_country_code: str = "",
        declared_regime_code: str = "",
        vendor_id: int | None = None,
        document_type: str = "INVOICE",
        extraction_document_id: int | None = None,
    ) -> ExtractionOutput:
        """
        Run the full extraction pipeline on *ocr_text*.

        Steps:
            1. Resolve jurisdiction (4-tier: override → entity → settings → auto)
            2. Select schema for the resolved jurisdiction
            3. Fallback schema resolution if primary schema missing
            4. Extract header, line-item, and tax fields
            5. Compute confidence
            6. Persist jurisdiction metadata on ExtractionDocument (if provided)

        Args:
            ocr_text:                Raw OCR text to extract from.
            declared_country_code:   Document-level country override (Tier 1).
            declared_regime_code:    Document-level regime override (Tier 1).
            vendor_id:               Vendor PK for entity profile lookup (Tier 2).
            document_type:           Document type (default INVOICE).
            extraction_document_id:  Optional ExtractionDocument PK to persist
                                     jurisdiction metadata on.
        """
        start = timezone.now()
        output = ExtractionOutput()

        # 1 — Jurisdiction resolution (4-tier precedence chain)
        resolution = JurisdictionResolutionService.resolve(
            ocr_text,
            declared_country_code=declared_country_code,
            declared_regime_code=declared_regime_code,
            vendor_id=vendor_id,
        )
        output.jurisdiction_resolution = resolution

        if resolution.warning_message:
            output.warnings.append(resolution.warning_message)

        if not resolution.resolved:
            output.errors.append("Jurisdiction could not be resolved from OCR text")
            cls._persist_jurisdiction_metadata(extraction_document_id, resolution, None)
            output.duration_ms = cls._elapsed_ms(start)
            return output

        # 2 — Schema selection using resolved jurisdiction
        schema = cls._select_schema(resolution, document_type)

        # 3 — Fallback: if no schema found and resolution was not AUTO,
        #     try auto-detection to find a schema under a different jurisdiction
        if not schema and resolution.resolution_mode != "AUTO":
            schema = cls._try_schema_fallback(resolution, ocr_text, document_type, output)

        if not schema:
            output.errors.append(
                f"No active schema found for country={resolution.country_code} "
                f"regime={resolution.regime_code} document_type={document_type}"
            )
            cls._persist_jurisdiction_metadata(extraction_document_id, resolution, None)
            output.duration_ms = cls._elapsed_ms(start)
            return output

        output.schema = schema

        # 4 — Persist jurisdiction metadata on ExtractionDocument
        cls._persist_jurisdiction_metadata(extraction_document_id, resolution, schema)

        # 5 — Load field definitions (via FieldRegistryService — cached + indexed)
        field_snapshot = FieldRegistryService.get_fields_for_schema(schema)

        # 6 — Extract header fields
        header_defs = FieldRegistryService.get_header_fields(schema)
        output.header_fields = cls._extract_fields(header_defs, ocr_text)

        # 7 — Extract tax fields
        tax_defs = FieldRegistryService.get_tax_fields(schema)
        output.tax_fields = cls._extract_fields(tax_defs, ocr_text)

        # 8 — Line items are not handled deterministically in Phase 1
        # (requires table parsing; will be added in Phase 2)

        # 9 — Compute overall confidence
        all_results = output.header_fields + output.tax_fields
        if all_results:
            output.overall_confidence = sum(r.confidence for r in all_results) / len(all_results)

        # 10 — Validate mandatory fields (via FieldRegistryService)
        mandatory_defs = FieldRegistryService.get_mandatory_fields(schema)
        cls._validate_mandatory(output, mandatory_defs)

        output.duration_ms = cls._elapsed_ms(start)
        return output

    # ------------------------------------------------------------------
    # Schema selection + fallback
    # ------------------------------------------------------------------

    @classmethod
    def _select_schema(
        cls,
        resolution: ResolutionResult,
        document_type: str,
    ) -> ExtractionSchemaDefinition | None:
        """
        Select schema via the resolved jurisdiction.

        Prefers the jurisdiction profile object when available,
        falls back to country_code + regime_code lookup.
        """
        if resolution.jurisdiction:
            result = SchemaRegistryService.get_schema_by_jurisdiction(
                resolution.jurisdiction, document_type,
            )
        else:
            result = SchemaRegistryService.get_schema(
                country_code=resolution.country_code,
                document_type=document_type,
                tax_regime=resolution.regime_code or None,
            )

        if result.resolved and result.schema:
            return result.schema
        return None

    @classmethod
    def _try_schema_fallback(
        cls,
        primary_resolution: ResolutionResult,
        ocr_text: str,
        document_type: str,
        output: ExtractionOutput,
    ) -> ExtractionSchemaDefinition | None:
        """
        When schema is missing for the primary resolution and the system
        setting ``fallback_to_detection_on_schema_miss`` is enabled,
        attempt auto-detection to find a schema under a different
        jurisdiction.
        """
        from apps.extraction_core.models import ExtractionRuntimeSettings

        settings = ExtractionRuntimeSettings.get_active()
        if not settings or not settings.fallback_to_detection_on_schema_miss:
            return None

        logger.info(
            "Schema not found for %s/%s — attempting auto-detection fallback",
            primary_resolution.country_code,
            primary_resolution.regime_code,
        )
        fallback = JurisdictionResolutionService.resolve_from_auto_detection(
            ocr_text=ocr_text,
        )
        if not fallback.resolved:
            return None

        schema = cls._select_schema(fallback, document_type)
        if schema:
            output.warnings.append(
                f"Schema resolved via auto-detection fallback "
                f"(detected {fallback.country_code}/{fallback.regime_code}) "
                f"because no schema exists for configured "
                f"{primary_resolution.country_code}/{primary_resolution.regime_code}"
            )
            # Update the output's resolution to reflect the fallback
            output.jurisdiction_resolution = fallback
        return schema

    # ------------------------------------------------------------------
    # Extraction logic
    # ------------------------------------------------------------------

    @classmethod
    def _extract_fields(
        cls,
        field_defs: list[TaxFieldDefinition],
        ocr_text: str,
    ) -> list[FieldExtractionResult]:
        """Extract each field using deterministic regex / rules."""
        results: list[FieldExtractionResult] = []
        for fd in field_defs:
            result = cls._extract_single_field(fd, ocr_text)
            results.append(result)
        return results

    @classmethod
    def _extract_single_field(
        cls,
        fd: TaxFieldDefinition,
        ocr_text: str,
    ) -> FieldExtractionResult:
        """
        Try to extract a single field deterministically.

        Strategy:
            1. If field has a validation_regex, use it as an extraction regex
            2. Try alias-based keyword search
            3. Mark as low-confidence if not found
        """
        result = FieldExtractionResult(field_key=fd.field_key, method="DETERMINISTIC")

        # Strategy 1: Use validation_regex as extraction pattern
        if fd.validation_regex:
            try:
                match = re.search(fd.validation_regex, ocr_text, re.IGNORECASE)
                if match:
                    result.raw_value = match.group(0)
                    result.confidence = 0.90
                    # Capture surrounding context as evidence
                    start = max(0, match.start() - 40)
                    end = min(len(ocr_text), match.end() + 40)
                    result.source_snippet = ocr_text[start:end]
                    return result
            except re.error:
                logger.warning("Invalid regex for field %s: %s", fd.field_key, fd.validation_regex)

        # Strategy 2: Alias-based keyword proximity search
        search_terms = [fd.field_key.replace("_", " "), fd.display_name] + (fd.aliases or [])
        for term in search_terms:
            if not term:
                continue
            idx = ocr_text.lower().find(term.lower())
            if idx != -1:
                # Extract the value following the keyword (simple heuristic)
                after = ocr_text[idx + len(term):idx + len(term) + 100]
                # Look for a colon/equals separator then grab until newline
                value_match = re.search(r'[:\-=]\s*(.+?)(?:\n|$)', after)
                if value_match:
                    result.raw_value = value_match.group(1).strip()
                    result.confidence = 0.60
                    result.source_snippet = ocr_text[max(0, idx - 20):idx + len(term) + 80]
                    return result

        # Not found
        result.confidence = 0.0
        return result

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @classmethod
    def _validate_mandatory(
        cls,
        output: ExtractionOutput,
        field_defs: list[TaxFieldDefinition],
    ) -> None:
        """Check that all mandatory fields were extracted."""
        extracted_keys = {
            r.field_key for r in (output.header_fields + output.tax_fields) if r.raw_value
        }
        for fd in field_defs:
            if fd.is_mandatory and fd.field_key not in extracted_keys:
                output.warnings.append(f"Mandatory field '{fd.field_key}' not extracted")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _elapsed_ms(cls, start) -> int:
        return int((timezone.now() - start).total_seconds() * 1000)

    # ------------------------------------------------------------------
    # Jurisdiction metadata persistence
    # ------------------------------------------------------------------

    @classmethod
    def _persist_jurisdiction_metadata(
        cls,
        extraction_document_id: int | None,
        resolution: ResolutionResult,
        schema: ExtractionSchemaDefinition | None,
    ) -> None:
        """
        Persist the jurisdiction resolution metadata on an
        ExtractionDocument record.

        This is a fire-and-forget helper — errors are logged but
        do not interrupt the extraction pipeline.
        """
        if not extraction_document_id:
            return

        try:
            from apps.extraction_documents.models import ExtractionDocument

            update_fields = {
                "jurisdiction_source": str(resolution.source) if resolution.source else "",
                "jurisdiction_resolution_mode": str(resolution.resolution_mode) if resolution.resolution_mode else "",
                "jurisdiction_warning": resolution.warning_message,
                "jurisdiction_confidence": resolution.confidence if resolution.resolved else None,
            }
            if resolution.jurisdiction:
                update_fields["resolved_jurisdiction"] = resolution.jurisdiction
            if schema:
                update_fields["resolved_schema"] = schema
            if resolution.detection_result:
                update_fields["jurisdiction_signals_json"] = {
                    "tiers_evaluated": resolution.tiers_evaluated,
                    "detection": resolution.detection_result.to_dict(),
                }
            elif resolution.tiers_evaluated:
                update_fields["jurisdiction_signals_json"] = {
                    "tiers_evaluated": resolution.tiers_evaluated,
                }

            ExtractionDocument.objects.filter(
                pk=extraction_document_id,
            ).update(**update_fields)
        except Exception:
            logger.exception(
                "Failed to persist jurisdiction metadata on ExtractionDocument %s",
                extraction_document_id,
            )
