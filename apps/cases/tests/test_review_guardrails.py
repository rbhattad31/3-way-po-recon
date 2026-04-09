from __future__ import annotations

from unittest.mock import patch

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import reverse
from rest_framework.test import APIClient

from apps.cases.models import APCase, APCaseComment, ReviewAssignment
from apps.cases.template_views import case_agent_view
from apps.core.enums import CaseStatus, MatchStatus, ReconciliationRunStatus, ReviewStatus, UserRole


@pytest.fixture
def tenant_a(db):
    from apps.accounts.models import CompanyProfile

    return CompanyProfile.objects.create(name="Tenant A", slug="tenant-a", is_active=True)


@pytest.fixture
def tenant_b(db):
    from apps.accounts.models import CompanyProfile

    return CompanyProfile.objects.create(name="Tenant B", slug="tenant-b", is_active=True)


@pytest.fixture
def admin_a(db, tenant_a):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="admin-a@example.com",
        password="pass",
        first_name="Admin",
        last_name="A",
        role=UserRole.ADMIN,
        company=tenant_a,
    )


@pytest.fixture
def admin_b(db, tenant_b):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="admin-b@example.com",
        password="pass",
        first_name="Admin",
        last_name="B",
        role=UserRole.ADMIN,
        company=tenant_b,
    )


@pytest.fixture
def reviewer_a(db, tenant_a):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="reviewer-a@example.com",
        password="pass",
        first_name="Reviewer",
        last_name="A",
        role=UserRole.REVIEWER,
        company=tenant_a,
    )


@pytest.fixture
def ap_processor_a(db, tenant_a):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="ap-a@example.com",
        password="pass",
        first_name="AP",
        last_name="A",
        role=UserRole.AP_PROCESSOR,
        company=tenant_a,
    )


@pytest.fixture
def review_bundle_factory(db):
    from apps.documents.models import DocumentUpload, Invoice
    from apps.reconciliation.models import ReconciliationConfig, ReconciliationResult, ReconciliationRun

    def _create(tenant, *, uploaded_by=None, assigned_to=None, review_status=ReviewStatus.IN_REVIEW, create_assignment=True):
        upload = DocumentUpload.objects.create(
            tenant=tenant,
            original_filename=f"{tenant.slug}.pdf",
            uploaded_by=uploaded_by,
        )
        invoice = Invoice.objects.create(
            tenant=tenant,
            document_upload=upload,
            invoice_number=f"INV-{tenant.slug}",
            total_amount="100.00",
            currency="USD",
        )
        config = ReconciliationConfig.objects.create(
            tenant=tenant,
            name=f"Config {tenant.slug}",
            is_default=False,
        )
        run = ReconciliationRun.objects.create(
            tenant=tenant,
            status=ReconciliationRunStatus.RUNNING,
            config=config,
        )
        result = ReconciliationResult.objects.create(
            run=run,
            tenant=tenant,
            invoice=invoice,
            match_status=MatchStatus.REQUIRES_REVIEW,
            requires_review=True,
        )
        case = APCase.objects.create(
            tenant=tenant,
            invoice=invoice,
            reconciliation_result=result,
            case_number=f"CASE-{tenant.slug}-{invoice.pk}",
            status=CaseStatus.IN_REVIEW,
            processing_path="THREE_WAY",
        )
        assignment = None
        if create_assignment:
            assignment = ReviewAssignment.objects.create(
                reconciliation_result=result,
                tenant=tenant,
                assigned_to=assigned_to,
                status=review_status,
                priority=5,
            )
            case.review_assignment = assignment
            case.save(update_fields=["review_assignment", "updated_at"])
        return {
            "upload": upload,
            "invoice": invoice,
            "result": result,
            "case": case,
            "assignment": assignment,
        }

    return _create


