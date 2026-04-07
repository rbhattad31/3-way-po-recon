"""LangGraph-driven orchestration for procurement recommendations."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, TypedDict

from langgraph.graph import END, START, StateGraph

from apps.core.decorators import observed_service
from apps.core.enums import PrefillStatus
from apps.procurement.agents.recommendation_agent import RecommendationAgent
from apps.procurement.models import AnalysisRun, ProcurementRequest, SupplierQuotation, ValidationResult
from apps.procurement.services.prefill.quotation_prefill_service import QuotationDocumentPrefillService

logger = logging.getLogger(__name__)


class RecommendationGraphState(TypedDict, total=False):
    request: ProcurementRequest
    run: AnalysisRun
    attributes: Dict[str, Any]
    rule_result: Dict[str, Any]
    archetype: Dict[str, Any]
    upstream_validation_context: Dict[str, Any]
    request_context: Dict[str, Any]
    validation_context: Dict[str, Any]
    needs_quotation_extraction: bool
    quotation_context: List[Dict[str, Any]]
    web_context: Dict[str, Any]
    ai_payload: Dict[str, Any]
    ai_result: Dict[str, Any]


class RecommendationGraphService:
    """End-to-end recommendation workflow using a LangGraph state machine."""

    _compiled_graph = None

    @classmethod
    @observed_service("procurement.recommendation.graph")
    def run(
        cls,
        *,
        request: ProcurementRequest,
        run: AnalysisRun,
        attributes: Dict[str, Any],
        rule_result: Dict[str, Any],
        archetype: Dict[str, Any] | None = None,
        validation_context: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        state: RecommendationGraphState = {
            "request": request,
            "run": run,
            "attributes": attributes,
            "rule_result": rule_result,
            "archetype": archetype or {},
            "upstream_validation_context": validation_context or {},
        }
        final_state = cls._get_graph().invoke(state)
        return final_state.get("ai_result") or {}

    @classmethod
    def _get_graph(cls):
        if cls._compiled_graph is None:
            graph = StateGraph(RecommendationGraphState)
            graph.add_node("collect_request_context", cls._collect_request_context)
            graph.add_node("collect_validation_context", cls._collect_validation_context)
            graph.add_node("check_quotation_context", cls._check_quotation_context)
            graph.add_node("extract_quotation_context", cls._extract_quotation_context)
            graph.add_node("collect_quotation_context", cls._collect_quotation_context)
            graph.add_node("fetch_web_market_data", cls._fetch_web_market_data)
            graph.add_node("assemble_ai_payload", cls._assemble_ai_payload)
            graph.add_node("call_recommendation_agent", cls._call_recommendation_agent)

            graph.add_edge(START, "collect_request_context")
            graph.add_edge("collect_request_context", "collect_validation_context")
            graph.add_edge("collect_validation_context", "check_quotation_context")
            graph.add_conditional_edges(
                "check_quotation_context",
                cls._route_quotation_step,
                {
                    "extract_quotation_context": "extract_quotation_context",
                    "collect_quotation_context": "collect_quotation_context",
                },
            )
            graph.add_edge("extract_quotation_context", "collect_quotation_context")
            graph.add_edge("collect_quotation_context", "fetch_web_market_data")
            graph.add_edge("fetch_web_market_data", "assemble_ai_payload")
            graph.add_edge("assemble_ai_payload", "call_recommendation_agent")
            graph.add_edge("call_recommendation_agent", END)
            cls._compiled_graph = graph.compile()
        return cls._compiled_graph

    @staticmethod
    def _collect_request_context(state: RecommendationGraphState) -> Dict[str, Any]:
        request = state["request"]
        attrs = state.get("attributes") or {}
        return {
            "request_context": {
                "request_id": str(request.request_id),
                "title": request.title,
                "description": request.description,
                "domain_code": request.domain_code,
                "schema_code": request.schema_code,
                "request_type": request.request_type,
                "status": request.status,
                "priority": request.priority,
                "currency": request.currency,
                "geography_country": request.geography_country,
                "geography_city": request.geography_city,
                "trace_id": request.trace_id,
                "attribute_count": len(attrs),
                "request_prefill_status": request.prefill_status,
                "request_prefill_payload": request.prefill_payload_json or {},
            },
        }

    @staticmethod
    def _collect_validation_context(state: RecommendationGraphState) -> Dict[str, Any]:
        request = state["request"]
        validation_result = (
            ValidationResult.objects
            .filter(run__request=request)
            .select_related("run")
            .order_by("-created_at")
            .first()
        )
        if not validation_result:
            return {"validation_context": {}}

        return {
            "validation_context": {
                "run_id": str(validation_result.run.run_id),
                "overall_status": validation_result.overall_status,
                "completeness_score": validation_result.completeness_score,
                "summary_text": validation_result.summary_text,
                "readiness_for_recommendation": validation_result.readiness_for_recommendation,
                "readiness_for_benchmarking": validation_result.readiness_for_benchmarking,
                "recommended_next_action": validation_result.recommended_next_action,
                "missing_items": validation_result.missing_items_json or [],
                "warnings": validation_result.warnings_json or [],
                "ambiguous_items": validation_result.ambiguous_items_json or [],
            },
        }

    @staticmethod
    def _check_quotation_context(state: RecommendationGraphState) -> Dict[str, Any]:
        request = state["request"]
        quotations = list(request.quotations.select_related("uploaded_document").prefetch_related("line_items"))
        needs_extraction = any(RecommendationGraphService._quotation_needs_extraction(quotation) for quotation in quotations)
        return {"needs_quotation_extraction": needs_extraction}

    @staticmethod
    def _route_quotation_step(state: RecommendationGraphState) -> str:
        return "extract_quotation_context" if state.get("needs_quotation_extraction") else "collect_quotation_context"

    @staticmethod
    def _extract_quotation_context(state: RecommendationGraphState) -> Dict[str, Any]:
        request = state["request"]
        for quotation in request.quotations.select_related("uploaded_document"):
            if not RecommendationGraphService._quotation_needs_extraction(quotation):
                continue
            payload = QuotationDocumentPrefillService.run_prefill(quotation)
            if not payload.get("success"):
                logger.warning(
                    "Quotation prefill failed during recommendation flow for quotation %s: %s",
                    quotation.pk,
                    payload.get("error"),
                )
        return {}

    @staticmethod
    def _collect_quotation_context(state: RecommendationGraphState) -> Dict[str, Any]:
        request = state["request"]
        quotation_context = []
        quotations = request.quotations.select_related("uploaded_document").prefetch_related("line_items").order_by("created_at")
        for quotation in quotations:
            quotation_context.append(RecommendationGraphService._serialize_quotation(quotation))
        return {"quotation_context": quotation_context}

    @staticmethod
    def _fetch_web_market_data(state: RecommendationGraphState) -> Dict[str, Any]:
        """Fetch live market data from web search for the recommended system type.

        Runs regardless of whether the rules engine was confident or not.
        Gives the AI real-world product specs, brand options, and indicative
        pricing instead of relying only on static catalogue constants.
        """
        try:
            from apps.procurement.services.web_search_service import WebSearchService

            attrs = state.get("attributes") or {}
            rule_result = state.get("rule_result") or {}
            request = state["request"]

            # Determine what to search for
            system_type_code = rule_result.get("system_type_code") or ""
            if not system_type_code:
                # Try to infer from rule result text
                rec_text = (rule_result.get("recommended_option") or "").upper()
                for code in ("FCU_CHILLED_WATER", "VRF_SYSTEM", "SPLIT_SYSTEM",
                             "PACKAGED_DX_UNIT", "CHILLER_PLANT", "CASSETTE_SPLIT"):
                    if code.replace("_", " ") in rec_text or code in rec_text:
                        system_type_code = code
                        break
                if not system_type_code:
                    # Derive from key attributes
                    store_type = str(attrs.get("store_type") or "").upper()
                    cw = str(attrs.get("chilled_water_available") or "NO").upper()
                    zones = int(attrs.get("zone_count") or 1)
                    if cw == "YES":
                        system_type_code = "FCU_CHILLED_WATER"
                    elif zones >= 3:
                        system_type_code = "VRF_SYSTEM"
                    else:
                        system_type_code = "SPLIT_SYSTEM"

            geography = (
                getattr(request, "geography_country", "")
                or str(getattr(request, "location", "") or "")
                or "UAE"
            )
            currency = getattr(request, "currency", "AED") or "AED"
            capacity_tr = None
            try:
                area = float(attrs.get("area_sqm") or 0)
                if area:
                    capacity_tr = round(area * 130 / 3517, 1)  # GCC rule of thumb
            except (TypeError, ValueError):
                pass

            # Also search for the store type context
            store_type_kw = str(attrs.get("store_type") or "").replace("_", " ").lower()
            extra = f"retail {store_type_kw} GCC" if store_type_kw else ""

            web_result = WebSearchService.search_product_info(
                system_type=system_type_code,
                capacity_tr=capacity_tr,
                geography=geography,
                currency=currency,
                extra_keywords=extra,
            )

            logger.info(
                "RecommendationGraphService: web search for system_type=%s, geography=%s -> "
                "%d snippets, pricing=%s",
                system_type_code,
                geography,
                len(web_result.get("snippets") or []),
                web_result.get("pricing"),
            )
            return {"web_context": web_result}

        except Exception as exc:
            logger.warning(
                "RecommendationGraphService._fetch_web_market_data failed (non-blocking): %s", exc
            )
            return {"web_context": {"snippets": [], "pricing": {}, "source": "WEB_SEARCH", "notes": f"Web search unavailable: {exc}"}}

    @staticmethod
    def _assemble_ai_payload(state: RecommendationGraphState) -> Dict[str, Any]:
        # Merge upstream (step-1 report) with any DB-sourced validation context.
        # The upstream report from _validate_and_normalize is preferred when present.
        upstream = state.get("upstream_validation_context") or {}
        db_validation = state.get("validation_context") or {}
        merged_validation = {**db_validation, **upstream} if upstream else db_validation

        payload = {
            "request": state.get("request_context") or {},
            "attributes": state.get("attributes") or {},
            "rule_result": state.get("rule_result") or {},
            "archetype": state.get("archetype") or {},
            "validation_context": merged_validation,
            "quotation_context": state.get("quotation_context") or [],
            "web_market_context": state.get("web_context") or {},
            "analysis_run": {
                "run_id": str(state["run"].run_id),
                "run_type": state["run"].run_type,
            },
        }
        return {"ai_payload": payload}

    @staticmethod
    def _call_recommendation_agent(state: RecommendationGraphState) -> Dict[str, Any]:
        ai_payload = state.get("ai_payload") or {}
        return {"ai_result": RecommendationAgent.execute_from_payload(ai_payload)}

    @staticmethod
    def _quotation_needs_extraction(quotation: SupplierQuotation) -> bool:
        if not quotation.uploaded_document_id:
            return False
        if quotation.line_items.exists():
            return False
        if quotation.prefill_payload_json:
            return False
        return quotation.prefill_status not in (PrefillStatus.COMPLETED, PrefillStatus.REVIEW_PENDING)

    @staticmethod
    def _serialize_quotation(quotation: SupplierQuotation) -> Dict[str, Any]:
        payload = quotation.prefill_payload_json or {}
        header_fields = payload.get("header_fields") or {}
        payload_line_items = payload.get("line_items") or []
        db_line_items = list(quotation.line_items.all())

        if db_line_items:
            line_items = [
                {
                    "line_number": item.line_number,
                    "description": item.description,
                    "normalized_description": item.normalized_description,
                    "category_code": item.category_code,
                    "quantity": float(item.quantity),
                    "unit": item.unit,
                    "unit_rate": float(item.unit_rate),
                    "total_amount": float(item.total_amount),
                    "brand": item.brand,
                    "model": item.model,
                    "confidence": item.extraction_confidence,
                    "source": item.extraction_source,
                }
                for item in db_line_items[:50]
            ]
        else:
            line_items = payload_line_items[:50]

        return {
            "quotation_id": quotation.pk,
            "vendor_name": quotation.vendor_name or RecommendationGraphService._payload_value(header_fields, "vendor_name"),
            "quotation_number": quotation.quotation_number or RecommendationGraphService._payload_value(header_fields, "quotation_number"),
            "quotation_date": str(quotation.quotation_date or RecommendationGraphService._payload_value(header_fields, "quotation_date") or ""),
            "currency": quotation.currency or RecommendationGraphService._payload_value(header_fields, "currency") or quotation.request.currency,
            "total_amount": RecommendationGraphService._safe_float(
                quotation.total_amount if quotation.total_amount is not None else RecommendationGraphService._payload_value(header_fields, "total_amount")
            ),
            "prefill_status": quotation.prefill_status,
            "extraction_status": quotation.extraction_status,
            "commercial_terms": payload.get("commercial_terms") or [],
            "line_items": line_items,
            "has_confirmed_line_items": bool(db_line_items),
            "uses_extracted_payload": bool(payload and not db_line_items),
        }

    @staticmethod
    def _payload_value(header_fields: Dict[str, Any], key: str) -> Any:
        value = header_fields.get(key)
        if isinstance(value, dict):
            return value.get("value")
        return value

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None