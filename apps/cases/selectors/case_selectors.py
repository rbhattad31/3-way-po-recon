"""
Case selectors — query helpers for AP Cases.

Provides filtered querysets used by views, APIs, and services.
"""

from django.db.models import Count, Exists, F, OuterRef, Q, QuerySet, Subquery
from django.utils import timezone

from apps.cases.models import APCase
from apps.core.enums import CaseStatus, ProcessingPath, UserRole


class CaseSelectors:

    @staticmethod
    def stats_from_queryset(base: QuerySet) -> dict:
        """Aggregate case statistics from a pre-scoped/pre-filtered queryset."""
        total = base.count()
        by_status = dict(
            base
            .values_list("status")
            .annotate(count=Count("id"))
            .values_list("status", "count")
        )
        by_path = dict(
            base
            .values_list("processing_path")
            .annotate(count=Count("id"))
            .values_list("processing_path", "count")
        )
        overdue = base.filter(
            status__in=[CaseStatus.READY_FOR_REVIEW, CaseStatus.IN_REVIEW],
            created_at__lt=timezone.now() - timezone.timedelta(hours=48),
        ).count()

        agent_processed = base.filter(
            requires_human_review=False,
        ).exclude(status=CaseStatus.NEW).count()
        human_involved = base.filter(
            requires_human_review=True,
        ).count()

        # In-progress: all *_IN_PROGRESS statuses + NEW (pipeline not yet complete)
        in_progress_statuses = [
            s for s in CaseStatus
            if "IN_PROGRESS" in s.value or s == CaseStatus.NEW
        ]
        in_progress = base.filter(status__in=in_progress_statuses).count()

        failed = by_status.get(CaseStatus.FAILED, 0)

        return {
            "total": total,
            "by_status": by_status,
            "by_path": by_path,
            "overdue": overdue,
            "agent_processed": agent_processed,
            "human_involved": human_involved,
            "in_progress": in_progress,
            "failed": failed,
        }

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
    def scope_for_user(qs: QuerySet, user) -> QuerySet:
        """Apply ownership scoping based on user role and config.

        AP_PROCESSOR users only see cases for invoices they uploaded,
        unless ``ap_processor_sees_all_cases`` is enabled in the default
        ReconciliationConfig.
        """
        if not user or not user.is_authenticated:
            return qs.none()
        user_role = getattr(user, "role", None)

        # REVIEWER sees cases assigned to them + unassigned review-ready cases
        if user_role == UserRole.REVIEWER:
            return qs.filter(
                Q(assigned_to=user) | Q(assigned_to__isnull=True)
            )

        if user_role != UserRole.AP_PROCESSOR:
            return qs  # ADMIN, FINANCE_MANAGER, AUDITOR see everything

        from apps.reconciliation.models import ReconciliationConfig
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config and config.ap_processor_sees_all_cases:
            return qs

        # AP_PROCESSOR sees only their own cases
        return qs.filter(
            Q(invoice__document_upload__uploaded_by=user)
            | Q(assigned_to=user)
        )

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
    def stats(user=None) -> dict:
        """Aggregate case statistics for dashboard.

        When *user* is provided the counts are scoped via
        ``scope_for_user`` so AP_PROCESSOR only sees their own numbers.
        """
        base = APCase.objects.filter(is_active=True)
        if user:
            base = CaseSelectors.scope_for_user(base, user)
        return CaseSelectors.stats_from_queryset(base)

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
