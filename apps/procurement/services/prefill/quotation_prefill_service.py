"""Quotation prefill pipeline (agent-first)."""
from __future__ import annotations

import logging
from typing import Any

from apps.core.decorators import observed_service
from apps.core.enums import AgentType
from apps.documents.blob_service import document_upload_temp_path
from apps.procurement.agents.Azure_Document_Intelligence_Extractor_Agent import extract_document
from apps.procurement.services.agent_run_tracking import run_procurement_component_with_tracking
from apps.procurement.services.prefill.attribute_mapping_service import AttributeMappingService
from apps.procurement.services.prefill.prefill_status_service import PrefillStatusService

logger = logging.getLogger(__name__)


class QuotationDocumentPrefillService:
    @staticmethod
    @observed_service("procurement.quotation_prefill")
    def run_prefill(quotation, tenant=None) -> dict[str, Any]:
        if not quotation.uploaded_document or not quotation.uploaded_document.file:
            raise ValueError("No source document attached to the quotation")

        PrefillStatusService.mark_quotation_in_progress(quotation)

        try:
            with document_upload_temp_path(quotation.uploaded_document) as file_path:
                raw_extraction = run_procurement_component_with_tracking(
                    agent_type=AgentType.PROCUREMENT_AZURE_DI_EXTRACTION,
                    invocation_reason="AzureDIExtractorAgent.extract",
                    tenant=getattr(quotation, "tenant", None),
                    actor_user=getattr(getattr(quotation, "request", None), "created_by", None),
                    input_payload={"source": "quotation_prefill_service", "quotation_pk": getattr(quotation, "pk", None)},
                    execute_fn=lambda: extract_document(file_path=file_path, doc_type_hint="quotation"),
                )

            mapped = AttributeMappingService.map_quotation_fields(raw_extraction or {})
            confidence_breakdown = AttributeMappingService.classify_confidence(mapped.get("header_fields", {}))
            overall_confidence = float((raw_extraction or {}).get("confidence", 0.5))

            payload = {
                "success": True,
                "header_fields": mapped.get("header_fields", {}),
                "commercial_terms": mapped.get("commercial_terms", []),
                "line_items": mapped.get("line_items", []),
                "unmapped": mapped.get("unmapped", []),
                "confidence_breakdown": confidence_breakdown,
                "overall_confidence": overall_confidence,
            }
            PrefillStatusService.mark_quotation_completed(quotation, confidence=overall_confidence, payload=payload)
            return payload
        except Exception as exc:
            logger.exception("Quotation prefill failed for quotation=%s", getattr(quotation, "pk", None))
            PrefillStatusService.mark_quotation_failed(quotation, str(exc))
            return {"success": False, "error": str(exc)}
