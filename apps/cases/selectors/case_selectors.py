"""
Case selectors — query helpers for AP Cases.

Provides filtered querysets used by views, APIs, and services.
"""

from django.db.models import Count, F, Q, QuerySet
from django.utils import timezone

from apps.cases.models import APCase
from apps.core.enums import CaseStatus, ProcessingPath


class CaseSelectors:

    @staticmethod
    def inbox(
        processing_path: str = "",
        status: str = "",
        priority: str = "",
        assigned_to_id: int = None,
        vendor_id: int = None,
        search: str = "",
    ) -> QuerySet:
        """Filtered inbox queryset for the AP Case list view."""
        qs = APCase.objects.select_related(
            "invoice", "vendor", "assigned_to", "purchase_order",
        ).filter(is_active=True)

        if processing_path:
            qs = qs.filter(processing_path=processing_path)
        if status:
            qs = qs.filter(status=status)
        if priority:
            qs = qs.filter(priority=priority)
        if assigned_to_id:
            qs = qs.filter(assigned_to_id=assigned_to_id)
        if vendor_id:
            qs = qs.filter(vendor_id=vendor_id)
        if search:
            qs = qs.filter(
                Q(case_number__icontains=search)
                | Q(invoice__invoice_number__icontains=search)
                | Q(vendor__name__icontains=search)
            )

        return qs.order_by("-created_at")

    @staticmethod
    def for_review(user=None) -> QuerySet:
        """Cases assigned to a user (or unassigned) that need review."""
        qs = APCase.objects.filter(
            status__in=[CaseStatus.READY_FOR_REVIEW, CaseStatus.IN_REVIEW],
            is_active=True,
        ).select_related("invoice", "vendor")

        if user:
            qs = qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))

        return qs.order_by("priority", "created_at")

    @staticmethod
    def stats() -> dict:
        """Aggregate case statistics for dashboard."""
        total = APCase.objects.filter(is_active=True).count()
        by_status = dict(
            APCase.objects.filter(is_active=True)
            .values_list("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )
        by_path = dict(
            APCase.objects.filter(is_active=True)
            .values_list("processing_path")
            .annotate(count=Count("id"))
            .values_list("processing_path", "count")
        )
        overdue = APCase.objects.filter(
            is_active=True,
            status__in=[CaseStatus.READY_FOR_REVIEW, CaseStatus.IN_REVIEW],
            created_at__lt=timezone.now() - timezone.timedelta(hours=48),
        ).count()

        return {
            "total": total,
            "by_status": by_status,
            "by_path": by_path,
            "overdue": overdue,
        }

    @staticmethod
    def get_with_related(case_id: int) -> APCase:
        """Load a case with all related data for the case console."""
        return APCase.objects.select_related(
            "invoice", "vendor", "purchase_order",
            "reconciliation_result", "review_assignment",
            "assigned_to", "summary",
        ).prefetch_related(
            "stages", "artifacts", "decisions",
            "assignments", "comments", "activities",
        ).get(id=case_id, is_active=True)
