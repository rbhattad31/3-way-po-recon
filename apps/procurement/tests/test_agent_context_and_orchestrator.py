from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.core.enums import AnalysisRunStatus, AnalysisRunType, ProcurementRequestType
from apps.procurement.models import AnalysisRun, ProcurementAgentExecutionRecord, ProcurementRequest
from apps.procurement.runtime import ProcurementAgentMemory, ProcurementAgentOrchestrator


User = get_user_model()


@pytest.mark.django_db
class TestProcurementAgentOrchestrator:
    def _seed_run(self):
        user = User.objects.create(email="proc-test@example.com", role="AP_PROCESSOR")
        req = ProcurementRequest.objects.create(
            title="Test Request",
            domain_code="HVAC",
            schema_code="HVAC_STD",
            request_type=ProcurementRequestType.RECOMMENDATION,
            status="PROCESSING",
            created_by=user,
            assigned_to=user,
        )
        run = AnalysisRun.objects.create(
            request=req,
            run_type=AnalysisRunType.RECOMMENDATION,
            status=AnalysisRunStatus.RUNNING,
            triggered_by=user,
            created_by=user,
        )
        return user, req, run

    def test_run_creates_completed_execution_record(self):
        user, req, run = self._seed_run()
        orchestrator = ProcurementAgentOrchestrator()
        memory = ProcurementAgentMemory()
        captured = {}

        def _agent_fn(ctx):
            captured["ctx"] = ctx
            return {
                "reasoning_summary": "AI chose option X",
                "confidence": 0.88,
                "recommended_option": "Option X",
            }

        result = orchestrator.run(
            run=run,
            agent_type="recommendation",
            agent_fn=_agent_fn,
            memory=memory,
            request_user=user,
        )

        assert result.status == "completed"
        assert result.confidence == 0.88
        assert result.execution_record_id is not None

        rec = ProcurementAgentExecutionRecord.objects.get(pk=result.execution_record_id)
        assert rec.status == AnalysisRunStatus.COMPLETED
        assert rec.agent_type == "recommendation"
        assert rec.confidence_score == pytest.approx(0.88)
        assert "AI chose option X" in rec.reasoning_summary
        assert rec.trace_id == run.trace_id or rec.trace_id == ""

        # Context shape checks
        assert captured["ctx"].procurement_request_id == req.pk
        assert captured["ctx"].analysis_run_id == run.pk
        assert captured["ctx"].analysis_type == AnalysisRunType.RECOMMENDATION
        assert captured["ctx"].domain_code == "HVAC"

        # Shared memory updated
        assert memory.current_recommendation == "Option X"
        assert memory.current_confidence == pytest.approx(0.88)

    def test_run_failure_marks_execution_record_failed(self):
        user, req, run = self._seed_run()
        orchestrator = ProcurementAgentOrchestrator()

        def _agent_fn(_ctx):
            raise RuntimeError("simulated agent failure")

        result = orchestrator.run(
            run=run,
            agent_type="benchmark",
            agent_fn=_agent_fn,
            request_user=user,
        )

        assert result.status == "failed"
        assert "simulated agent failure" in result.error
        assert result.execution_record_id is not None

        rec = ProcurementAgentExecutionRecord.objects.get(pk=result.execution_record_id)
        assert rec.status == AnalysisRunStatus.FAILED
        assert "simulated agent failure" in rec.error_message
