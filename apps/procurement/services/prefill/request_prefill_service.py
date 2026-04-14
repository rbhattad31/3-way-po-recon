"""Request document prefill pipeline (agent-first)."""
from __future__ import annotations

import logging
from typing import Any

from apps.core.decorators import observed_service
from apps.core.enums import AgentType
from apps.documents.blob_service import document_upload_temp_path
from apps.procurement.agents.Azure_Document_Intelligence_Extractor_Agent import extract_document
from apps.procurement.agents.Procurement_Form_Filling_Agent import ProcurementFormFillingAgent
from apps.procurement.services.agent_run_tracking import run_procurement_component_with_tracking
from apps.procurement.services.prefill.attribute_mapping_service import AttributeMappingService
from apps.procurement.services.prefill.prefill_status_service import PrefillStatusService

logger = logging.getLogger(__name__)


class RequestDocumentPrefillService:
    @staticmethod
    @observed_service("procurement.request_prefill")
    def run_prefill(request, tenant=None) -> dict[str, Any]:
        if not request.uploaded_document or not request.uploaded_document.file:
            raise ValueError("No source document attached to the request")

        PrefillStatusService.mark_request_in_progress(request)

        try:
            with document_upload_temp_path(request.uploaded_document) as file_path:
                raw_extraction = run_procurement_component_with_tracking(
                    agent_type=AgentType.PROCUREMENT_AZURE_DI_EXTRACTION,
                    invocation_reason="AzureDIExtractorAgent.extract",
                    tenant=getattr(request, "tenant", None),
                    actor_user=getattr(request, "created_by", None),
                    input_payload={
                        "source": "request_prefill_service",
                        "procurement_request_id": str(getattr(request, "request_id", "")),
                        "procurement_request_pk": getattr(request, "pk", None),
                        "source_document_type": getattr(request, "source_document_type", "") or "",
                    },
                    execute_fn=lambda: extract_document(file_path=file_path, doc_type_hint="hvac_request_form"),
                )

            filled = run_procurement_component_with_tracking(
                agent_type=AgentType.PROCUREMENT_FORM_FILLING,
                invocation_reason="ProcurementFormFillingAgent.fill_form",
                tenant=getattr(request, "tenant", None),
                actor_user=getattr(request, "created_by", None),
                input_payload={"source": "request_prefill_service", "procurement_request_pk": getattr(request, "pk", None)},
                execute_fn=lambda: ProcurementFormFillingAgent.fill_form(
                    extraction_output=raw_extraction or {},
                    source_doc_type=getattr(request, "source_document_type", "") or "",
                ),
            )

            mapped = AttributeMappingService.map_request_fields(filled)
            confidence_breakdown = AttributeMappingService.classify_confidence(mapped.get("core_fields", {}))
            overall_confidence = float((filled or {}).get("confidence", (raw_extraction or {}).get("confidence", 0.5)))

            payload = {
                "success": True,
                "core_fields": mapped.get("core_fields", {}),
                "attributes": mapped.get("attributes", []),
                "unmapped": mapped.get("unmapped", []),
                "confidence_breakdown": confidence_breakdown,
                "overall_confidence": overall_confidence,
                "field_count": len(mapped.get("core_fields", {})) + len(mapped.get("attributes", [])),
                "form_fill_agent_used": True,
            }
            PrefillStatusService.mark_request_completed(request, confidence=overall_confidence, payload=payload)
            return payload
        except Exception as exc:
            logger.exception("Request prefill failed for request=%s", getattr(request, "pk", None))
            PrefillStatusService.mark_request_failed(request, str(exc))
            return {"success": False, "error": str(exc)}
