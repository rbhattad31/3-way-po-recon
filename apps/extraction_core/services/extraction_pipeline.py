"""
ExtractionPipeline — Enhanced extraction orchestrator.

Wires together ALL Phase 1-11 components into a single governed pipeline:

    1. Resolve jurisdiction (settings-only: entity profile | runtime default)
    2. Select extraction schema via SchemaRegistryService
    3. Build prompt via PromptBuilderService
    4. Run deterministic extraction (existing ExtractionService)
    5. (Optional) LLM extraction for unresolved fields
    6. Build ExtractionOutputContract
    7. Enhanced normalization (EnhancedNormalizationService)
    8. Enhanced validation (EnhancedValidationService)
    9. Evidence capture (EvidenceCaptureService)
    10. Review routing (ReviewRoutingEngine)
    11. Persist ExtractionRun + field values + line items
    12. Audit events at every step

This is the **new primary entry point** for governed extraction.
The legacy ExtractionService.extract() still works unchanged.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from django.conf import settings
from django.utils import timezone

from apps.core.enums import (
    ExtractionRunStatus,
    JurisdictionSource,
)
from apps.extraction_core.models import (
    ExtractionEvidence,
    ExtractionFieldValue,
    ExtractionIssue,
    ExtractionLineItem,
    ExtractionOCRText,
    ExtractionRun,
    ExtractionRuntimeSettings,
    ExtractionSchemaDefinition,
    TaxJurisdictionProfile,
)
from apps.extraction_core.services.evidence_service import EvidenceCaptureService
from apps.extraction_core.services.enhanced_normalization import (
    EnhancedNormalizationService,
)
from apps.extraction_core.services.enhanced_validation import (
    EnhancedValidationService,
)
from apps.extraction_core.services.extraction_audit import ExtractionAuditService
from apps.extraction_core.services.output_contract import (
    ExtractionOutputContract,
    FieldValue,
    LineItemRow,
    MetaBlock,
    TaxBlock,
    WarningItem,
)
from apps.extraction_core.services.prompt_builder_service import PromptBuilderService
from apps.extraction_core.services.resolution_service import (
    JurisdictionResolutionService,
    ResolutionResult,
)
from apps.extraction_core.services.review_routing_engine import (
    ReviewRoutingEngine,
    RoutingDecision,
)
from apps.extraction_core.services.schema_registry import SchemaRegistryService

logger = logging.getLogger(__name__)


class ExtractionPipeline:
    """
    Governed extraction pipeline orchestrator.

    All steps emit audit events and persist structured results into the
    new ExtractionRun + ExtractionFieldValue + ExtractionLineItem models.
    """

    @classmethod
    def run(
        cls,
        *,
        extraction_document_id: int,
        ocr_text: str,
        document_type: str = "INVOICE",
        vendor_id: int | None = None,
        enable_llm: bool = False,
        user=None,
        tenant=None,
    ) -> ExtractionRun:
        """
        Execute the full governed extraction pipeline.

        Parameters
        ----------
        extraction_document_id
            FK to DocumentUpload.
        ocr_text
            Raw OCR text to extract from.
        document_type
            Document type code (e.g. ``INVOICE``).
        vendor_id
            Vendor PK for entity-profile jurisdiction lookup.
        enable_llm
            Whether to enable LLM-based extraction for unresolved fields.
        user
            The requesting user (for audit/RBAC).

        Returns
        -------
        ExtractionRun
            Persisted extraction run with all related records.
        """
        started_at = timezone.now()

        # ── Create ExtractionRun record ──────────────────────────────
        run = ExtractionRun.objects.create(
            document_upload_id=extraction_document_id,
            status=ExtractionRunStatus.PENDING,
            started_at=started_at,
            created_by=user,
            tenant=tenant,
        )

        # ── Persist OCR text in separate table ──────────────────────
        ExtractionOCRText.objects.update_or_create(
            extraction_run=run,
            defaults={
                "ocr_text": ocr_text,
                "ocr_char_count": len(ocr_text),
                "ocr_page_count": ocr_text.count("\f") + 1 if ocr_text else 0,
            },
        )

        ExtractionAuditService.log_extraction_started(
            extraction_run_id=run.pk,
            document_id=extraction_document_id,
            user=user,
        )

        try:
            run.status = ExtractionRunStatus.JURISDICTION_RESOLVED
            run.save(update_fields=["status", "updated_at"])

            # ── Step 1: Resolve jurisdiction ─────────────────────────
            resolution = cls._resolve_jurisdiction(
                run, ocr_text, vendor_id, user,
            )
            if not resolution.resolved:
                return cls._fail_run(
                    run, "Jurisdiction could not be resolved", started_at,
                )

            # ── Step 2: Select schema ────────────────────────────────
            run.status = ExtractionRunStatus.SCHEMA_SELECTED
            run.save(update_fields=["status", "updated_at"])

            schema = cls._select_schema(run, resolution, document_type, user)
            if not schema:
                return cls._fail_run(
                    run,
                    f"No active schema for {resolution.country_code}/"
                    f"{resolution.regime_code}/{document_type}",
                    started_at,
                )

            # ── Step 3: Build prompt ─────────────────────────────────
            run.status = ExtractionRunStatus.PROMPT_BUILT
            run.save(update_fields=["status", "updated_at"])

            prompt_payload = cls._build_prompt(
                run, schema, resolution, document_type, user,
            )

            # ── Step 4: Run extraction ───────────────────────────────
            run.status = ExtractionRunStatus.EXTRACTING
            run.save(update_fields=["status", "updated_at"])

            from apps.extraction_core.services.extraction_service import (
                ExtractionService,
            )
            legacy_result = ExtractionService.extract(
                ocr_text=ocr_text,
                document_type=document_type,
                declared_country_code=resolution.country_code,
                declared_regime_code=resolution.regime_code or "",
                vendor_id=vendor_id,
                extraction_document_id=extraction_document_id,
                enable_llm=enable_llm,
            )

            # ── Step 5: Build output contract ────────────────────────
            output = cls._build_output_contract(
                run, legacy_result, resolution, schema, prompt_payload,
            )

            # ── Step 6: Enhanced normalization ───────────────────────
            run.status = ExtractionRunStatus.NORMALIZING
            run.save(update_fields=["status", "updated_at"])

            norm_start = timezone.now()
            norm_svc = EnhancedNormalizationService(
                country_code=resolution.country_code,
                regime_code=resolution.regime_code or "",
                jurisdiction_profile=resolution.jurisdiction,
            )
            norm_svc.normalize_output(output)
            norm_ms = int(
                (timezone.now() - norm_start).total_seconds() * 1000
            )

            ExtractionAuditService.log_normalization_completed(
                extraction_run_id=run.pk,
                country_code=run.country_code,
                regime_code=run.regime_code,
                user=user,
                duration_ms=norm_ms,
            )

            # ── Step 7: Enhanced validation ──────────────────────────
            run.status = ExtractionRunStatus.VALIDATING
            run.save(update_fields=["status", "updated_at"])

            val_start = timezone.now()
            val_svc = EnhancedValidationService(
                country_code=resolution.country_code,
                regime_code=resolution.regime_code or "",
                jurisdiction_profile=resolution.jurisdiction,
            )
            mandatory_keys = set(
                schema.get_all_field_keys()
            ) if hasattr(schema, "get_all_field_keys") else set()
            issues = val_svc.validate(
                output,
                extraction_run=run,
                mandatory_fields=mandatory_keys,
            )
            val_ms = int(
                (timezone.now() - val_start).total_seconds() * 1000
            )

            has_tax_issues = any(
                i.get("check_type") in ("TAX_CONSISTENCY", "TAX_ID_FORMAT")
                for i in issues
            )

            ExtractionAuditService.log_validation_completed(
                extraction_run_id=run.pk,
                country_code=run.country_code,
                regime_code=run.regime_code,
                user=user,
                duration_ms=val_ms,
                issue_count=len(issues),
            )

            # ── Step 8: Evidence capture ─────────────────────────────
            ev_start = timezone.now()
            evidence_records = EvidenceCaptureService.capture_from_output(
                extraction_run=run,
                output=output,
                ocr_text=ocr_text,
            )
            ev_ms = int(
                (timezone.now() - ev_start).total_seconds() * 1000
            )

            ExtractionAuditService.log_evidence_captured(
                extraction_run_id=run.pk,
                country_code=run.country_code,
                regime_code=run.regime_code,
                user=user,
                evidence_count=len(evidence_records),
                duration_ms=ev_ms,
            )

            # ── Step 9: Review routing ───────────────────────────────
            routing = ReviewRoutingEngine.evaluate(
                output=output,
                overall_confidence=output.overall_confidence,
                has_tax_issues=has_tax_issues,
                schema_missing=False,
            )

            output.requires_review = routing.needs_review
            output.review_reasons = routing.reasons

            # NOTE: Trace-level Langfuse scores (extraction_confidence,
            # extraction_requires_review) are emitted by the canonical
            # call site in apps/extraction/tasks.py.  Removed here to
            # avoid duplicate scores on a different trace_id.

            if routing.needs_review:
                ExtractionAuditService.log_review_route_assigned(
                    extraction_run_id=run.pk,
                    country_code=run.country_code,
                    regime_code=run.regime_code,
                    user=user,
                    queue=routing.queue,
                    priority=routing.priority,
                    reasons=routing.reasons,
                )

            # ── Step 10: Persist all data ────────────────────────────
            output.resolved = True
            cls._persist_run(run, output, routing, legacy_result, started_at)
            cls._persist_field_values(run, output)
            cls._persist_line_items(run, output)

            # ── Step 11: Completion audit ────────────────────────────
            ExtractionAuditService.log_extraction_completed(
                extraction_run_id=run.pk,
                country_code=run.country_code,
                regime_code=run.regime_code,
                schema_code=run.schema_code,
                schema_version=run.schema_version,
                user=user,
                duration_ms=run.duration_ms,
                overall_confidence=run.overall_confidence or 0.0,
            )

            return run

        except Exception as exc:
            logger.exception(
                "Extraction pipeline failed for run %s", run.pk,
            )
            ExtractionAuditService.log_extraction_failed(
                extraction_run_id=run.pk,
                user=user,
                error_message=str(exc),
            )
            return cls._fail_run(run, str(exc), started_at)

    # ------------------------------------------------------------------
    # Step 1 — Jurisdiction resolution (settings-only)
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_jurisdiction(
        cls,
        run: ExtractionRun,
        ocr_text: str,
        vendor_id: int | None,
        user,
    ) -> ResolutionResult:
        """
        Resolve jurisdiction from entity profile or runtime default ONLY.

        LLM/auto-detection is NOT used in the governed pipeline.
        """
        jur_start = timezone.now()

        resolution = JurisdictionResolutionService.resolve(
            ocr_text,
            vendor_id=vendor_id,
        )

        # Determine source type
        source_str = str(resolution.source) if resolution.source else ""
        if "ENTITY" in source_str.upper():
            jurisdiction_source = JurisdictionSource.ENTITY
        else:
            jurisdiction_source = JurisdictionSource.FIXED

        jur_ms = int(
            (timezone.now() - jur_start).total_seconds() * 1000
        )

        # Update run
        run.country_code = resolution.country_code
        run.regime_code = resolution.regime_code or ""
        run.jurisdiction_source = jurisdiction_source
        run.jurisdiction = resolution.jurisdiction
        run.jurisdiction_confidence = resolution.confidence
        run.save(update_fields=[
            "country_code", "regime_code", "jurisdiction_source",
            "jurisdiction", "jurisdiction_confidence", "updated_at",
        ])

        ExtractionAuditService.log_jurisdiction_resolved(
            extraction_run_id=run.pk,
            country_code=run.country_code,
            regime_code=run.regime_code,
            jurisdiction_source=jurisdiction_source,
            confidence=resolution.confidence,
            user=user,
            duration_ms=jur_ms,
        )

        return resolution

    # ------------------------------------------------------------------
    # Step 2 — Schema selection
    # ------------------------------------------------------------------

    @classmethod
    def _select_schema(
        cls,
        run: ExtractionRun,
        resolution: ResolutionResult,
        document_type: str,
        user,
    ) -> ExtractionSchemaDefinition | None:
        """Select schema using SchemaRegistryService."""
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

        if not (lookup.resolved and lookup.schema):
            return None

        schema = lookup.schema
        run.schema = schema
        run.schema_code = schema.name
        run.schema_version = schema.schema_version
        run.save(update_fields=[
            "schema", "schema_code", "schema_version", "updated_at",
        ])

        ExtractionAuditService.log_schema_selected(
            extraction_run_id=run.pk,
            schema_code=schema.name,
            schema_version=schema.schema_version,
            country_code=run.country_code,
            regime_code=run.regime_code,
            user=user,
        )

        return schema

    # ------------------------------------------------------------------
    # Step 3 — Prompt building
    # ------------------------------------------------------------------

    @classmethod
    def _build_prompt(
        cls,
        run: ExtractionRun,
        schema: ExtractionSchemaDefinition,
        resolution: ResolutionResult,
        document_type: str,
        user,
    ) -> dict:
        """Build prompt via PromptBuilderService."""
        prompt_payload = PromptBuilderService.build(
            country_code=resolution.country_code,
            regime_code=resolution.regime_code or "",
            document_type=document_type,
            schema=schema,
            jurisdiction_profile=resolution.jurisdiction,
        )

        run.prompt_code = prompt_payload.get("prompt_code", "")
        run.prompt_version = prompt_payload.get("prompt_version", "")
        run.save(update_fields=[
            "prompt_code", "prompt_version", "updated_at",
        ])

        ExtractionAuditService.log_prompt_selected(
            extraction_run_id=run.pk,
            prompt_code=run.prompt_code,
            prompt_version=run.prompt_version,
            country_code=run.country_code,
            regime_code=run.regime_code,
            user=user,
        )

        return prompt_payload

    # ------------------------------------------------------------------
    # Step 5 — Build output contract from legacy result
    # ------------------------------------------------------------------

    @classmethod
    def _build_output_contract(
        cls,
        run: ExtractionRun,
        legacy_result,
        resolution: ResolutionResult,
        schema: ExtractionSchemaDefinition,
        prompt_payload: dict,
    ) -> ExtractionOutputContract:
        """
        Convert the legacy ExtractionResult into the standard
        ExtractionOutputContract.
        """
        output = ExtractionOutputContract()

        # Meta
        output.meta = MetaBlock(
            extraction_run_id=run.pk,
            document_id=run.document_upload_id,
            document_type=schema.document_type,
            extraction_method=legacy_result.extraction_method,
            schema_code=schema.name,
            schema_version=schema.schema_version,
            prompt_code=prompt_payload.get("prompt_code", ""),
            prompt_version=prompt_payload.get("prompt_version", ""),
            country_code=resolution.country_code,
            regime_code=resolution.regime_code or "",
            jurisdiction_source=run.jurisdiction_source,
            timestamp=timezone.now().isoformat(),
            duration_ms=legacy_result.duration_ms,
        )

        # Header + tax fields
        for key, fr in legacy_result.header_fields.items():
            output.header[key] = FieldValue(
                value=fr.normalized_value or fr.raw_value,
                confidence=fr.confidence,
                evidence=fr.source_snippet,
                extraction_method=fr.method,
                page_number=(
                    fr.evidence.page_number
                    if fr.evidence
                    else None
                ),
            )

        # Tax fields
        for key, fr in legacy_result.tax_fields.items():
            output.tax.tax_fields[key] = FieldValue(
                value=fr.normalized_value or fr.raw_value,
                confidence=fr.confidence,
                evidence=fr.source_snippet,
                extraction_method=fr.method,
                page_number=(
                    fr.evidence.page_number
                    if fr.evidence
                    else None
                ),
            )

        # Line items
        for idx, line_dict in enumerate(legacy_result.line_items):
            li = LineItemRow(index=idx)
            confs = []
            for key, fr in line_dict.items():
                li.fields[key] = FieldValue(
                    value=fr.normalized_value or fr.raw_value,
                    confidence=fr.confidence,
                    evidence=fr.source_snippet,
                    extraction_method=fr.method,
                )
                if fr.confidence:
                    confs.append(fr.confidence)
            if confs:
                li.confidence = sum(confs) / len(confs)
            output.line_items.append(li)

        # Confidence
        output.overall_confidence = legacy_result.overall_confidence

        # Warnings
        for w in legacy_result.warnings:
            output.warnings.append(
                WarningItem(message=w, severity="WARNING")
            )

        # Backward-compatible QR payload for extraction console.
        qr_obj = getattr(legacy_result, "qr_data", None)
        if qr_obj is not None:
            try:
                output.qr_data = qr_obj.to_serializable()
            except Exception:
                output.qr_data = {}

        # Errors
        output.errors = list(legacy_result.errors)

        return output

    # ------------------------------------------------------------------
    # Step 10 — Persistence
    # ------------------------------------------------------------------

    @classmethod
    def _persist_run(
        cls,
        run: ExtractionRun,
        output: ExtractionOutputContract,
        routing: RoutingDecision,
        legacy_result,
        started_at,
    ) -> None:
        """Persist final extraction run state."""
        completed_at = timezone.now()
        duration_ms = int(
            (completed_at - started_at).total_seconds() * 1000
        )

        run.status = ExtractionRunStatus.COMPLETED
        run.overall_confidence = output.overall_confidence
        run.extraction_method = output.meta.extraction_method
        run.extracted_data_json = output.to_dict()
        run.review_queue = routing.queue
        run.requires_review = routing.needs_review
        run.review_reasons_json = routing.reasons
        run.completed_at = completed_at
        run.duration_ms = duration_ms
        run.field_count = len(output.get_all_field_codes())

        # Confidence breakdown from legacy result
        if hasattr(legacy_result, "confidence"):
            run.header_confidence = legacy_result.confidence.header
            run.tax_confidence = legacy_result.confidence.tax
            run.line_item_confidence = legacy_result.confidence.line_item

        # Coverage
        if hasattr(legacy_result, "field_coverage_pct"):
            run.field_coverage_pct = legacy_result.field_coverage_pct
        if hasattr(legacy_result, "mandatory_coverage_pct"):
            run.mandatory_coverage_pct = legacy_result.mandatory_coverage_pct

        run.save()

    @classmethod
    def _persist_field_values(
        cls,
        run: ExtractionRun,
        output: ExtractionOutputContract,
    ) -> None:
        """Bulk-create ExtractionFieldValue records."""
        records = []

        # Header fields
        for field_code, fv in output.header.items():
            records.append(
                ExtractionFieldValue(
                    extraction_run=run,
                    field_code=field_code,
                    value=str(fv.value) if fv.value is not None else "",
                    confidence=fv.confidence,
                    extraction_method=fv.extraction_method or "DETERMINISTIC",
                    category="HEADER",
                )
            )

        # Tax fields
        for field_code, fv in output.tax.tax_fields.items():
            records.append(
                ExtractionFieldValue(
                    extraction_run=run,
                    field_code=field_code,
                    value=str(fv.value) if fv.value is not None else "",
                    confidence=fv.confidence,
                    extraction_method=fv.extraction_method or "DETERMINISTIC",
                    category="TAX",
                )
            )

        # Reference fields
        for field_code, fv in output.references.items():
            records.append(
                ExtractionFieldValue(
                    extraction_run=run,
                    field_code=field_code,
                    value=str(fv.value) if fv.value is not None else "",
                    confidence=fv.confidence,
                    extraction_method=fv.extraction_method or "DETERMINISTIC",
                    category="HEADER",
                )
            )

        # Line-item fields
        for li in output.line_items:
            for field_code, fv in li.fields.items():
                records.append(
                    ExtractionFieldValue(
                        extraction_run=run,
                        field_code=field_code,
                        value=str(fv.value) if fv.value is not None else "",
                        confidence=fv.confidence,
                        extraction_method=(
                            fv.extraction_method or "DETERMINISTIC"
                        ),
                        category="LINE_ITEM",
                        line_item_index=li.index,
                    )
                )

        if records:
            ExtractionFieldValue.objects.bulk_create(records)

    @classmethod
    def _persist_line_items(
        cls,
        run: ExtractionRun,
        output: ExtractionOutputContract,
    ) -> None:
        """Bulk-create ExtractionLineItem records."""
        records = []
        for li in output.line_items:
            data_json = {
                k: str(v.value) if v.value is not None else ""
                for k, v in li.fields.items()
            }
            records.append(
                ExtractionLineItem(
                    extraction_run=run,
                    line_index=li.index,
                    data_json=data_json,
                    confidence=li.confidence,
                    page_number=li.page_number,
                )
            )
        if records:
            ExtractionLineItem.objects.bulk_create(records)

    # ------------------------------------------------------------------
    # Failure helper
    # ------------------------------------------------------------------

    @classmethod
    def _fail_run(
        cls,
        run: ExtractionRun,
        error_message: str,
        started_at,
    ) -> ExtractionRun:
        """Mark an extraction run as failed."""
        completed_at = timezone.now()
        run.status = ExtractionRunStatus.FAILED
        run.error_message = error_message
        run.completed_at = completed_at
        run.duration_ms = int(
            (completed_at - started_at).total_seconds() * 1000
        )
        run.save()
        return run
