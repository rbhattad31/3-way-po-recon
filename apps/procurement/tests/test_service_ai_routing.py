from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from apps.procurement.services.benchmark_service import BenchmarkService
from apps.procurement.services.recommendation_service import RecommendationService
from apps.procurement.services.validation.orchestrator_service import _run_agent_augmentation


def test_recommendation_ai_path_routes_via_orchestrator():
    request = SimpleNamespace(request_id="REQ-1")
    run = SimpleNamespace(pk=10)
    attrs = {"x": 1}
    rule_result = {"confident": False}

    fake_result = SimpleNamespace(status="completed", output={"recommended_option": "X"}, error="")

    with patch("apps.procurement.services.recommendation_service.ProcurementAgentOrchestrator") as mock_orch_cls, \
         patch("apps.procurement.services.recommendation_graph_service.RecommendationGraphService.run", return_value={"recommended_option": "X"}):
        mock_orch_cls.return_value.run.return_value = fake_result

        out = RecommendationService._invoke_ai_via_orchestrator(
            request=request,
            run=run,
            attrs=attrs,
            rule_result=rule_result,
            request_user=None,
        )

    assert out == {"recommended_option": "X"}
    assert mock_orch_cls.return_value.run.call_count == 1
    assert mock_orch_cls.return_value.run.call_args.kwargs["agent_type"] == "recommendation"


def test_benchmark_ai_path_routes_via_orchestrator():
    item = SimpleNamespace(pk=7, description="test-item")
    run = SimpleNamespace(pk=5)

    fake_result = SimpleNamespace(status="completed", output={"avg": 12.5}, error="")

    with patch("apps.procurement.services.benchmark_service.ProcurementAgentOrchestrator") as mock_orch_cls:
        mock_orch_cls.return_value.run.return_value = fake_result

        out = BenchmarkService._resolve_benchmark(
            item,
            run=run,
            memory=None,
            use_ai=True,
            request_user=None,
        )

    assert out == {"avg": 12.5}
    assert mock_orch_cls.return_value.run.call_count == 1
    assert "benchmark_item_" in mock_orch_cls.return_value.run.call_args.kwargs["agent_type"]


def test_validation_augmentation_routes_via_orchestrator_and_falls_back_on_failure():
    request = SimpleNamespace(request_id="REQ-9")
    run = SimpleNamespace(pk=77)
    findings = [
        {"item_code": "A", "status": "AMBIGUOUS"},
        {"item_code": "B", "status": "PRESENT"},
    ]

    # success path
    ok_result = SimpleNamespace(status="completed", output={"updated_findings": findings}, error="")
    with patch("apps.procurement.services.validation.orchestrator_service.ProcurementAgentOrchestrator") as mock_orch_cls, \
         patch("apps.procurement.services.validation.validation_agent.ValidationAgentService.augment_findings", return_value=findings):
        mock_orch_cls.return_value.run.return_value = ok_result
        out = _run_agent_augmentation(request, run, findings, request_user=None)

    assert out == findings
    assert mock_orch_cls.return_value.run.call_count == 1
    assert mock_orch_cls.return_value.run.call_args.kwargs["agent_type"] == "validation_augmentation"

    # failed path -> deterministic findings unchanged
    fail_result = SimpleNamespace(status="failed", output={}, error="boom")
    with patch("apps.procurement.services.validation.orchestrator_service.ProcurementAgentOrchestrator") as mock_orch_cls, \
         patch("apps.procurement.services.validation.validation_agent.ValidationAgentService.augment_findings", return_value=findings):
        mock_orch_cls.return_value.run.return_value = fail_result
        out2 = _run_agent_augmentation(request, run, findings, request_user=None)

    assert out2 == findings
