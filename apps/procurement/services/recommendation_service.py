"""RecommendationService — orchestrates the product/solution recommendation flow."""
from __future__ import annotations

import logging
from typing import Any, Dict

from django.db import transaction

from apps.auditlog.services import AuditService
from apps.core.decorators import observed_service
from apps.core.enums import (
    AnalysisRunStatus,
    AnalysisRunType,
    ComplianceStatus,
    ProcurementRequestStatus,
)
from apps.core.trace import TraceContext
from apps.procurement.models import (
    AnalysisRun,
    ComplianceResult,
    ProcurementRequest,
    RecommendationResult,
)
from apps.procurement.services.analysis_run_service import AnalysisRunService
from apps.procurement.services.request_service import (
    AttributeService,
    ProcurementRequestService,
)

logger = logging.getLogger(__name__)


class RecommendationService:
    """Orchestrates the recommendation analysis flow.

    Steps:
      1. Validate attributes
      2. Apply rule-based logic
      3. Invoke AI (only if deterministic rules are insufficient)
      4. Validate compliance
      5. Persist RecommendationResult
      6. Update request status
    """

    @staticmethod
    @observed_service("procurement.recommendation.run", audit_event="RECOMMENDATION_RUN_STARTED")
    def run_recommendation(
        request: ProcurementRequest,
        run: AnalysisRun,
        *,
        use_ai: bool = True,
        tenant=None,
    ) -> RecommendationResult:
        AnalysisRunService.start_run(run)

        try:
            # Step 1: Gather attributes
            attrs = AttributeService.get_attributes_dict(request)

            # Step 2: Apply deterministic rules
            rule_result = RecommendationService._apply_rules(request, attrs)

            # Step 3: Invoke AI workflow if rules are inconclusive
            ai_result = None
            if use_ai and not rule_result.get("confident"):
                from apps.procurement.services.recommendation_graph_service import RecommendationGraphService
                ai_result = RecommendationGraphService.run(
                    request=request,
                    run=run,
                    attributes=attrs,
                    rule_result=rule_result,
                )

            # Merge results
            final = RecommendationService._merge_recommendation_result(rule_result, ai_result)

            # Step 4: Compliance check
            compliance_status = ComplianceStatus.NOT_CHECKED
            compliance_data = None
            if final.get("recommended_option"):
                from apps.procurement.services.compliance_service import ComplianceService
                compliance_data = ComplianceService.check_recommendation(request, final)
                compliance_status = compliance_data.get("status", ComplianceStatus.NOT_CHECKED)

            # Step 5: Persist result
            with transaction.atomic():
                result = RecommendationResult.objects.create(
                    run=run,
                    recommended_option=final.get("recommended_option", "No recommendation"),
                    reasoning_summary=final.get("reasoning_summary", ""),
                    reasoning_details_json=final.get("reasoning_details"),
                    confidence_score=RecommendationService._normalize_confidence(final.get("confidence", 0.0)),
                    constraints_json=final.get("constraints"),
                    compliance_status=compliance_status,
                    output_payload_json=final,
                    tenant=tenant,
                )

                if compliance_data:
                    ComplianceResult.objects.create(
                        run=run,
                        compliance_status=compliance_status,
                        rules_checked_json=compliance_data.get("rules_checked"),
                        violations_json=compliance_data.get("violations"),
                        recommendations_json=compliance_data.get("recommendations"),
                        tenant=tenant,
                    )

            # Step 6: Finalize run and update request status
            AnalysisRunService.complete_run(
                run,
                output_summary=result.recommended_option,
                confidence_score=result.confidence_score,
            )
            new_status = (
                ProcurementRequestStatus.COMPLETED
                if compliance_status != ComplianceStatus.FAIL
                else ProcurementRequestStatus.REVIEW_REQUIRED
            )
            ProcurementRequestService.update_status(request, new_status, user=run.triggered_by)

            return result

        except Exception as exc:
            AnalysisRunService.fail_run(run, str(exc))
            ProcurementRequestService.update_status(
                request, ProcurementRequestStatus.FAILED, user=run.triggered_by,
            )
            raise

    @staticmethod
    def _apply_rules(
        request: ProcurementRequest,
        attrs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply deterministic rule-based recommendation logic.

        Override or extend this method per domain. Returns a dict with:
        - recommended_option (str)
        - reasoning_summary (str)
        - confident (bool) — True if rules alone can decide
        - constraints (list)
        """
        # Placeholder: no deterministic rules yet — defer to AI
        return {
            "recommended_option": "",
            "reasoning_summary": "No deterministic rules matched. Deferring to AI analysis.",
            "confident": False,
            "confidence": 0.0,
            "constraints": [],
            "reasoning_details": {"source": "rules_engine", "rules_evaluated": 0},
        }

    @staticmethod
    def _merge_recommendation_result(
        rule_result: Dict[str, Any],
        ai_result: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        """Prefer AI output whenever it produced a recommendation or meaningful reasoning."""
        if not ai_result:
            return rule_result

        merged = {
            **rule_result,
            **ai_result,
            "reasoning_details": {
                **(rule_result.get("reasoning_details") or {}),
                **(ai_result.get("reasoning_details") or {}),
            },
        }

        if ai_result.get("recommended_option") or ai_result.get("reasoning_summary"):
            return merged
        return rule_result

    @staticmethod
    def _normalize_confidence(value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, confidence))
