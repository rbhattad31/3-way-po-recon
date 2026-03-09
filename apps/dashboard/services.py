"""Dashboard analytics service — aggregation queries for the dashboard."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from django.db.models import Avg, Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.agents.models import AgentRun
from apps.core.enums import AgentRunStatus, MatchStatus, ReviewStatus
from apps.documents.models import GoodsReceiptNote, Invoice, PurchaseOrder
from apps.reconciliation.models import ReconciliationException, ReconciliationResult
from apps.reviews.models import ReviewAssignment
from apps.vendors.models import Vendor


class DashboardService:
    """Read-only aggregation service for the main dashboard."""

    @staticmethod
    def get_summary() -> Dict[str, Any]:
        total_results = ReconciliationResult.objects.count()
        matched = ReconciliationResult.objects.filter(match_status=MatchStatus.MATCHED).count()
        avg_conf = ReconciliationResult.objects.aggregate(
            avg=Avg("deterministic_confidence")
        )["avg"] or 0.0

        return {
            "total_invoices": Invoice.objects.count(),
            "total_pos": PurchaseOrder.objects.count(),
            "total_grns": GoodsReceiptNote.objects.count(),
            "total_vendors": Vendor.objects.filter(is_active=True).count(),
            "pending_reviews": ReviewAssignment.objects.filter(
                status__in=[ReviewStatus.PENDING, ReviewStatus.ASSIGNED, ReviewStatus.IN_REVIEW]
            ).count(),
            "open_exceptions": ReconciliationException.objects.filter(resolved=False).count(),
            "matched_pct": round((matched / total_results * 100) if total_results else 0, 1),
            "avg_confidence": round(avg_conf * 100, 1),
        }

    @staticmethod
    def get_match_status_breakdown() -> List[Dict[str, Any]]:
        total = ReconciliationResult.objects.count() or 1
        qs = (
            ReconciliationResult.objects
            .values("match_status")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        return [
            {
                "match_status": row["match_status"],
                "count": row["count"],
                "percentage": round(row["count"] / total * 100, 1),
            }
            for row in qs
        ]

    @staticmethod
    def get_exception_breakdown() -> List[Dict[str, Any]]:
        return list(
            ReconciliationException.objects.filter(resolved=False)
            .values("exception_type")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

    @staticmethod
    def get_agent_performance() -> List[Dict[str, Any]]:
        return list(
            AgentRun.objects
            .values("agent_type")
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
    def get_daily_volume(days: int = 30) -> List[Dict[str, Any]]:
        since = timezone.now() - timedelta(days=days)
        invoices = dict(
            Invoice.objects.filter(created_at__gte=since)
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .values_list("date", "count")
        )
        reconciled = dict(
            ReconciliationResult.objects.filter(created_at__gte=since)
            .annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(count=Count("id"))
            .values_list("date", "count")
        )
        exceptions = dict(
            ReconciliationException.objects.filter(created_at__gte=since)
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
    def get_recent_activity(limit: int = 20) -> List[Dict[str, Any]]:
        activities: List[Dict[str, Any]] = []

        for inv in Invoice.objects.order_by("-created_at")[:limit]:
            activities.append({
                "id": inv.pk,
                "entity_type": "Invoice",
                "description": f"Invoice {inv.invoice_number or '(no number)'} uploaded",
                "status": inv.status,
                "timestamp": inv.created_at,
            })

        for r in ReconciliationResult.objects.order_by("-created_at")[:limit]:
            activities.append({
                "id": r.pk,
                "entity_type": "Reconciliation",
                "description": f"Result #{r.pk} — {r.match_status}",
                "status": r.match_status,
                "timestamp": r.created_at,
            })

        for ra in ReviewAssignment.objects.order_by("-created_at")[:limit]:
            activities.append({
                "id": ra.pk,
                "entity_type": "Review",
                "description": f"Review #{ra.pk} — {ra.status}",
                "status": ra.status,
                "timestamp": ra.created_at,
            })

        activities.sort(key=lambda x: x["timestamp"], reverse=True)
        return activities[:limit]
