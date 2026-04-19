from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.benchmarking.models import (
    BenchmarkLineItem,
    BenchmarkQuotation,
    BenchmarkRequest,
    BenchmarkResult,
)


class BenchmarkRequestDeleteViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            email="bench-admin@example.com",
            password="testpass123",
        )
        self.client.force_login(self.user)

        self.bench_request = BenchmarkRequest.objects.create(
            title="Delete Me",
            geography="UAE",
            scope_type="SITC",
            status="COMPLETED",
            is_active=True,
        )
        self.quotation = BenchmarkQuotation.objects.create(
            request=self.bench_request,
            supplier_name="Supplier X",
            quotation_ref="QT-DEL-001",
            extraction_status="DONE",
            is_active=True,
        )
        self.line_item = BenchmarkLineItem.objects.create(
            quotation=self.quotation,
            description="Ducting work",
            line_number=1,
            category="DUCTING",
            variance_status="NEEDS_REVIEW",
            is_active=True,
        )
        self.result = BenchmarkResult.objects.create(
            request=self.bench_request,
            overall_status="NEEDS_REVIEW",
            is_active=True,
        )

    def test_request_delete_soft_deletes_children_and_redirects(self):
        response = self.client.post(
            reverse("benchmarking:request_delete", args=[self.bench_request.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        self.bench_request.refresh_from_db()
        self.quotation.refresh_from_db()
        self.line_item.refresh_from_db()
        self.result.refresh_from_db()

        self.assertFalse(self.bench_request.is_active)
        self.assertFalse(self.quotation.is_active)
        self.assertFalse(self.line_item.is_active)
        self.assertFalse(self.result.is_active)
        rendered_requests = list(response.context["bench_requests"])
        self.assertEqual(rendered_requests, [])