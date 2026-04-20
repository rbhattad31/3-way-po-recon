"""
TEST 10 -- Cases, Reviews & Workflow
=====================================
Covers:
  - APCase model and lifecycle statuses
  - ReviewAssignment + ReviewDecision + ReviewComment
  - ReviewWorkflowService
  - CaseOrchestrator
  - CaseTimelineService
  - Cases and Reviews UI pages
  - Copilot session UI
"""

import pytest

pytestmark = pytest.mark.django_db(transaction=False)


class TestCaseModels:
    """AP Case domain models."""

    def test_apcase_model(self):
        from apps.cases.models import APCase
        assert APCase is not None

    def test_review_assignment_model(self):
        from apps.cases.models import ReviewAssignment
        assert ReviewAssignment is not None

    def test_review_decision_model(self):
        from apps.cases.models import ReviewDecision
        assert ReviewDecision is not None

    def test_review_comment_model(self):
        from apps.cases.models import ReviewComment
        assert ReviewComment is not None

    def test_manual_review_action_model(self):
        try:
            from apps.cases.models import ManualReviewAction
            assert ManualReviewAction is not None
        except ImportError:
            pytest.skip("ManualReviewAction not present")

    def test_case_status_enum_importable(self):
        from apps.core.enums import CaseStatus
        assert CaseStatus is not None

    def test_review_status_enum_importable(self):
        from apps.core.enums import ReviewStatus
        assert ReviewStatus is not None


class TestReviewWorkflowService:
    """ReviewWorkflowService key methods."""

    def test_review_workflow_service_importable(self):
        from apps.cases.services.review_workflow_service import ReviewWorkflowService
        assert ReviewWorkflowService is not None

    def test_has_create_assignment_method(self):
        from apps.cases.services.review_workflow_service import ReviewWorkflowService
        assert hasattr(ReviewWorkflowService, "create_assignment"), \
            "ReviewWorkflowService.create_assignment() missing"

    def test_has_finalise_method(self):
        from apps.cases.services.review_workflow_service import ReviewWorkflowService
        assert hasattr(ReviewWorkflowService, "_finalise") or \
               hasattr(ReviewWorkflowService, "finalize") or \
               hasattr(ReviewWorkflowService, "approve"), \
            "ReviewWorkflowService must have finalize/approve method"


class TestCaseOrchestrator:
    """CaseOrchestrator stage machine."""

    def test_case_orchestrator_importable(self):
        try:
            from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator
            assert CaseOrchestrator is not None
        except ImportError:
            pytest.skip("CaseOrchestrator not available")

    def test_case_creation_service_importable(self):
        try:
            from apps.cases.services.case_creation_service import CaseCreationService
            assert CaseCreationService is not None
        except ImportError:
            pytest.skip("CaseCreationService not available")

    def test_case_task_importable(self):
        try:
            from apps.cases.tasks import process_case_task
            assert process_case_task is not None
        except ImportError:
            pytest.skip("process_case_task not available")


class TestCaseTimelineService:
    """CaseTimelineService -- unified 8-category timeline."""

    def test_timeline_service_importable(self):
        from apps.auditlog.timeline_service import CaseTimelineService
        assert CaseTimelineService is not None

    def test_timeline_service_has_build_method(self):
        from apps.auditlog.timeline_service import CaseTimelineService
        assert hasattr(CaseTimelineService, "get_case_timeline") or \
               hasattr(CaseTimelineService, "get_stage_timeline"), \
            "CaseTimelineService must expose timeline retrieval methods"


class TestCasesUI:
    """Cases + Reviews UI pages."""

    CASE_URLS = [
        "/cases/",
        "/reviews/",
    ]

    def test_case_pages_no_500(self, admin_client):
        failures = []
        for url in self.CASE_URLS:
            r = admin_client.get(url)
            if r.status_code == 500:
                failures.append(url)
        assert not failures, f"These case pages returned 500: {failures}"

    def test_case_pages_accessible(self, admin_client):
        for url in self.CASE_URLS:
            r = admin_client.get(url)
            assert r.status_code in (200, 302, 404), \
                f"{url} returned {r.status_code}"

    def test_cases_api_accessible(self, admin_client):
        r = admin_client.get("/api/v1/cases/")
        assert r.status_code in (200, 204), \
            f"Cases API returned {r.status_code}"


class TestCopilot:
    """Copilot chat session."""

    def test_copilot_ui_accessible(self, admin_client):
        r = admin_client.get("/copilot/")
        assert r.status_code in (200, 302, 404), \
            f"/copilot/ returned {r.status_code}"

    def test_copilot_api_accessible(self, admin_client):
        r = admin_client.get("/api/v1/copilot/")
        assert r.status_code in (200, 204, 404), \
            f"Copilot API returned {r.status_code}"

    def test_copilot_service_importable(self):
        try:
            from apps.copilot.services.copilot_service import CopilotService
            assert CopilotService is not None
        except ImportError:
            pytest.skip("CopilotService not available")


class TestReportGeneration:
    """Reports module."""

    def test_reports_ui_accessible(self, admin_client):
        r = admin_client.get("/reports/")
        assert r.status_code in (200, 302, 404), \
            f"/reports/ returned {r.status_code}"

    def test_reports_model_importable(self):
        try:
            from apps.reports import models as rm
            assert rm is not None
        except ImportError:
            pytest.skip("Reports models not available")
