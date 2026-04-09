from django.urls import path
from django.contrib.auth import views as auth_views

from apps.accounts.login_view import RateLimitedLoginView
from apps.accounts.template_views import (
    UserListView, UserCreateView, UserDetailView,
    RoleListView, RoleDetailView, RoleCreateView,
    PermissionListView,
    RolePermissionMatrixView,
    CompanyProfileListView, CompanyProfileDetailView,
    TenantProfileView, TenantUserListView, InviteUserView,
    TenantSettingsView, AcceptInvitationView,
)

app_name = "accounts"

urlpatterns = [
    # Auth — RateLimitedLoginView adds IP-based brute-force protection
    path("login/", RateLimitedLoginView.as_view(), name="login"),
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

    # Admin Console — Company Profile
    path("admin-console/company/", CompanyProfileListView.as_view(), name="company_list"),
    path("admin-console/company/<int:pk>/", CompanyProfileDetailView.as_view(), name="company_detail"),

    # Organisation self-service
    path("organisation/", TenantProfileView.as_view(), name="organisation_profile"),
    path("organisation/users/", TenantUserListView.as_view(), name="organisation_users"),
    path("organisation/invite/", InviteUserView.as_view(), name="invite_user"),
    path("organisation/settings/", TenantSettingsView.as_view(), name="organisation_settings"),
    # Accept invitation (exempt from LoginRequired — new users not yet logged in)
    path("invite/<str:token>/", AcceptInvitationView.as_view(), name="accept_invitation"),
]