@pytest.mark.django_db
class TestTemplateTenantIsolation:
    def test_case_add_comment_blocks_cross_tenant_case(self, client, admin_a, admin_b, review_bundle_factory):
        bundle_b = review_bundle_factory(admin_b.company, assigned_to=admin_b)
        client.force_login(admin_a)

        with patch("apps.auditlog.services.AuditService.log_event"):
            response = client.post(
                reverse("cases:case_add_comment", args=[bundle_b["case"].pk]),
                {"body": "cross-tenant"},
            )

        assert response.status_code == 404
        assert APCaseComment.objects.count() == 0

    def test_case_assign_blocks_cross_tenant_case(self, client, admin_a, admin_b, reviewer_a, review_bundle_factory):
        bundle_b = review_bundle_factory(admin_b.company, assigned_to=admin_b)
        client.force_login(admin_a)

        response = client.post(
            reverse("cases:case_assign", args=[bundle_b["case"].pk]),
            {"assigned_to": reviewer_a.pk},
        )

        bundle_b["case"].refresh_from_db()
        assert response.status_code == 404
        assert bundle_b["case"].assigned_to_id is None

    def test_review_assignment_detail_blocks_cross_tenant_assignment(self, client, admin_a, admin_b, review_bundle_factory):
        bundle_b = review_bundle_factory(admin_b.company, assigned_to=admin_b)
        client.force_login(admin_a)

        response = client.get(reverse("reviews:assignment_detail", args=[bundle_b["assignment"].pk]))

        assert response.status_code == 404

    def test_review_decide_blocks_cross_tenant_assignment(self, client, admin_a, admin_b, review_bundle_factory):
        bundle_b = review_bundle_factory(admin_b.company, assigned_to=admin_b)
        client.force_login(admin_a)

        with patch("apps.auditlog.services.AuditService.log_event"), patch(
            "apps.core.langfuse_client.get_client", return_value=None
        ):
            response = client.post(
                reverse("reviews:decide", args=[bundle_b["assignment"].pk]),
                {"decision": "APPROVED", "reason": "cross-tenant"},
            )

        bundle_b["assignment"].refresh_from_db()
        assert response.status_code == 404
        assert bundle_b["assignment"].status == ReviewStatus.IN_REVIEW

    def test_review_add_comment_blocks_cross_tenant_assignment(self, client, admin_a, admin_b, review_bundle_factory):
        bundle_b = review_bundle_factory(admin_b.company, assigned_to=admin_b)
        client.force_login(admin_a)

        response = client.post(
            reverse("reviews:add_comment", args=[bundle_b["assignment"].pk]),
            {"body": "cross-tenant"},
        )

        assert response.status_code == 404
        assert bundle_b["assignment"].comments.count() == 0

    def test_review_create_assignments_ignores_cross_tenant_results(self, client, admin_a, admin_b, review_bundle_factory):
        bundle_b = review_bundle_factory(admin_b.company, create_assignment=False)
        client.force_login(admin_a)

        with patch("apps.auditlog.services.AuditService.log_event"), patch(
            "apps.core.langfuse_client.start_trace_safe", return_value=None
        ), patch("apps.core.langfuse_client.score_trace_safe"), patch(
            "apps.reconciliation.services.eval_adapter.ReconciliationEvalAdapter.sync_for_review_assignment"
        ):
            response = client.post(
                reverse("reviews:create_assignments"),
                {"result_ids": [str(bundle_b["result"].pk)]},
            )

        assert response.status_code == 302
        assert not ReviewAssignment.objects.filter(reconciliation_result=bundle_b["result"]).exists()


@pytest.mark.django_db
class TestReviewApiPermissions:
    def test_reviewer_can_start_review_via_api(self, reviewer_a, review_bundle_factory):
        bundle = review_bundle_factory(reviewer_a.company, assigned_to=reviewer_a, review_status=ReviewStatus.ASSIGNED)
        client = APIClient()
        client.force_authenticate(user=reviewer_a)

        with patch("apps.auditlog.services.AuditService.log_event"), patch(
            "apps.core.langfuse_client.get_client", return_value=None
        ):
            response = client.post(f"/api/v1/reviews/{bundle['assignment'].pk}/start/")

        bundle["assignment"].refresh_from_db()
        assert response.status_code == 200
        assert bundle["assignment"].status == ReviewStatus.IN_REVIEW

    def test_ap_processor_cannot_start_someone_elses_review_via_api(self, ap_processor_a, reviewer_a, review_bundle_factory):
        bundle = review_bundle_factory(reviewer_a.company, assigned_to=reviewer_a, review_status=ReviewStatus.ASSIGNED)
        client = APIClient()
        client.force_authenticate(user=ap_processor_a)

        response = client.post(f"/api/v1/reviews/{bundle['assignment'].pk}/start/")

        bundle["assignment"].refresh_from_db()
        assert response.status_code == 403
        assert bundle["assignment"].status == ReviewStatus.ASSIGNED

    def test_case_comments_post_requires_comment_permission(self, reviewer_a, review_bundle_factory):
        bundle = review_bundle_factory(reviewer_a.company, assigned_to=reviewer_a)
        client = APIClient()
        client.force_authenticate(user=reviewer_a)

        response = client.post(
            f"/api/v1/cases/{bundle['case'].pk}/comments/",
            {"body": "no write permission"},
            format="json",
        )

        assert response.status_code == 403


@pytest.mark.django_db
class TestCaseAgentViewDoesNotCreateAssignments:
    def test_case_agent_view_get_does_not_create_review_assignment(self, reviewer_a, review_bundle_factory):
        bundle = review_bundle_factory(reviewer_a.company, create_assignment=False)
        bundle["case"].status = "READY_FOR_REVIEW"
        bundle["case"].save(update_fields=["status", "updated_at"])

        request = RequestFactory().get(reverse("cases:case_agent_view", args=[bundle["case"].pk]))
        request.user = reviewer_a
        request.tenant = reviewer_a.company

        with patch("apps.cases.template_views.render", return_value=HttpResponse("ok")), patch(
            "apps.auditlog.timeline_service.CaseTimelineService.get_case_timeline", return_value=[]
        ):
            response = case_agent_view.__wrapped__(request, bundle["case"].pk)

        assert response.status_code == 200
        assert not ReviewAssignment.objects.filter(reconciliation_result=bundle["result"]).exists()
