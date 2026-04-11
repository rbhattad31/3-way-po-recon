"""
ExtractionService — Schema-driven document extraction pipeline.

Orchestrates the full extraction flow:

    1. Resolve jurisdiction via JurisdictionResolutionService (4-tier)
    2. Select extraction schema via SchemaRegistryService
    3. Build extraction template from schema + field definitions
    4. Run deterministic field extraction against OCR text
    5. (Optional) LLM extraction for unresolved / low-confidence fields
    6. Merge results and compute metrics
    7. Persist jurisdiction metadata + field results on ExtractionRun

This is the **primary entry point** for document extraction.
It does NOT call JurisdictionResolverService directly — all jurisdiction
work is delegated to JurisdictionResolutionService.

Design principles:
    - Schema-driven: the schema defines WHAT to extract
    - Modular: each step is a separate classmethod, testable in isolation
    - Hybrid: deterministic first, LLM for unresolved fields (when enabled)
    - Clean output: typed dataclasses with ``to_dict()`` for API responses
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from django.conf import settings
from django.utils import timezone

from apps.extraction_configs.services.field_registry import FieldRegistryService
from apps.extraction_core.models import ExtractionSchemaDefinition
from apps.extraction_core.services.resolution_service import (
    JurisdictionResolutionService,
    ResolutionResult,
)
from apps.extraction_core.services.schema_registry import SchemaRegistryService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output data structures
# ---------------------------------------------------------------------------


@dataclass
class FieldSpec:
    """
    Specification of a single field to extract, derived from the schema
    and field registry.  Carries all the metadata needed by the
    extraction engine to locate and validate the field.
    """

    field_key: str
    display_name: str
    data_type: str
    category: str  # HEADER / LINE_ITEM / TAX / PARTY
    is_mandatory: bool = False
    is_tax_field: bool = False
    validation_regex: str = ""
    aliases: list[str] = field(default_factory=list)
    sort_order: int = 0


@dataclass
class ExtractionTemplate:
    """
    Schema-driven extraction template — defines WHAT to extract.

    Built from the resolved schema + field definitions.  Acts as the
    contract between schema selection and field extraction.
    """

    schema_id: int
    schema_name: str
    schema_version: str
    document_type: str
    country_code: str
    regime_code: str
    header_fields: list[FieldSpec] = field(default_factory=list)
    line_item_fields: list[FieldSpec] = field(default_factory=list)
    tax_fields: list[FieldSpec] = field(default_factory=list)
    mandatory_keys: set[str] = field(default_factory=set)
    all_field_keys: set[str] = field(default_factory=set)

    def to_dict(self) -> dict:
        return {
            "schema_id": self.schema_id,
            "schema_name": self.schema_name,
            "schema_version": self.schema_version,
            "document_type": self.document_type,
            "country_code": self.country_code,
            "regime_code": self.regime_code,
            "header_field_count": len(self.header_fields),
            "line_item_field_count": len(self.line_item_fields),
            "tax_field_count": len(self.tax_fields),
            "total_field_count": len(self.all_field_keys),
            "mandatory_field_count": len(self.mandatory_keys),
        }


@dataclass
class FieldEvidence:
    """
    Provenance and traceability record for a single extracted field.

    Captures *where* in the document the value came from and *how*
    it was extracted — essential for audit trails.
    """

    source_snippet: str = ""         # OCR text surrounding the match
    page_number: int | None = None   # 1-indexed page
    table_row_index: int | None = None  # 0-indexed row within a table
    extraction_method: str = "DETERMINISTIC"  # DETERMINISTIC | LLM | HYBRID | MANUAL | OCR
    llm_model: str = ""              # model name if LLM-extracted
    regex_pattern: str = ""          # regex used if deterministic
    alias_matched: str = ""          # alias/keyword that triggered match

    def to_dict(self) -> dict:
        d: dict = {
            "extraction_method": self.extraction_method,
        }
        if self.source_snippet:
            d["source_snippet"] = self.source_snippet
        if self.page_number is not None:
            d["page_number"] = self.page_number
        if self.table_row_index is not None:
            d["table_row_index"] = self.table_row_index
        if self.llm_model:
            d["llm_model"] = self.llm_model
        if self.regex_pattern:
            d["regex_pattern"] = self.regex_pattern
        if self.alias_matched:
            d["alias_matched"] = self.alias_matched
        return d


@dataclass
class FieldResult:
    """
    Result of extracting a single field.

    Combines the field specification (what was expected) with the
    extraction outcome (what was found), plus structured evidence.
    """

    field_key: str
    display_name: str = ""
    category: str = ""
    data_type: str = ""
    raw_value: str = ""
    normalized_value: str = ""
    confidence: float = 0.0
    method: str = "DETERMINISTIC"
    source_snippet: str = ""  # kept for backwards compat
    is_mandatory: bool = False
    extracted: bool = False
    evidence: FieldEvidence | None = None

    def to_dict(self) -> dict:
        d = {
            "field_key": self.field_key,
            "display_name": self.display_name,
            "category": self.category,
            "data_type": self.data_type,
            "value": self.normalized_value or self.raw_value,
            "raw_value": self.raw_value,
            "confidence": round(self.confidence, 4),
            "method": self.method,
            "is_mandatory": self.is_mandatory,
            "extracted": self.extracted,
        }
        if self.evidence:
            d["evidence"] = self.evidence.to_dict()
        return d


@dataclass
class JurisdictionMeta:
    """Jurisdiction metadata extracted from the resolution result."""

    country_code: str = ""
    regime_code: str = ""
    source: str = ""
    confidence: float = 0.0
    resolution_mode: str = ""
    warning: str = ""
    jurisdiction_id: int | None = None
    tiers_evaluated: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "country_code": self.country_code,
            "regime_code": self.regime_code,
            "source": self.source,
            "confidence": round(self.confidence, 4),
            "resolution_mode": self.resolution_mode,
            "warning": self.warning,
            "jurisdiction_id": self.jurisdiction_id,
            "tiers_evaluated": self.tiers_evaluated,
        }


@dataclass
class ConfidenceBreakdown:
    """
    Multi-dimensional confidence scores for the extraction.

    Factors in extraction method, jurisdiction resolution quality,
    and per-category field completeness.
    """

    overall: float = 0.0
    header: float = 0.0
    tax: float = 0.0
    line_item: float = 0.0
    jurisdiction: float = 0.0
    #: Whether the extraction should be flagged for human review
    requires_review: bool = False
    review_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "overall": round(self.overall, 4),
            "header": round(self.header, 4),
            "tax": round(self.tax, 4),
            "line_item": round(self.line_item, 4),
            "jurisdiction": round(self.jurisdiction, 4),
            "requires_review": self.requires_review,
            "review_reasons": self.review_reasons,
        }


@dataclass
class ExtractionExecutionResult:
    """
    Complete extraction pipeline result.

    Named ExtractionExecutionResult to distinguish from the Django model
    ExtractionResult in apps/extraction, which is the UI-facing summary record.
    This dataclass carries the full governed pipeline output in memory and is
    not persisted directly.

    Provides a clean, typed output contract with jurisdiction metadata,
    schema template, extracted fields, coverage metrics, confidence
    breakdown, and errors/warnings.
    """

    jurisdiction: JurisdictionMeta = field(default_factory=JurisdictionMeta)
    template: ExtractionTemplate | None = None
    header_fields: dict[str, FieldResult] = field(default_factory=dict)
    tax_fields: dict[str, FieldResult] = field(default_factory=dict)
    line_items: list[dict[str, FieldResult]] = field(default_factory=list)
    overall_confidence: float = 0.0
    extraction_method: str = "DETERMINISTIC"
    field_coverage_pct: float = 0.0
    mandatory_coverage_pct: float = 0.0
    resolved: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_ms: int | None = None
    llm_audit: Any = None  # LLMExtractionAudit when LLM was used
    validation: Any = None  # ValidationResult when validation was run
    confidence: ConfidenceBreakdown = field(
        default_factory=ConfidenceBreakdown,
    )
    review_routing: Any = None  # ReviewRoutingDecision when evaluated
    document_intelligence: Any = None  # DocumentIntelligenceResult
    enrichment: Any = None  # EnrichmentResult from master data matching
    page_info: dict = field(default_factory=dict)  # ParsedDocument.to_dict()
    line_item_meta: dict = field(default_factory=dict)  # LineItemExtractionResult.to_dict()

    def to_dict(self) -> dict:
        d = {
            "resolved": self.resolved,
            "jurisdiction": self.jurisdiction.to_dict(),
            "template": self.template.to_dict() if self.template else None,
            "header_fields": {
                k: v.to_dict() for k, v in self.header_fields.items()
            },
            "tax_fields": {
                k: v.to_dict() for k, v in self.tax_fields.items()
            },
            "line_items": [
                {k: v.to_dict() for k, v in line.items()}
                for line in self.line_items
            ],
            "overall_confidence": round(self.overall_confidence, 4),
            "extraction_method": self.extraction_method,
            "field_coverage_pct": round(self.field_coverage_pct, 2),
            "mandatory_coverage_pct": round(self.mandatory_coverage_pct, 2),
            "confidence": self.confidence.to_dict(),
            "errors": self.errors,
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
        }
        if self.document_intelligence and hasattr(self.document_intelligence, "to_dict"):
            d["document_intelligence"] = self.document_intelligence.to_dict()
        if self.enrichment and hasattr(self.enrichment, "to_dict"):
            d["enrichment"] = self.enrichment.to_dict()
        if self.page_info:
            d["page_info"] = self.page_info
        if self.line_item_meta:
            d["line_item_meta"] = self.line_item_meta
        if self.review_routing and hasattr(self.review_routing, "to_dict"):
            d["review_routing"] = self.review_routing.to_dict()
        if self.llm_audit and hasattr(self.llm_audit, "to_dict"):
            d["llm_audit"] = self.llm_audit.to_dict()
        if self.validation and hasattr(self.validation, "to_dict"):
            d["validation"] = self.validation.to_dict()
        return d


# DEPRECATED (target removal: 2026-Q3) — This alias exists only to support
# legacy imports.  All internal code has been migrated to
# ExtractionExecutionResult.  Do NOT use this alias in new code.
# The Django model apps.extraction.models.ExtractionResult is the UI-facing
# summary record; this dataclass is the in-memory pipeline result.
import warnings as _warnings


def __getattr__(name):
    if name == "ExtractionResult":
        _warnings.warn(
            "ExtractionResult alias is deprecated; use ExtractionExecutionResult",
            DeprecationWarning,
            stacklevel=2,
        )
        return ExtractionExecutionResult
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# ExtractionService
# ---------------------------------------------------------------------------


class ExtractionService:
    """
    Schema-driven document extraction pipeline.

    Deterministic-first, with optional LLM fallback for unresolved
    or low-confidence fields (hybrid mode).  When ``enable_llm=True``,
    fields that deterministic extraction could not resolve are sent to
    the LLM via PromptBuilderService + LLMExtractionAdapter.
    """

    #: Fields at or below this confidence are sent to LLM in hybrid mode
    LLM_CONFIDENCE_THRESHOLD: float = getattr(
        settings, "EXTRACTION_CONFIDENCE_THRESHOLD", 0.75,
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def extract(
        cls,
        *,
        ocr_text: str,
        document_type: str = "INVOICE",
        declared_country_code: str = "",
        declared_regime_code: str = "",
        vendor_id: int | None = None,
        extraction_document_id: int | None = None,
        enable_llm: bool = False,
    ) -> ExtractionExecutionResult:
        """
        Run the full extraction pipeline.

        Args:
            ocr_text:                Raw OCR text to extract from.
            document_type:           Document type (default ``INVOICE``).
            declared_country_code:   Document-level country override.
            declared_regime_code:    Document-level regime override.
            vendor_id:               Vendor PK for entity profile lookup.
            extraction_document_id:  DocumentUpload PK for persistence.
            enable_llm:              If True, run LLM extraction for
                                     unresolved / low-confidence fields.

        Returns:
            ExtractionExecutionResult with jurisdiction, template, fields, and metrics.
        """
        start = timezone.now()
        result = ExtractionExecutionResult()

        # ── Step 1: Resolve jurisdiction ──────────────────────────────
        resolution = JurisdictionResolutionService.resolve(
            ocr_text,
            declared_country_code=declared_country_code,
            declared_regime_code=declared_regime_code,
            vendor_id=vendor_id,
        )

        # ── Step 2: Extract jurisdiction metadata ─────────────────────
        result.jurisdiction = cls._build_jurisdiction_meta(resolution)

        if resolution.warning_message:
            result.warnings.append(resolution.warning_message)

        if not resolution.resolved:
            result.errors.append(
                "Jurisdiction could not be resolved from OCR text"
            )
            cls._persist_document_metadata(
                extraction_document_id, resolution, None, result,
            )
            result.duration_ms = cls._elapsed_ms(start)
            return result

        # ── Step 2b: Document intelligence layer ─────────────────────
        from apps.extraction_core.services.document_intelligence import (
            DocumentIntelligenceService,
        )
        intel = DocumentIntelligenceService.analyze(
            ocr_text,
            country_code=resolution.country_code,
            regime_code=resolution.regime_code or "",
        )
        result.document_intelligence = intel
        result.warnings.extend(intel.warnings)

        # Use classified document type if caller passed generic INVOICE
        # and classifier detected a more specific type with confidence
        effective_doc_type = document_type
        if (
            document_type == "INVOICE"
            and intel.classification.confidence >= 0.5
            and intel.classification.document_type != "INVOICE"
        ):
            effective_doc_type = intel.classification.document_type
            logger.info(
                "Document intelligence reclassified %s -> %s (%.2f)",
                document_type,
                effective_doc_type,
                intel.classification.confidence,
            )

        # ── Step 3: Select schema ─────────────────────────────────────
        schema = cls._select_schema(resolution, effective_doc_type)

        # Fall back to original document_type if reclassified type has no schema
        if not schema and effective_doc_type != document_type:
            logger.info(
                "No schema for reclassified type %s — falling back to %s",
                effective_doc_type,
                document_type,
            )
            schema = cls._select_schema(resolution, document_type)

        if not schema and resolution.resolution_mode != "AUTO":
            schema = cls._try_schema_fallback(
                resolution, ocr_text, document_type, result,
            )

        if not schema:
            result.errors.append(
                f"No active schema for country={resolution.country_code} "
                f"regime={resolution.regime_code} "
                f"document_type={document_type}"
            )
            cls._persist_document_metadata(
                extraction_document_id, resolution, None, result,
            )
            result.duration_ms = cls._elapsed_ms(start)
            return result

        # ── Step 4: Build extraction template ─────────────────────────
        template = cls._build_template(schema, resolution)
        result.template = template

        # ── Step 4b: Page-aware document parsing ─────────────────────
        from apps.extraction_core.services.page_parser import PageParser
        parsed_doc = PageParser.parse(ocr_text)
        result.page_info = parsed_doc.to_dict()
        clean_text = parsed_doc.full_clean_text or ocr_text

        # ── Step 5: Run deterministic field extraction ────────────────
        result.header_fields = cls._extract_field_group(
            template.header_fields, clean_text,
        )
        result.tax_fields = cls._extract_field_group(
            template.tax_fields, clean_text,
        )

        # Enrich evidence with page numbers
        cls._enrich_page_evidence(result, parsed_doc)

        # ── Step 5a: Table stitching + line-item extraction ───────────
        cls._extract_line_items(result, template, parsed_doc, resolution)

        # ── Step 5b: LLM extraction for unresolved fields ─────────────
        if enable_llm:
            cls._run_llm_extraction(
                result, template, clean_text, resolution,
            )

        # ── Step 6: Normalize extracted values ────────────────────────
        cls._normalize_fields(result, resolution, parsed_doc)

        # ── Step 7: Validate extracted values ─────────────────────────
        cls._validate_fields(result, template, resolution)

        # ── Step 7b: Master data enrichment ───────────────────────────
        from apps.extraction_core.services.master_data_enrichment import (
            MasterDataEnrichmentService,
        )
        try:
            enrichment = MasterDataEnrichmentService.enrich(
                extraction_result=result,
                country_code=resolution.country_code,
                regime_code=resolution.regime_code or "",
            )
            result.enrichment = enrichment
            result.warnings.extend(enrichment.warnings)
        except Exception:
            logger.exception("Master data enrichment failed — continuing")

        # ── Step 8: Confidence scoring (multi-dimensional) ────────────
        from apps.extraction_core.services.confidence_scorer import (
            ConfidenceScorer,
        )
        result.confidence = ConfidenceScorer.score(
            result, template, validation=result.validation,
        )
        # Also keep legacy coverage metrics
        cls._compute_metrics(result, template)

        # ── Step 9: Record warnings for missing mandatory fields ──────
        cls._check_mandatory_fields(result, template)

        # ── Step 9b: Review routing decision ──────────────────────────
        from apps.extraction_core.services.review_routing import (
            ReviewRoutingService,
        )
        result.review_routing = ReviewRoutingService.evaluate(
            result.confidence, result,
        )

        result.resolved = True
        result.duration_ms = cls._elapsed_ms(start)

        # ── Step 10: Persist ──────────────────────────────────────────
        cls._persist_document_metadata(
            extraction_document_id, resolution, schema, result,
        )
        cls._persist_field_results(extraction_document_id, result)

        return result

    # ------------------------------------------------------------------
    # Step 2 — Jurisdiction metadata
    # ------------------------------------------------------------------

    @classmethod
    def _build_jurisdiction_meta(
        cls,
        resolution: ResolutionResult,
    ) -> JurisdictionMeta:
        """Map ResolutionResult -> JurisdictionMeta."""
        return JurisdictionMeta(
            country_code=resolution.country_code,
            regime_code=resolution.regime_code,
            source=str(resolution.source) if resolution.source else "",
            confidence=resolution.confidence,
            resolution_mode=(
                str(resolution.resolution_mode)
                if resolution.resolution_mode
                else ""
            ),
            warning=resolution.warning_message,
            jurisdiction_id=(
                resolution.jurisdiction.pk if resolution.jurisdiction else None
            ),
            tiers_evaluated=list(resolution.tiers_evaluated),
        )

    # ------------------------------------------------------------------
    # Step 3 — Schema selection
    # ------------------------------------------------------------------

    @classmethod
    def _select_schema(
        cls,
        resolution: ResolutionResult,
        document_type: str,
    ) -> ExtractionSchemaDefinition | None:
        """
        Select schema from the SchemaRegistryService using ONLY the
        resolved jurisdiction — no direct country/regime fallback
        outside the registry.
        """
        if resolution.jurisdiction:
            lookup = SchemaRegistryService.get_schema_by_jurisdiction(
                resolution.jurisdiction, document_type,
            )
        else:
            lookup = SchemaRegistryService.get_schema(
                country_code=resolution.country_code,
                document_type=document_type,
                tax_regime=resolution.regime_code or None,
            )

        if lookup.resolved and lookup.schema:
            return lookup.schema
        return None

    @classmethod
    def _try_schema_fallback(
        cls,
        primary: ResolutionResult,
        ocr_text: str,
        document_type: str,
        result: ExtractionExecutionResult,
    ) -> ExtractionSchemaDefinition | None:
        """
        When the primary resolution's jurisdiction has no schema and
        ``fallback_to_detection_on_schema_miss`` is enabled, try
        auto-detection to find a schema under a different jurisdiction.
        """
        from apps.extraction_core.models import ExtractionRuntimeSettings

        settings = ExtractionRuntimeSettings.get_active()
        if not settings or not settings.fallback_to_detection_on_schema_miss:
            return None

        logger.info(
            "Schema miss for %s/%s — attempting auto-detection fallback",
            primary.country_code,
            primary.regime_code,
        )
        fallback = JurisdictionResolutionService.resolve_from_auto_detection(
            ocr_text=ocr_text,
        )
        if not fallback.resolved:
            return None

        schema = cls._select_schema(fallback, document_type)
        if schema:
            result.warnings.append(
                f"Schema resolved via auto-detection fallback "
                f"(detected {fallback.country_code}/{fallback.regime_code}) "
                f"because no schema exists for configured "
                f"{primary.country_code}/{primary.regime_code}"
            )
            # Update jurisdiction meta to reflect fallback
            result.jurisdiction = cls._build_jurisdiction_meta(fallback)
        return schema

    # ------------------------------------------------------------------
    # Step 4 — Template building
    # ------------------------------------------------------------------

    @classmethod
    def _build_template(
        cls,
        schema: ExtractionSchemaDefinition,
        resolution: ResolutionResult,
    ) -> ExtractionTemplate:
        """
        Build an ExtractionTemplate from the schema + FieldRegistryService.

        The template captures every field the schema expects, along with
        the extraction parameters (regex, aliases, data type) needed by
        the extraction engine.
        """
        snapshot = FieldRegistryService.get_fields_for_schema(schema)
        by_key = snapshot.by_key

        header_keys = schema.header_fields_json or []
        line_item_keys = schema.line_item_fields_json or []
        tax_keys = schema.tax_fields_json or []

        header_specs = cls._keys_to_specs(header_keys, by_key)
        line_item_specs = cls._keys_to_specs(line_item_keys, by_key)
        tax_specs = cls._keys_to_specs(tax_keys, by_key)

        all_keys = set(header_keys) | set(line_item_keys) | set(tax_keys)
        mandatory_keys = {
            fd.field_key
            for fd in snapshot.all_fields
            if fd.is_mandatory and fd.field_key in all_keys
        }

        return ExtractionTemplate(
            schema_id=schema.pk,
            schema_name=schema.name,
            schema_version=schema.schema_version,
            document_type=schema.document_type,
            country_code=resolution.country_code,
            regime_code=resolution.regime_code,
            header_fields=header_specs,
            line_item_fields=line_item_specs,
            tax_fields=tax_specs,
            mandatory_keys=mandatory_keys,
            all_field_keys=all_keys,
        )

    @classmethod
    def _keys_to_specs(
        cls,
        keys: list[str],
        by_key: dict,
    ) -> list[FieldSpec]:
        """Convert a list of field keys into FieldSpec objects."""
        specs: list[FieldSpec] = []
        for key in keys:
            fd = by_key.get(key)
            if fd:
                specs.append(
                    FieldSpec(
                        field_key=fd.field_key,
                        display_name=fd.display_name,
                        data_type=fd.data_type or "STRING",
                        category=fd.category or "HEADER",
                        is_mandatory=fd.is_mandatory,
                        is_tax_field=fd.is_tax_field,
                        validation_regex=fd.validation_regex or "",
                        aliases=fd.aliases or [],
                        sort_order=fd.sort_order or 0,
                    )
                )
            else:
                # Schema references a field key not in the registry —
                # create a minimal spec so extraction still attempts it
                specs.append(
                    FieldSpec(
                        field_key=key,
                        display_name=key.replace("_", " ").title(),
                        data_type="STRING",
                        category="HEADER",
                    )
                )
        return specs

    # ------------------------------------------------------------------
    # Step 5 — Deterministic field extraction
    # ------------------------------------------------------------------

    @classmethod
    def _extract_field_group(
        cls,
        specs: list[FieldSpec],
        ocr_text: str,
    ) -> dict[str, FieldResult]:
        """
        Run deterministic extraction for a group of field specs.

        Returns a dict keyed by field_key for direct lookup.
        """
        results: dict[str, FieldResult] = {}
        for spec in specs:
            results[spec.field_key] = cls._extract_single_field(spec, ocr_text)
        return results

    @classmethod
    def _extract_single_field(
        cls,
        spec: FieldSpec,
        ocr_text: str,
    ) -> FieldResult:
        """
        Deterministic extraction for a single field.

        Strategy:
            1. Regex extraction if ``validation_regex`` is set
            2. Alias / keyword proximity heuristic
            3. Mark as not-extracted with 0 confidence
        """
        result = FieldResult(
            field_key=spec.field_key,
            display_name=spec.display_name,
            category=spec.category,
            data_type=spec.data_type,
            is_mandatory=spec.is_mandatory,
            method="DETERMINISTIC",
        )

        # Strategy 1: Regex
        if spec.validation_regex:
            try:
                match = re.search(spec.validation_regex, ocr_text, re.IGNORECASE)
                if match:
                    result.raw_value = match.group(0)
                    result.confidence = 0.90
                    start = max(0, match.start() - 40)
                    end = min(len(ocr_text), match.end() + 40)
                    snippet = ocr_text[start:end]
                    result.source_snippet = snippet
                    result.extracted = True
                    result.evidence = FieldEvidence(
                        source_snippet=snippet,
                        extraction_method="DETERMINISTIC",
                        regex_pattern=spec.validation_regex,
                    )
                    return result
            except re.error:
                logger.warning(
                    "Invalid regex for field %s: %s",
                    spec.field_key,
                    spec.validation_regex,
                )

        # Strategy 2: Alias / keyword proximity
        search_terms = (
            [spec.field_key.replace("_", " "), spec.display_name]
            + spec.aliases
        )
        for term in search_terms:
            if not term:
                continue
            idx = ocr_text.lower().find(term.lower())
            if idx == -1:
                continue
            after = ocr_text[idx + len(term) : idx + len(term) + 100]
            value_match = re.search(r"[:\-=]\s*(.+?)(?:\n|$)", after)
            if value_match:
                result.raw_value = value_match.group(1).strip()
                result.confidence = 0.60
                snippet = ocr_text[
                    max(0, idx - 20) : idx + len(term) + 80
                ]
                result.source_snippet = snippet
                result.extracted = True
                result.evidence = FieldEvidence(
                    source_snippet=snippet,
                    extraction_method="DETERMINISTIC",
                    alias_matched=term,
                )
                return result

        # Not found
        result.confidence = 0.0
        result.extracted = False
        return result

    # ------------------------------------------------------------------
    # Step 4b / 5 helpers — Page evidence enrichment
    # ------------------------------------------------------------------

    @classmethod
    def _enrich_page_evidence(
        cls,
        result: ExtractionExecutionResult,
        parsed_doc: Any,
    ) -> None:
        """
        Enrich extracted field evidence with page numbers.

        After deterministic extraction, walks every extracted field and
        uses the ``PageParser`` to determine which page each snippet
        came from.
        """
        from apps.extraction_core.services.page_parser import (
            PageParser,
            ParsedDocument,
        )

        if not isinstance(parsed_doc, ParsedDocument):
            return
        if parsed_doc.page_count <= 1:
            return  # Single page — no enrichment needed

        for fr in list(result.header_fields.values()) + list(
            result.tax_fields.values()
        ):
            if not fr.extracted or not fr.source_snippet:
                continue

            page_num = PageParser.find_page_for_text(
                parsed_doc.pages, fr.source_snippet,
            )
            if page_num is not None:
                if fr.evidence:
                    fr.evidence.page_number = page_num
                else:
                    fr.evidence = FieldEvidence(
                        source_snippet=fr.source_snippet,
                        extraction_method=fr.method,
                        page_number=page_num,
                    )

    # ------------------------------------------------------------------
    # Step 5a — Table stitching + line-item extraction
    # ------------------------------------------------------------------

    @classmethod
    def _extract_line_items(
        cls,
        result: ExtractionExecutionResult,
        template: ExtractionTemplate,
        parsed_doc: Any,
        resolution: ResolutionResult,
    ) -> None:
        """
        Run table stitching and line-item extraction across pages.

        Uses the schema's ``line_item_fields`` to know which columns to
        look for.  Skipped if the schema defines no line-item fields.
        """
        from apps.extraction_core.services.page_parser import ParsedDocument
        from apps.extraction_core.services.table_stitcher import TableStitcher
        from apps.extraction_core.services.line_item_extractor import (
            LineItemExtractor,
        )

        if not template.line_item_fields:
            return

        if not isinstance(parsed_doc, ParsedDocument):
            return

        # Determine locale-specific decimal separator
        decimal_sep = cls._get_decimal_separator(resolution)

        try:
            # Stitch tables across pages
            stitched_tables = TableStitcher.stitch(
                parsed_doc.pages,
                decimal_separator=decimal_sep,
            )

            if not stitched_tables:
                result.warnings.append(
                    "No table regions detected for line-item extraction",
                )
                return

            # Extract line items from stitched tables
            li_result = LineItemExtractor.extract(
                tables=stitched_tables,
                template=template,
                decimal_separator=decimal_sep,
            )

            result.line_items = li_result.line_items
            result.line_item_meta = li_result.to_dict()

            if not li_result.totals_consistent:
                result.warnings.append(
                    f"Line-item totals inconsistency: "
                    f"{li_result.totals_discrepancy}",
                )

            logger.info(
                "Extracted %d line items from %d table(s)",
                len(li_result.line_items),
                len(stitched_tables),
            )

        except Exception:
            logger.exception("Line-item extraction failed — continuing")
            result.warnings.append(
                "Line-item extraction failed (see logs)",
            )

    @classmethod
    def _get_decimal_separator(
        cls,
        resolution: ResolutionResult,
    ) -> str:
        """
        Determine the locale-appropriate decimal separator.

        EU countries (DE, FR, IT, ES, NL, PT, etc.) use comma.
        Most others (US, UK, IN, AE, SA) use period.
        """
        eu_comma_countries = {
            "DE", "FR", "IT", "ES", "NL", "PT", "BE", "AT", "CH", "PL",
            "CZ", "SE", "NO", "DK", "FI", "HU", "RO", "BG", "HR", "SK",
            "SI", "GR", "BR", "AR", "CL", "CO", "TR", "RU",
        }
        country = (resolution.country_code or "").upper()
        return "," if country in eu_comma_countries else "."

    # ------------------------------------------------------------------
    # Step 6 — Normalization
    # ------------------------------------------------------------------

    @classmethod
    def _normalize_fields(
        cls,
        result: ExtractionExecutionResult,
        resolution: ResolutionResult,
        parsed_doc: Any = None,
    ) -> None:
        """
        Apply jurisdiction-driven normalization to all extracted fields,
        including line items.

        Writes ``normalized_value`` on each FieldResult in-place.
        """
        from apps.extraction_core.services.normalization_service import (
            NormalizationService,
        )

        try:
            svc = NormalizationService(
                country_code=resolution.country_code,
                regime_code=resolution.regime_code or "",
            )
            count = svc.normalize_fields(
                result.header_fields, result.tax_fields,
            )
            # Also normalize line-item fields
            for line_item in result.line_items:
                for fr in line_item.values():
                    if fr.extracted and fr.raw_value:
                        try:
                            svc._ensure_loaded()
                            if svc._normalize_field(fr):
                                count += 1
                        except Exception:
                            pass
            if count:
                logger.info("Normalized %d fields", count)
        except Exception:
            logger.exception("Normalization step failed — continuing")

    # ------------------------------------------------------------------
    # Step 7 — Validation
    # ------------------------------------------------------------------

    @classmethod
    def _validate_fields(
        cls,
        result: ExtractionExecutionResult,
        template: ExtractionTemplate,
        resolution: ResolutionResult,
    ) -> None:
        """
        Run jurisdiction-driven validation on extracted fields.

        Stores the ``ValidationResult`` on ``result.validation``.
        """
        from apps.extraction_core.services.validation_service import (
            ValidationService,
        )

        try:
            svc = ValidationService(
                country_code=resolution.country_code,
                regime_code=resolution.regime_code or "",
            )
            validation = svc.validate(
                header_fields=result.header_fields,
                tax_fields=result.tax_fields,
                template=template,
            )
            result.validation = validation
        except Exception:
            logger.exception("Validation step failed — continuing")

    # ------------------------------------------------------------------
    # Step 8 — Metrics
    # ------------------------------------------------------------------

    @classmethod
    def _compute_metrics(
        cls,
        result: ExtractionExecutionResult,
        template: ExtractionTemplate,
    ) -> None:
        """Compute overall confidence and coverage percentages."""
        all_results = list(result.header_fields.values()) + list(
            result.tax_fields.values()
        )
        # Include line-item fields in metrics
        for line_item in result.line_items:
            all_results.extend(line_item.values())

        extracted = [r for r in all_results if r.extracted]

        # Overall confidence (average of extracted fields)
        if extracted:
            result.overall_confidence = sum(
                r.confidence for r in extracted
            ) / len(extracted)

        # Field coverage (header + tax, not counting line-item expansion)
        total = len(template.all_field_keys)
        header_tax_extracted = [
            r for r in list(result.header_fields.values())
            + list(result.tax_fields.values())
            if r.extracted
        ]
        if total:
            result.field_coverage_pct = len(header_tax_extracted) / total * 100

        # Mandatory coverage
        mandatory_count = len(template.mandatory_keys)
        if mandatory_count:
            mandatory_extracted = sum(
                1
                for r in list(result.header_fields.values())
                + list(result.tax_fields.values())
                if r.extracted and r.field_key in template.mandatory_keys
            )
            result.mandatory_coverage_pct = (
                mandatory_extracted / mandatory_count * 100
            )

    # ------------------------------------------------------------------
    # Step 9 — Mandatory field warnings
    # ------------------------------------------------------------------

    @classmethod
    def _check_mandatory_fields(
        cls,
        result: ExtractionExecutionResult,
        template: ExtractionTemplate,
    ) -> None:
        """Append warnings for any mandatory fields not extracted."""
        all_results = {**result.header_fields, **result.tax_fields}
        for key in template.mandatory_keys:
            fr = all_results.get(key)
            if not fr or not fr.extracted:
                result.warnings.append(
                    f"Mandatory field '{key}' not extracted"
                )

    # ------------------------------------------------------------------
    # Step 5b — LLM extraction (hybrid)
    # ------------------------------------------------------------------

    @classmethod
    def _run_llm_extraction(
        cls,
        result: ExtractionExecutionResult,
        template: ExtractionTemplate,
        ocr_text: str,
        resolution: ResolutionResult,
    ) -> None:
        """
        Run LLM extraction for fields that deterministic extraction
        did not resolve or resolved with low confidence.

        Merges LLM results into the ExtractionExecutionResult, updating
        extraction_method to HYBRID when LLM contributes fields.
        """
        from apps.extraction_core.services.llm_extraction_adapter import (
            LLMExtractionAdapter,
        )

        unresolved = cls._find_unresolved_fields(result, template)
        if not unresolved:
            logger.info("All fields resolved deterministically — skipping LLM")
            return

        logger.info(
            "LLM extraction: %d unresolved fields to extract",
            len(unresolved),
        )

        # Fetch jurisdiction profile for tax-specific prompt guidance
        jurisdiction_profile = resolution.jurisdiction

        adapter = LLMExtractionAdapter()
        llm_results, audit = adapter.extract_fields(
            template=template,
            ocr_text=ocr_text,
            jurisdiction_profile=jurisdiction_profile,
            unresolved_field_keys=unresolved,
        )

        result.llm_audit = audit

        if not llm_results:
            result.warnings.append(
                "LLM extraction returned no results"
                + (f": {audit.error_message}" if audit.error_message else "")
            )
            return

        # Merge LLM results into the extraction result
        merged_count = cls._merge_llm_results(result, llm_results)

        if merged_count > 0:
            result.extraction_method = "HYBRID"
            logger.info(
                "Merged %d LLM-extracted fields (method=HYBRID)", merged_count,
            )

    @classmethod
    def _find_unresolved_fields(
        cls,
        result: ExtractionExecutionResult,
        template: ExtractionTemplate,
    ) -> set[str]:
        """
        Identify field keys that were not extracted or have confidence
        below the LLM threshold.
        """
        unresolved: set[str] = set()
        all_results = {**result.header_fields, **result.tax_fields}

        for key in template.all_field_keys:
            fr = all_results.get(key)
            if not fr or not fr.extracted:
                unresolved.add(key)
            elif fr.confidence < cls.LLM_CONFIDENCE_THRESHOLD:
                unresolved.add(key)

        return unresolved

    @classmethod
    def _merge_llm_results(
        cls,
        result: ExtractionExecutionResult,
        llm_results: dict[str, FieldResult],
    ) -> int:
        """
        Merge LLM field results into the extraction result.

        Only replaces a deterministic result if the LLM result has
        higher confidence or the deterministic result was not extracted.
        Returns the count of fields merged.
        """
        merged = 0

        for field_key, llm_fr in llm_results.items():
            if not llm_fr.extracted:
                continue

            # Determine which dict the field belongs to
            if field_key in result.header_fields:
                existing = result.header_fields[field_key]
                if not existing.extracted or llm_fr.confidence > existing.confidence:
                    result.header_fields[field_key] = llm_fr
                    merged += 1
            elif field_key in result.tax_fields:
                existing = result.tax_fields[field_key]
                if not existing.extracted or llm_fr.confidence > existing.confidence:
                    result.tax_fields[field_key] = llm_fr
                    merged += 1
            else:
                # Field not in deterministic results — could be header or tax
                # based on the FieldResult category
                if llm_fr.category == "TAX":
                    result.tax_fields[field_key] = llm_fr
                else:
                    result.header_fields[field_key] = llm_fr
                merged += 1

        return merged

    # ------------------------------------------------------------------
    # Step 10 — Persistence
    # ------------------------------------------------------------------

    @classmethod
    def _persist_document_metadata(
        cls,
        extraction_document_id: int | None,
        resolution: ResolutionResult,
        schema: ExtractionSchemaDefinition | None,
        result: ExtractionExecutionResult,
    ) -> None:
        """
        Persist jurisdiction + extraction metadata on ExtractionRun.

        Fire-and-forget -- errors are logged, not raised.

        NOTE: ExtractionDocument was removed. Metadata is now persisted
        directly on ExtractionRun by ExtractionPipeline.  This method is
        retained as a no-op for callers outside the governed pipeline.
        """
        return

    @classmethod
    def _persist_field_results(
        cls,
        extraction_document_id: int | None,
        result: ExtractionExecutionResult,
    ) -> None:
        """
        Persist per-field extraction results as ExtractionFieldValue rows.

        NOTE: ExtractionDocument/ExtractionFieldResult were removed.
        Field results are now persisted on ExtractionFieldValue by
        ExtractionPipeline.  This method is retained as a no-op.
        """
        return

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _elapsed_ms(cls, start) -> int:
        return int((timezone.now() - start).total_seconds() * 1000)
