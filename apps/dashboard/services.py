"""Dashboard analytics service — aggregation queries for the dashboard."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.agents.models import AgentRun
from apps.core.enums import AgentRunStatus, MatchStatus, ReviewStatus, UserRole
from apps.documents.models import GoodsReceiptNote, Invoice, PurchaseOrder
from apps.reconciliation.models import ReconciliationException, ReconciliationResult
from apps.reviews.models import ReviewAssignment
from apps.vendors.models import Vendor


class DashboardService:
    """Read-only aggregation service for the main dashboard."""

    # ------------------------------------------------------------------
    # Scoping helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _scope_invoices(qs, user=None):
        """Restrict invoice queryset for AP_PROCESSOR based on config."""
        if user is None or getattr(user, "role", None) != UserRole.AP_PROCESSOR:
            return qs
        from apps.reconciliation.models import ReconciliationConfig
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config and config.ap_processor_sees_all_cases:
            return qs
        return qs.filter(document_upload__uploaded_by=user)

    @staticmethod
    def _scope_recon_results(qs, user=None):
        """Restrict reconciliation results to invoices visible to user."""
        if user is None or getattr(user, "role", None) != UserRole.AP_PROCESSOR:
            return qs
        from apps.reconciliation.models import ReconciliationConfig
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config and config.ap_processor_sees_all_cases:
            return qs
        return qs.filter(invoice__document_upload__uploaded_by=user)

    @staticmethod
    def _scope_exceptions(qs, user=None):
        """Restrict exceptions to reconciliation results visible to user."""
        if user is None or getattr(user, "role", None) != UserRole.AP_PROCESSOR:
            return qs
        from apps.reconciliation.models import ReconciliationConfig
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config and config.ap_processor_sees_all_cases:
            return qs
        return qs.filter(result__invoice__document_upload__uploaded_by=user)

    @staticmethod
    def _scope_reviews(qs, user=None):
        """Restrict review assignments to invoices visible to user."""
        if user is None or getattr(user, "role", None) != UserRole.AP_PROCESSOR:
            return qs
        from apps.reconciliation.models import ReconciliationConfig
        config = ReconciliationConfig.objects.filter(is_default=True).first()
        if config and config.ap_processor_sees_all_cases:
            return qs
        return qs.filter(
            reconciliation_result__invoice__document_upload__uploaded_by=user
        )

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------
    @staticmethod
    def get_summary(user=None) -> Dict[str, Any]:
        invoice_qs = DashboardService._scope_invoices(Invoice.objects.all(), user)
        recon_qs = DashboardService._scope_recon_results(ReconciliationResult.objects.all(), user)
        exception_qs = DashboardService._scope_exceptions(ReconciliationException.objects.filter(resolved=False), user)
        review_qs = DashboardService._scope_reviews(ReviewAssignment.objects.filter(
            status__in=[ReviewStatus.PENDING, ReviewStatus.ASSIGNED, ReviewStatus.IN_REVIEW]
        ), user)

        total_results = recon_qs.count()
        matched = recon_qs.filter(match_status=MatchStatus.MATCHED).count()
        avg_conf = recon_qs.aggregate(
            avg=Avg("deterministic_confidence")
        )["avg"] or 0.0

        from apps.core.enums import InvoiceStatus
        extracted = invoice_qs.exclude(
            status__in=[InvoiceStatus.UPLOADED, InvoiceStatus.EXTRACTION_IN_PROGRESS]
        ).count()
        reconciled = invoice_qs.filter(
            status=InvoiceStatus.RECONCILED
        ).count()

        return {
            "total_invoices": invoice_qs.count(),
            "total_pos": PurchaseOrder.objects.count(),
            "total_grns": GoodsReceiptNote.objects.count(),
            "total_vendors": Vendor.objects.filter(is_active=True).count(),
            "pending_reviews": review_qs.count(),
            "open_exceptions": exception_qs.count(),
            "matched_pct": round((matched / total_results * 100) if total_results else 0, 1),
            "avg_confidence": round(avg_conf * 100, 1),
            "extracted_count": extracted,
            "reconciled_count": total_results,
            "posted_count": reconciled,
        }

    @staticmethod
    def get_match_status_breakdown(user=None) -> List[Dict[str, Any]]:
        qs = DashboardService._scope_recon_results(ReconciliationResult.objects.all(), user)
        total = qs.count() or 1
        rows = (
            qs.values("match_status")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        return [
            {
                "match_status": row["match_status"],
                "count": row["count"],
                "percentage": round(row["count"] / total * 100, 1),
            }
            for row in rows
        ]

    @staticmethod
    def get_exception_breakdown(user=None) -> List[Dict[str, Any]]:
        qs = DashboardService._scope_exceptions(
            ReconciliationException.objects.filter(resolved=False), user
        )
        return list(
            qs.values("exception_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

    @staticmethod
    def get_mode_breakdown(user=None) -> List[Dict[str, Any]]:
        """Breakdown of reconciliation results by mode (2-Way vs 3-Way)."""
        qs = DashboardService._scope_recon_results(ReconciliationResult.objects.all(), user)
        total = qs.count() or 1
        rows = (
            qs.values("reconciliation_mode")
            .annotate(
                count=Count("id"),
                matched_count=Count("id", filter=Q(match_status=MatchStatus.MATCHED)),
                avg_confidence=Avg("deterministic_confidence"),
            )
            .order_by("-count")
        )
        return [
            {
                "reconciliation_mode": row["reconciliation_mode"] or "UNSET",
                "count": row["count"],
                "percentage": round(row["count"] / total * 100, 1),
                "matched_count": row["matched_count"],
                "match_rate": round(
                    (row["matched_count"] / row["count"] * 100)
                    if row["count"] else 0, 1
                ),
                "avg_confidence": round(
                    (row["avg_confidence"] or 0) * 100, 1
                ),
            }
            for row in qs
        ]

    @staticmethod
    def get_agent_performance(user=None) -> List[Dict[str, Any]]:
        qs = AgentRun.objects.all()
        if user is not None and getattr(user, "role", None) == UserRole.AP_PROCESSOR:
            from apps.reconciliation.models import ReconciliationConfig
            config = ReconciliationConfig.objects.filter(is_default=True).first()
            if not (config and config.ap_processor_sees_all_cases):
                qs = qs.filter(
                    reconciliation_result__invoice__document_upload__uploaded_by=user
                )
        return list(
            qs.values("agent_type")
            .annotate(
                total_runs=Count("id"),
                success_count=Count("id", filter=Q(status=AgentRunStatus.COMPLETED)),
                avg_confidence=Avg("confidence"),
                avg_duration_ms=Avg("duration_ms"),
                total_tokens=Sum("total_tokens"),
            )
            .order_by("agent_type")
        )

    @staticmethod
    def get_daily_volume(days: int = 30, user=None) -> List[Dict[str, Any]]:
        since = timezone.now() - timedelta(days=days)
        inv_qs = DashboardService._scope_invoices(
            Invoice.objects.filter(created_at__gte=since), user
        )
        recon_qs = DashboardService._scope_recon_results(
            ReconciliationResult.objects.filter(created_at__gte=since), user
        )
        exc_qs = DashboardService._scope_exceptions(
            ReconciliationException.objects.filter(created_at__gte=since), user
        )
        invoices = dict(
            inv_qs
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .values_list("date", "count")
        )
        reconciled = dict(
            recon_qs
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .values_list("date", "count")
        )
        exceptions = dict(
            exc_qs
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .values_list("date", "count")
        )
        all_dates = sorted(set(list(invoices) + list(reconciled) + list(exceptions)))
        return [
            {
                "date": d,
                "invoices": invoices.get(d, 0),
                "reconciled": reconciled.get(d, 0),
                "exceptions": exceptions.get(d, 0),
            }
            for d in all_dates
        ]

    @staticmethod
    def get_recent_activity(limit: int = 20, user=None) -> List[Dict[str, Any]]:
        activities: List[Dict[str, Any]] = []

        inv_qs = DashboardService._scope_invoices(Invoice.objects.all(), user)
        for inv in inv_qs.order_by("-created_at")[:limit]:
            activities.append({
                "id": inv.pk,
                "entity_type": "Invoice",
                "description": f"Invoice {inv.invoice_number or '(no number)'} uploaded",
                "status": inv.status,
                "timestamp": inv.created_at,
            })

        recon_qs = DashboardService._scope_recon_results(ReconciliationResult.objects.all(), user)
        for r in recon_qs.order_by("-created_at")[:limit]:
            activities.append({
                "id": r.pk,
                "entity_type": "Reconciliation",
                "description": f"Result #{r.pk} — {r.match_status}",
                "status": r.match_status,
                "timestamp": r.created_at,
            })

        review_qs = DashboardService._scope_reviews(ReviewAssignment.objects.all(), user)
        for ra in review_qs.order_by("-created_at")[:limit]:
            activities.append({
                "id": ra.pk,
                "entity_type": "Review",
                "description": f"Review #{ra.pk} — {ra.status}",
                "status": ra.status,
                "timestamp": ra.created_at,
            })

        activities.sort(key=lambda x: x["timestamp"], reverse=True)
        return activities[:limit]
