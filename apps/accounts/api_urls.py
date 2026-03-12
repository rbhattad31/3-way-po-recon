"""API URL routes for RBAC management — included under /api/v1/accounts/."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.accounts.views import (
    UserViewSet, RoleViewSet, PermissionViewSet, RolePermissionMatrixView,
)

router = DefaultRouter()
router.register(r"users", UserViewSet, basename="api-users")
router.register(r"roles", RoleViewSet, basename="api-roles")
router.register(r"permissions", PermissionViewSet, basename="api-permissions")

urlpatterns = [
    path("", include(router.urls)),
    path("role-matrix/", RolePermissionMatrixView.as_view(), name="api-role-matrix"),
]
