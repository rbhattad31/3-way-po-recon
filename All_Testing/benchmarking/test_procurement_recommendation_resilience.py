from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.core.enums import AnalysisRunType, ProcurementRequestStatus
from apps.procurement.models import HVACRecommendationRule
from apps.procurement.services.recommendation_graph_service import RecommendationGraphService
from apps.procurement.tasks import run_analysis_task


class ProcurementRecommendationResilienceTests(SimpleTestCase):
    def test_hvac_rule_matches_handles_non_numeric_attrs_without_crashing(self):
        rule = HVACRecommendationRule(
            rule_code="R-RES-001",
            rule_name="Resilience rule",
            store_type_filter="MALL",
            area_sq_ft_min=1000,
            ambient_temp_min_c=40,
            budget_level_filter="MEDIUM_HIGH",
            energy_priority_filter="MEDIUM_HIGH",
            recommended_system="VRF",
            priority=1,
            is_active=True,
        )

        attrs = {
            "country": "UAE",
            "city": "Dubai",
            "store_type": "MALL",
            "area_sqft": "1,200 sqft",
            "ambient_temp_max": "45 C",
            "budget_level": "HIGH",
            "energy_efficiency_priority": "HIGH",
        }

        matched = rule.matches(attrs)

        self.assertFalse(matched)

    def test_run_analysis_task_marks_run_failed_when_recommendation_service_errors(self):
        proc_request = SimpleNamespace(
            pk=210,
            domain_code="HVAC",
            status=ProcurementRequestStatus.PENDING_RFQ,
        )
        run = SimpleNamespace(
            pk=99,
            run_id="run-99",
            run_type=AnalysisRunType.RECOMMENDATION,
            request=proc_request,
            triggered_by_id=None,
            triggered_by=None,
            trace_id="",
            refresh_from_db=Mock(),
        )
        run_queryset = Mock()
        run_queryset.get.return_value = run

        with patch("apps.procurement.models.AnalysisRun.objects.select_related", return_value=run_queryset), patch(
            "apps.procurement.services.recommendation_service.RecommendationService.run_recommendation",
            side_effect=RuntimeError("forced recommendation failure"),
        ), patch(
            "apps.procurement.services.analysis_run_service.AnalysisRunService.fail_run"
        ) as fail_run_mock, patch(
            "apps.procurement.services.request_service.ProcurementRequestService.update_status"
        ) as update_status_mock, patch(
            "apps.procurement.services.eval_adapter.ProcurementEvalAdapter.sync_for_analysis_run"
        ):
            result = run_analysis_task.run(tenant_id=None, run_id=run.pk)

        self.assertEqual(result["status"], "failed")
        fail_run_mock.assert_called_once()
        fail_call = fail_run_mock.call_args[0]
        self.assertEqual(fail_call[0], run)
        self.assertIn("forced recommendation failure", fail_call[1])
        self.assertEqual(update_status_mock.call_args_list[-1][0][0], proc_request)
        self.assertEqual(update_status_mock.call_args_list[-1][0][1], ProcurementRequestStatus.FAILED)

    def test_no_rule_match_calls_ai_recommend_path(self):
        state = {
            "ai_payload": {
                "attributes": {"store_type": "MALL"},
                "rule_result": {
                    "confident": False,
                    "reasoning_details": {"rules_evaluated": 12},
                },
            },
            "request": SimpleNamespace(pk=210),
        }

        expected = {
            "recommended_option": "VRF",
            "confidence": 0.78,
            "reasoning_summary": "AI recommendation for no-rule path",
        }

        with patch(
            "apps.procurement.services.recommendation_graph_service.HVACRecommendationAgent.recommend",
            return_value=expected,
        ) as recommend_mock, patch(
            "apps.procurement.services.recommendation_graph_service.HVACRecommendationAgent.explain"
        ) as explain_mock:
            result = RecommendationGraphService._call_recommendation_agent(state)

        recommend_mock.assert_called_once_with(
            attrs={"store_type": "MALL"},
            no_match_context={"rules_evaluated": 12},
            procurement_request_pk=210,
        )
        explain_mock.assert_not_called()
        self.assertEqual(result, {"ai_result": expected})
