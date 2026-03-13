"""
Case selectors — query helpers for AP Cases.

Provides filtered querysets used by views, APIs, and services.
"""

from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet, Subquery
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
        match_status: str = "",
        reconciliation_mode: str = "",
        date_from: str = "",
        date_to: str = "",
        processing_type: str = "",
    ) -> QuerySet:
        """Filtered inbox queryset for the AP Case list view."""
        from apps.agents.models import AgentRun

        qs = APCase.objects.select_related(
            "invoice", "vendor", "assigned_to", "purchase_order",
            "reconciliation_result", "review_assignment",
        ).filter(is_active=True)

        # Annotate with has_agent_runs for processing type display
        qs = qs.annotate(
            has_agent_runs=Exists(
                AgentRun.objects.filter(
                    reconciliation_result_id=OuterRef("reconciliation_result_id"),
                ).exclude(status="SKIPPED")
            ),
        )

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
        if match_status:
            qs = qs.filter(reconciliation_result__match_status=match_status)
        if reconciliation_mode:
            qs = qs.filter(reconciliation_mode=reconciliation_mode)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)
        if processing_type == "agent_only":
            qs = qs.filter(has_agent_runs=True, requires_human_review=False)
        elif processing_type == "human_involved":
            qs = qs.filter(requires_human_review=True)
        elif processing_type == "mixed":
            qs = qs.filter(has_agent_runs=True, requires_human_review=True)
        if search:
            qs = qs.filter(
                Q(case_number__icontains=search)
                | Q(invoice__invoice_number__icontains=search)
                | Q(vendor__name__icontains=search)
                | Q(invoice__raw_vendor_name__icontains=search)
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

        agent_processed = APCase.objects.filter(
            is_active=True, requires_human_review=False,
        ).exclude(status=CaseStatus.NEW).count()
        human_involved = APCase.objects.filter(
            is_active=True, requires_human_review=True,
        ).count()

        return {
            "total": total,
            "by_status": by_status,
            "by_path": by_path,
            "overdue": overdue,
            "agent_processed": agent_processed,
            "human_involved": human_involved,
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
