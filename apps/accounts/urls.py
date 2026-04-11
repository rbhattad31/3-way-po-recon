from django.urls import path
from django.contrib.auth import views as auth_views

from apps.accounts.template_views import (
    RoleBasedLoginView,
    UserListView, UserCreateView, UserDetailView,
    RoleListView, RoleDetailView, RoleCreateView,
    PermissionListView,
    RolePermissionMatrixView,
)

app_name = "accounts"

urlpatterns = [
    # Auth
    path("login/", RoleBasedLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # Admin Console — User Management
    path("admin-console/users/", UserListView.as_view(), name="user_list"),
    path("admin-console/users/new/", UserCreateView.as_view(), name="user_create"),
    path("admin-console/users/<int:pk>/", UserDetailView.as_view(), name="user_detail"),

    # Admin Console — Role Management
    path("admin-console/roles/", RoleListView.as_view(), name="role_list"),
    path("admin-console/roles/new/", RoleCreateView.as_view(), name="role_create"),
    path("admin-console/roles/<int:pk>/", RoleDetailView.as_view(), name="role_detail"),

    # Admin Console — Permission Catalog
    path("admin-console/permissions/", PermissionListView.as_view(), name="permission_list"),

    # Admin Console — Role-Permission Matrix
    path("admin-console/role-matrix/", RolePermissionMatrixView.as_view(), name="role_matrix"),
]
