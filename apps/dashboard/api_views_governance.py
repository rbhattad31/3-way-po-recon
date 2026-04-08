"""Agent Governance API views — JSON endpoints for the governance dashboard."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasAnyRole
from apps.dashboard.agent_governance_service import AgentGovernanceDashboardService


def _get_gov_filters(request):
    return {
        "date_from": request.query_params.get("date_from"),
        "date_to": request.query_params.get("date_to"),
        "agent_type": request.query_params.get("agent_type"),
        "status": request.query_params.get("status"),
        "trace_id": request.query_params.get("trace_id"),
        "actor_role": request.query_params.get("actor_role"),
        "permission": request.query_params.get("permission"),
    }


class _GovBaseView(APIView):
    """Base for governance views — ADMIN, AUDITOR get full access.

    FINANCE_MANAGER gets summary-level data (scrubbed on service layer).
    """
    permission_classes = [IsAuthenticated, HasAnyRole]
    allowed_roles = ["ADMIN", "AUDITOR", "FINANCE_MANAGER"]


class GovDashSummaryView(_GovBaseView):
    def get(self, request):
        data = AgentGovernanceDashboardService.get_summary(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class GovIdentityView(_GovBaseView):
    def get(self, request):
        data = AgentGovernanceDashboardService.get_execution_identity(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class GovAuthorizationView(_GovBaseView):
    def get(self, request):
        data = AgentGovernanceDashboardService.get_authorization_matrix(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class GovToolAuthorizationView(_GovBaseView):
    def get(self, request):
        data = AgentGovernanceDashboardService.get_tool_authorization(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class GovRecommendationsView(_GovBaseView):
    def get(self, request):
        data = AgentGovernanceDashboardService.get_recommendation_governance(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class GovProtectedActionsView(_GovBaseView):
    def get(self, request):
        data = AgentGovernanceDashboardService.get_protected_action_outcomes(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class GovDenialsView(_GovBaseView):
    def get(self, request):
        limit = min(int(request.query_params.get("limit", 50)), 100)
        data = AgentGovernanceDashboardService.get_denials(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None), limit=limit,
        )
        return Response(data)


class GovCoverageTrendView(_GovBaseView):
    def get(self, request):
        data = AgentGovernanceDashboardService.get_coverage_trend(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class GovSystemAgentView(_GovBaseView):
    """Dedicated panel — ADMIN/AUDITOR only."""
    allowed_roles = ["ADMIN", "AUDITOR"]

    def get(self, request):
        data = AgentGovernanceDashboardService.get_system_agent_oversight(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None),
        )
        return Response(data)


class GovTraceDetailView(_GovBaseView):
    def get(self, request, run_id):
        data = AgentGovernanceDashboardService.get_trace_detail(
            run_id=run_id, user=request.user, tenant=getattr(request, 'tenant', None),
        )
        if data is None:
            return Response({"detail": "Not found."}, status=404)
        return Response(data)


class GovTraceRunListView(_GovBaseView):
    def get(self, request):
        limit = min(int(request.query_params.get("limit", 50)), 100)
        data = AgentGovernanceDashboardService.get_trace_run_list(
            filters=_get_gov_filters(request), user=request.user, tenant=getattr(request, 'tenant', None), limit=limit,
        )
        return Response(data)
