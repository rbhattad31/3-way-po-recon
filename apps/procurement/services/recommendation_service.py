"""RecommendationService -- agent-first procurement recommendation runner."""
from __future__ import annotations

import logging
from typing import Any

from apps.agents.services.base_agent import BaseAgent
from apps.core.enums import AgentType, AnalysisRunStatus, ComplianceStatus, ProcurementRequestStatus
from apps.procurement.hvac.rules_engine import HVACRulesEngine
from apps.procurement.services.agent_run_tracking import run_procurement_component_with_tracking
from apps.procurement.models import ComplianceResult, RecommendationResult
from apps.procurement.services.analysis_run_service import AnalysisRunService
from apps.procurement.services.recommendation_graph_service import RecommendationGraphService
from apps.procurement.services.request_service import AttributeService, ProcurementRequestService

logger = logging.getLogger(__name__)


class RecommendationService:
    """Execute recommendation using deterministic rules + AI agents."""

    @staticmethod
    def _merge_compliance_outputs(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
        """Merge rule-based and AI-augmented compliance findings."""
        merged_rules = list(primary.get("rules_checked") or [])
        merged_rules.extend(secondary.get("rules_checked") or [])

        merged_violations = list(primary.get("violations") or [])
        merged_violations.extend(secondary.get("violations") or [])

        merged_recommendations: list[str] = []
        for item in list(primary.get("recommendations") or []) + list(secondary.get("recommendations") or []):
            value = BaseAgent._sanitise_text(str(item or "").strip())
            if value and value not in merged_recommendations:
                merged_recommendations.append(value)

        statuses = {
            str(primary.get("status") or ComplianceStatus.NOT_CHECKED),
            str(secondary.get("status") or ComplianceStatus.NOT_CHECKED),
        }
        if ComplianceStatus.FAIL in statuses:
            merged_status = ComplianceStatus.FAIL
        elif ComplianceStatus.PARTIAL in statuses:
            merged_status = ComplianceStatus.PARTIAL
        elif ComplianceStatus.PASS in statuses:
            merged_status = ComplianceStatus.PASS
        else:
            merged_status = ComplianceStatus.NOT_CHECKED

        return {
            "status": merged_status,
            "rules_checked": merged_rules,
            "violations": merged_violations,
            "recommendations": merged_recommendations,
            "hvac_alignment": primary.get("hvac_alignment") or secondary.get("hvac_alignment") or "",
            "domain_flags": list(secondary.get("domain_flags") or []),
            "geography_flags": list(secondary.get("geography_flags") or []),
            "ai_augmented": bool(secondary.get("ai_augmented")),
        }

    @staticmethod
    def run_recommendation(request, run, *, use_ai: bool = True, request_user: Any = None):
        # Lifecycle start
        if run.status in {AnalysisRunStatus.QUEUED, "QUEUED"}:
            AnalysisRunService.start_run(run)

        attrs = AttributeService.get_attributes_dict(request)
        rule_result = HVACRulesEngine.evaluate(
            domain_code=request.domain_code or "HVAC",
            attrs=attrs,
            geography_country=request.geography_country or "",
        )

        ai_result = {}
        if use_ai:
            try:
                ai_result = RecommendationGraphService.run(
                    request=request,
                    run=run,
                    attributes=attrs,
                    rule_result=rule_result,
                    archetype={},
                    validation_context={},
                ) or {}
            except Exception:
                logger.exception("RecommendationGraphService.run failed; falling back to deterministic rule result")
                ai_result = {}

        final_payload = dict(rule_result or {})
        if isinstance(ai_result, dict):
            # Merge all top-level keys from ai_result, but handle reasoning_details
            # separately so the DB rule engine provenance (rule_matched, rules_loaded,
            # source, db_rule, rule_conditions, inputs) is never overwritten.
            overrides = {
                k: v
                for k, v in ai_result.items()
                if k != "reasoning_details" and v not in (None, "")
            }
            final_payload.update(overrides)

            # Smart-merge reasoning_details: DB rule fields take priority; AI explain
            # output is nested under the "ai_explain" key so downstream consumers
            # (ReasonSummaryAgent, workspace template) can read both sources.
            rule_rd: dict = dict(final_payload.get("reasoning_details") or {})
            ai_rd: dict = ai_result.get("reasoning_details") or {}
            if ai_rd:
                rule_rd["ai_explain"] = ai_rd
                # Surface ai_reasoning_used at the top level for direct reads
                rule_rd.setdefault("ai_reasoning_used", ai_rd.get("ai_reasoning_used", True))
                # Preserve DB source; do not let agent explain clobber it
                rule_rd.setdefault("source", "db_rules")
            final_payload["reasoning_details"] = rule_rd

        recommended_option = str(
            final_payload.get("recommended_option")
            or final_payload.get("recommended_system_type")
            or "Recommendation pending review"
        )
        confidence = float(final_payload.get("confidence") or 0.5)
        confidence = max(0.0, min(1.0, confidence))

        reasoning_summary = str(final_payload.get("reasoning_summary") or "")
        safe_summary = BaseAgent._sanitise_text(reasoning_summary)

        compliance_output: dict[str, Any] = {
            "status": ComplianceStatus.NOT_CHECKED,
            "rules_checked": [],
            "violations": [],
            "recommendations": [],
        }
        try:
            if (request.domain_code or "").upper() == "HVAC":
                from apps.procurement.agents.compliance_agent import ComplianceAgent
                from apps.procurement.services.domain.hvac.hvac_compliance_service import HVACComplianceService

                compliance_output = HVACComplianceService.check(attrs, final_payload) or compliance_output

                if (
                    compliance_output.get("status") in {ComplianceStatus.PARTIAL, ComplianceStatus.FAIL}
                    or confidence < 0.75
                ):
                    _compliance_input = {
                        "recommended_option": recommended_option,
                        "confidence": confidence,
                        "estimated_cost": final_payload.get("estimated_cost"),
                        "reasoning_summary": final_payload.get("reasoning_summary"),
                        "constraints": final_payload.get("constraints") or [],
                        "notes": final_payload.get("notes") or [],
                        "standards_notes": attrs.get("required_standards_local_notes") or "",
                        "violations": compliance_output.get("violations") or [],
                    }
                    ai_compliance_output = run_procurement_component_with_tracking(
                        agent_type=AgentType.PROCUREMENT_COMPLIANCE,
                        invocation_reason="ComplianceAgent.check",
                        tenant=getattr(request, "tenant", None),
                        actor_user=request_user,
                        input_payload={
                            "source": "compliance_check",
                            "procurement_request_id": str(getattr(request, "request_id", "")),
                            "procurement_request_pk": getattr(request, "pk", None),
                            "recommended_option": recommended_option,
                            "confidence": confidence,
                        },
                        execute_fn=lambda: ComplianceAgent.check(
                            request,
                            _compliance_input,
                            attrs=attrs,
                        ),
                    ) or {}
                    compliance_output = RecommendationService._merge_compliance_outputs(
                        compliance_output,
                        ai_compliance_output,
                    )
        except Exception:
            logger.exception("Compliance evaluation failed for request pk=%s", getattr(request, "pk", None))
            compliance_output = {
                "status": ComplianceStatus.NOT_CHECKED,
                "rules_checked": [],
                "violations": [],
                "recommendations": [
                    BaseAgent._sanitise_text("Compliance evaluation failed. Review manually.")
                ],
            }

        compliance_status = str(compliance_output.get("status") or ComplianceStatus.NOT_CHECKED)
        final_payload["compliance_status"] = compliance_status
        final_payload["compliance_result"] = compliance_output

        result_obj, _ = RecommendationResult.objects.update_or_create(
            run=run,
            defaults={
                "tenant": request.tenant,
                "recommended_option": recommended_option,
                "reasoning_summary": safe_summary,
                "reasoning_details_json": final_payload.get("reasoning_details") if isinstance(final_payload.get("reasoning_details"), dict) else {},
                "confidence_score": confidence,
                "constraints_json": final_payload.get("constraints") if isinstance(final_payload.get("constraints"), list) else [],
                "compliance_status": compliance_status,
                "output_payload_json": final_payload,
            },
        )

        ComplianceResult.objects.update_or_create(
            run=run,
            defaults={
                "tenant": request.tenant,
                "compliance_status": compliance_status,
                "rules_checked_json": compliance_output.get("rules_checked") or [],
                "violations_json": compliance_output.get("violations") or [],
                "recommendations_json": compliance_output.get("recommendations") or [],
            },
        )

        AnalysisRunService.complete_run(
            run,
            output_summary=safe_summary or recommended_option,
            confidence_score=confidence,
        )

        ProcurementRequestService.update_status(
            request,
            ProcurementRequestStatus.FAILED
            if compliance_status == ComplianceStatus.FAIL
            else ProcurementRequestStatus.PENDING_RFQ,
            user=request_user,
        )

        return result_obj
