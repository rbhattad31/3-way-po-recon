"""Governance API views — dedicated endpoints for governance & access observability."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasAnyRole
from apps.dashboard.governance_dashboard_service import GovernanceDashboardService


def _get_gov_filters(request):
    """Extract governance filter params from query string."""
    return {
        "date_from": request.query_params.get("date_from"),
        "date_to": request.query_params.get("date_to"),
        "agent_type": request.query_params.get("agent_type"),
        "status": request.query_params.get("status"),
        "trace_id": request.query_params.get("trace_id"),
        "actor_role": request.query_params.get("actor_role"),
        "permission": request.query_params.get("permission"),
    }


class GovSummaryAPIView(APIView):
    permission_classes = [IsAuthenticated, HasAnyRole]
    allowed_roles = ["ADMIN", "AUDITOR"]

    def get(self, request):
        data = GovernanceDashboardService.get_governance_summary(
            filters=_get_gov_filters(request),
        )
        return Response(data)


class GovAccessEventsAPIView(APIView):
    permission_classes = [IsAuthenticated, HasAnyRole]
    allowed_roles = ["ADMIN", "AUDITOR"]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 50)), 100)
        data = GovernanceDashboardService.get_access_events(
            filters=_get_gov_filters(request), limit=limit,
        )
        return Response(data)


class GovPermissionActivityAPIView(APIView):
    permission_classes = [IsAuthenticated, HasAnyRole]
    allowed_roles = ["ADMIN", "AUDITOR"]

    def get(self, request):
        data = GovernanceDashboardService.get_permission_activity(
            filters=_get_gov_filters(request),
        )
        return Response(data)


class GovTraceRunsAPIView(APIView):
    permission_classes = [IsAuthenticated, HasAnyRole]
    allowed_roles = ["ADMIN", "AUDITOR"]

    def get(self, request):
        limit = min(int(request.query_params.get("limit", 50)), 100)
        data = GovernanceDashboardService.get_trace_runs(
            filters=_get_gov_filters(request), limit=limit,
        )
        return Response(data)


class GovTraceDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, HasAnyRole]
    allowed_roles = ["ADMIN", "AUDITOR"]

    def get(self, request, run_id):
        data = GovernanceDashboardService.get_trace_detail(run_id=run_id)
        if data is None:
            return Response({"detail": "Not found."}, status=404)
        return Response(data)


class GovHealthAPIView(APIView):
    permission_classes = [IsAuthenticated, HasAnyRole]
    allowed_roles = ["ADMIN", "AUDITOR"]

    def get(self, request):
        data = GovernanceDashboardService.get_governance_health(
            filters=_get_gov_filters(request),
        )
        return Response(data)
