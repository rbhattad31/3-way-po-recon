"""Template views for enterprise RBAC management screens.

URL prefix: /admin-console/
"""
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Count
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import ListView, DetailView, TemplateView

from apps.accounts.forms import (
    UserCreateForm, UserProfileForm, UserRoleAssignForm,
    UserPermissionOverrideForm, RoleForm,
)
from apps.accounts.models import User
from apps.accounts.rbac_models import (
    Role, Permission, RolePermission, UserRole, UserPermissionOverride,
)
from apps.accounts.rbac_services import RBACEventService
from apps.core.decorators import observed_action
from apps.core.permissions import PermissionRequiredMixin


# ============================================================================
# User Management
# ============================================================================

class UserListView(PermissionRequiredMixin, ListView):
    """Paginated, searchable user list for business admins."""

    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 25
    required_permission = "users.manage"

    def get_queryset(self):
        qs = User.objects.all().order_by("email")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(email__icontains=q) | Q(first_name__icontains=q) |
                Q(last_name__icontains=q) | Q(department__icontains=q)
            )
        role_filter = self.request.GET.get("role", "")
        if role_filter:
            qs = qs.filter(role=role_filter)
        dept_filter = self.request.GET.get("department", "")
        if dept_filter:
            qs = qs.filter(department=dept_filter)
        status_filter = self.request.GET.get("status", "")
        if status_filter == "active":
            qs = qs.filter(is_active=True)
        elif status_filter == "inactive":
            qs = qs.filter(is_active=False)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["roles"] = Role.objects.filter(is_active=True).order_by("rank")
        ctx["departments"] = (
            User.objects.exclude(department="")
            .values_list("department", flat=True)
            .distinct()
            .order_by("department")
        )
        ctx["q"] = self.request.GET.get("q", "")
        ctx["role_filter"] = self.request.GET.get("role", "")
        ctx["dept_filter"] = self.request.GET.get("department", "")
        ctx["status_filter"] = self.request.GET.get("status", "")
        return ctx


@method_decorator(observed_action("user_create", permission="users.manage", entity_type="User"), name="dispatch")
class UserCreateView(PermissionRequiredMixin, TemplateView):
    """Create a new user."""

    template_name = "accounts/user_create.html"
    required_permission = "users.manage"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = UserCreateForm()
        return ctx

    def post(self, request, *args, **kwargs):
        form = UserCreateForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
                # Assign initial role if selected
                initial_role = form.cleaned_data.get("initial_role")
                if initial_role:
                    user.role = initial_role.code
                    user.save(update_fields=["role"])
                    UserRole.objects.create(
                        user=user,
                        role=initial_role,
                        is_primary=True,
                        assigned_by=request.user,
                    )
                    RBACEventService.log_role_assigned(user, initial_role, request.user, is_primary=True)
                RBACEventService.log_user_created(user, request.user)
            messages.success(request, f"User '{user.email}' created.")
            return redirect("accounts:user_detail", pk=user.pk)
        return self.render_to_response({"form": form})


@method_decorator(observed_action("user_manage", permission="users.manage", entity_type="User"), name="dispatch")
class UserDetailView(PermissionRequiredMixin, DetailView):
    """User detail / edit screen with tabs: profile, roles, permissions, overrides."""

    model = User
    template_name = "accounts/user_detail.html"
    context_object_name = "target_user"
    required_permission = "users.manage"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.object
        now = timezone.now()

        ctx["profile_form"] = UserProfileForm(instance=user)
        ctx["role_assign_form"] = UserRoleAssignForm()
        ctx["override_form"] = UserPermissionOverrideForm()

        ctx["user_roles"] = (
            UserRole.objects.filter(user=user)
            .select_related("role", "assigned_by")
            .order_by("-is_primary", "role__rank")
        )
        ctx["overrides"] = (
            UserPermissionOverride.objects.filter(user=user)
            .select_related("permission", "assigned_by")
        )
        ctx["effective_permissions"] = sorted(user.get_effective_permissions())
        ctx["all_permissions"] = Permission.objects.filter(is_active=True).order_by("module", "action")

        # Group effective permissions by module
        perm_by_module = {}
        for code in ctx["effective_permissions"]:
            module = code.split(".")[0] if "." in code else "other"
            perm_by_module.setdefault(module, []).append(code)
        ctx["perm_by_module"] = perm_by_module

        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get("action", "")

        if action == "update_profile":
            return self._handle_profile_update(request)
        elif action == "assign_role":
            return self._handle_role_assign(request)
        elif action == "remove_role":
            return self._handle_role_remove(request)
        elif action == "set_primary":
            return self._handle_set_primary(request)
        elif action == "add_override":
            return self._handle_add_override(request)
        elif action == "remove_override":
            return self._handle_remove_override(request)
        elif action == "toggle_active":
            return self._handle_toggle_active(request)

        return redirect("accounts:user_detail", pk=self.object.pk)

    def _handle_profile_update(self, request):
        user = self.object
        old_values = {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "department": getattr(user, "department", ""),
        }
        old_active = user.is_active
        form = UserProfileForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            if old_active != user.is_active:
                RBACEventService.log_user_status_change(user, user.is_active, request.user)
            # Log profile field changes
            new_values = {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "department": getattr(user, "department", ""),
            }
            if old_values != new_values:
                RBACEventService.log_user_updated(user, old_values, request.user)
            messages.success(request, "Profile updated.")
        else:
            for err in form.errors.values():
                messages.error(request, err)
        return redirect("accounts:user_detail", pk=user.pk)

    def _handle_role_assign(self, request):
        user = self.object
        form = UserRoleAssignForm(request.POST)
        if form.is_valid():
            role = form.cleaned_data["role"]
            is_primary = form.cleaned_data["is_primary"]
            expires_at = form.cleaned_data.get("expires_at")

            with transaction.atomic():
                user_role, created = UserRole.objects.update_or_create(
                    user=user, role=role,
                    defaults={
                        "is_active": True,
                        "assigned_by": request.user,
                        "expires_at": expires_at,
                    },
                )
                if is_primary:
                    old_code = user.role
                    UserRole.objects.filter(user=user, is_primary=True).exclude(pk=user_role.pk).update(is_primary=False)
                    user_role.is_primary = True
                    user_role.save(update_fields=["is_primary", "updated_at"])
                    user.role = role.code
                    user.save(update_fields=["role", "updated_at"])
                    user.clear_permission_cache()
                    if old_code != role.code:
                        RBACEventService.log_primary_role_changed(user, old_code, role.code, request.user)

                RBACEventService.log_role_assigned(user, role, request.user, is_primary)
            messages.success(request, f"Role '{role.code}' assigned.")
        else:
            for err in form.errors.values():
                messages.error(request, err)
        return redirect("accounts:user_detail", pk=user.pk)

    def _handle_role_remove(self, request):
        user = self.object
        ur_id = request.POST.get("user_role_id")
        ur = get_object_or_404(UserRole, id=ur_id, user=user)
        role = ur.role

        if role.code == "ADMIN":
            admin_count = UserRole.objects.filter(role__code="ADMIN", is_active=True).exclude(user=user).count()
            if admin_count == 0:
                messages.error(request, "Cannot remove the last Admin.")
                return redirect("accounts:user_detail", pk=user.pk)

        with transaction.atomic():
            ur.is_active = False
            ur.save(update_fields=["is_active", "updated_at"])
            user.clear_permission_cache()
            RBACEventService.log_role_removed(user, role, request.user)
            if ur.is_primary:
                next_ur = UserRole.objects.filter(user=user, is_active=True).select_related("role").order_by("role__rank").first()
                if next_ur:
                    next_ur.is_primary = True
                    next_ur.save(update_fields=["is_primary", "updated_at"])
                    user.role = next_ur.role.code
                    user.save(update_fields=["role", "updated_at"])

        messages.success(request, f"Role '{role.code}' removed.")
        return redirect("accounts:user_detail", pk=user.pk)

    def _handle_set_primary(self, request):
        user = self.object
        ur_id = request.POST.get("user_role_id")
        ur = get_object_or_404(UserRole, id=ur_id, user=user, is_active=True)

        old_code = user.role
        with transaction.atomic():
            UserRole.objects.filter(user=user, is_primary=True).update(is_primary=False)
            ur.is_primary = True
            ur.save(update_fields=["is_primary", "updated_at"])
            user.role = ur.role.code
            user.save(update_fields=["role", "updated_at"])
            user.clear_permission_cache()
            if old_code != ur.role.code:
                RBACEventService.log_primary_role_changed(user, old_code, ur.role.code, request.user)

        messages.success(request, f"Primary role set to '{ur.role.code}'.")
        return redirect("accounts:user_detail", pk=user.pk)

    def _handle_add_override(self, request):
        user = self.object
        form = UserPermissionOverrideForm(request.POST)
        if form.is_valid():
            permission = form.cleaned_data["permission"]
            override_type = form.cleaned_data["override_type"]
            with transaction.atomic():
                UserPermissionOverride.objects.update_or_create(
                    user=user, permission=permission,
                    defaults={
                        "override_type": override_type,
                        "reason": form.cleaned_data.get("reason", ""),
                        "assigned_by": request.user,
                        "expires_at": form.cleaned_data.get("expires_at"),
                        "is_active": True,
                    },
                )
                user.clear_permission_cache()
                RBACEventService.log_user_permission_override(
                    user, permission.code, override_type, request.user,
                    form.cleaned_data.get("reason", ""),
                )
            messages.success(request, f"Override '{override_type}' added for '{permission.code}'.")
        else:
            for err in form.errors.values():
                messages.error(request, err)
        return redirect("accounts:user_detail", pk=user.pk)

    def _handle_remove_override(self, request):
        user = self.object
        ov_id = request.POST.get("override_id")
        ov = get_object_or_404(UserPermissionOverride, id=ov_id, user=user)
        perm_code = ov.permission.code
        ov.is_active = False
        ov.save(update_fields=["is_active", "updated_at"])
        user.clear_permission_cache()
        RBACEventService.log_override_removed(user, perm_code, request.user)
        messages.success(request, "Override removed.")
        return redirect("accounts:user_detail", pk=user.pk)

    def _handle_toggle_active(self, request):
        user = self.object
        # Prevent self-deactivation
        if user.pk == request.user.pk:
            messages.error(request, "You cannot deactivate your own account.")
            return redirect("accounts:user_detail", pk=user.pk)
        user.is_active = not user.is_active
        user.save(update_fields=["is_active", "updated_at"])
        RBACEventService.log_user_status_change(user, user.is_active, request.user)
        status_text = "activated" if user.is_active else "deactivated"
        messages.success(request, f"User {status_text}.")
        return redirect("accounts:user_detail", pk=user.pk)


# ============================================================================
# Role Management
# ============================================================================

class RoleListView(PermissionRequiredMixin, ListView):
    """Role list for business admins."""

    model = Role
    template_name = "accounts/role_list.html"
    context_object_name = "roles"
    required_permission = "roles.manage"

    def get_queryset(self):
        qs = Role.objects.annotate(
            active_user_count=Count("user_roles", filter=Q(user_roles__is_active=True))
        ).order_by("rank", "code")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))
        return qs


@method_decorator(observed_action("role_manage", permission="roles.manage", entity_type="Role"), name="dispatch")
class RoleDetailView(PermissionRequiredMixin, DetailView):
    """Role detail / edit screen with permission management."""

    model = Role
    template_name = "accounts/role_detail.html"
    context_object_name = "role"
    required_permission = "roles.manage"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        role = self.object
        ctx["form"] = RoleForm(instance=role)

        # Permissions grouped by module
        all_perms = Permission.objects.filter(is_active=True).order_by("module", "action")
        granted_ids = set(
            RolePermission.objects.filter(role=role, is_allowed=True)
            .values_list("permission_id", flat=True)
        )
        modules = {}
        for perm in all_perms:
            modules.setdefault(perm.module, []).append({
                "perm": perm,
                "granted": perm.id in granted_ids,
            })
        ctx["modules"] = modules
        ctx["granted_count"] = len(granted_ids)
        ctx["total_count"] = all_perms.count()

        # Users with this role
        ctx["role_users"] = (
            UserRole.objects.filter(role=role, is_active=True)
            .select_related("user")
            .order_by("user__email")
        )
        return ctx

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get("action", "")

        if action == "update_role":
            return self._handle_update(request)
        elif action == "update_permissions":
            return self._handle_permissions_update(request)

        return redirect("accounts:role_detail", pk=self.object.pk)

    def _handle_update(self, request):
        role = self.object
        old_values = {"name": role.name, "description": role.description}
        form = RoleForm(request.POST, instance=role)
        if form.is_valid():
            form.save()
            RBACEventService.log_role_updated(role, old_values, request.user)
            messages.success(request, "Role updated.")
        else:
            for err in form.errors.values():
                messages.error(request, err)
        return redirect("accounts:role_detail", pk=role.pk)

    def _handle_permissions_update(self, request):
        role = self.object
        selected_perm_ids = set(map(int, request.POST.getlist("permissions", [])))
        all_perms = Permission.objects.filter(is_active=True)
        all_perm_ids = set(all_perms.values_list("id", flat=True))

        current_granted_ids = set(
            RolePermission.objects.filter(role=role, is_allowed=True)
            .values_list("permission_id", flat=True)
        )

        to_add = selected_perm_ids - current_granted_ids
        to_remove = current_granted_ids - selected_perm_ids

        with transaction.atomic():
            for pid in to_add:
                if pid in all_perm_ids:
                    RolePermission.objects.update_or_create(
                        role=role, permission_id=pid,
                        defaults={"is_allowed": True},
                    )
            RolePermission.objects.filter(role=role, permission_id__in=to_remove).delete()

            added_codes = list(Permission.objects.filter(id__in=to_add).values_list("code", flat=True))
            removed_codes = list(Permission.objects.filter(id__in=to_remove).values_list("code", flat=True))
            if added_codes or removed_codes:
                RBACEventService.log_role_permission_changed(role, added_codes, removed_codes, request.user)

        messages.success(request, f"Permissions updated: {len(to_add)} added, {len(to_remove)} removed.")
        return redirect("accounts:role_detail", pk=role.pk)


@method_decorator(observed_action("role_create", permission="roles.manage", entity_type="Role"), name="dispatch")
class RoleCreateView(PermissionRequiredMixin, TemplateView):
    """Create a new role."""

    template_name = "accounts/role_create.html"
    required_permission = "roles.manage"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = RoleForm()
        return ctx

    def post(self, request, *args, **kwargs):
        form = RoleForm(request.POST)
        if form.is_valid():
            role = form.save()
            RBACEventService.log_role_created(role, request.user)
            messages.success(request, f"Role '{role.code}' created.")
            return redirect("accounts:role_detail", pk=role.pk)
        return self.render_to_response({"form": form})


# ============================================================================
# Permission Catalog
# ============================================================================

class PermissionListView(PermissionRequiredMixin, ListView):
    """Permission catalog grouped by module."""

    model = Permission
    template_name = "accounts/permission_list.html"
    context_object_name = "permissions"
    required_permission = "roles.manage"

    def get_queryset(self):
        qs = Permission.objects.filter(is_active=True).order_by("module", "action")
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        permissions = ctx["permissions"]
        modules = {}
        for perm in permissions:
            modules.setdefault(perm.module, []).append(perm)
        ctx["modules"] = modules

        # For each permission, show which roles grant it
        perm_roles = {}
        for rp in RolePermission.objects.filter(is_allowed=True).select_related("role", "permission"):
            perm_roles.setdefault(rp.permission_id, []).append(rp.role)
        ctx["perm_roles"] = perm_roles
        return ctx


# ============================================================================
# Role-Permission Matrix
# ============================================================================

@method_decorator(observed_action("role_permission_matrix", permission="roles.manage", entity_type="Role"), name="dispatch")
class RolePermissionMatrixView(PermissionRequiredMixin, TemplateView):
    """Full role-permission matrix with bulk edit capability."""

    template_name = "accounts/role_matrix.html"
    required_permission = "roles.manage"

    def get_context_data(self, **kwargs):
        import json
        ctx = super().get_context_data(**kwargs)
        roles = Role.objects.filter(is_active=True).order_by("rank")
        permissions = Permission.objects.filter(is_active=True).order_by("module", "action")

        # Build lookup: (role_id, perm_id) -> True
        granted = set()
        for rp in RolePermission.objects.filter(is_allowed=True):
            granted.add((rp.role_id, rp.permission_id))

        # Group permissions by module
        modules = {}
        for perm in permissions:
            modules.setdefault(perm.module, []).append(perm)

        # JSON list of "role_id,perm_id" strings for JS pre-check
        granted_js = json.dumps([f"{r},{p}" for r, p in granted])

        ctx["roles"] = roles
        ctx["modules"] = modules
        ctx["granted"] = granted
        ctx["granted_js"] = granted_js
        return ctx

    def post(self, request, *args, **kwargs):
        roles = Role.objects.filter(is_active=True)
        permissions = Permission.objects.filter(is_active=True)

        perm_ids = {p.id for p in permissions}
        role_ids = {r.id for r in roles}

        current_granted = set()
        for rp in RolePermission.objects.filter(is_allowed=True):
            current_granted.add((rp.role_id, rp.permission_id))

        new_granted = set()
        for key in request.POST:
            if key.startswith("perm_"):
                parts = key.split("_")
                if len(parts) == 3:
                    try:
                        r_id = int(parts[1])
                        p_id = int(parts[2])
                        if r_id in role_ids and p_id in perm_ids:
                            new_granted.add((r_id, p_id))
                    except (ValueError, IndexError):
                        pass

        to_add = new_granted - current_granted
        to_remove = current_granted - new_granted

        changes_by_role = {}

        with transaction.atomic():
            for r_id, p_id in to_add:
                RolePermission.objects.update_or_create(
                    role_id=r_id, permission_id=p_id,
                    defaults={"is_allowed": True},
                )
                changes_by_role.setdefault(r_id, {"added": [], "removed": []})
                perm = Permission.objects.filter(id=p_id).first()
                if perm:
                    changes_by_role[r_id]["added"].append(perm.code)

            for r_id, p_id in to_remove:
                RolePermission.objects.filter(role_id=r_id, permission_id=p_id).delete()
                changes_by_role.setdefault(r_id, {"added": [], "removed": []})
                perm = Permission.objects.filter(id=p_id).first()
                if perm:
                    changes_by_role[r_id]["removed"].append(perm.code)

            # Audit
            for r_id, changes in changes_by_role.items():
                role = Role.objects.filter(id=r_id).first()
                if role:
                    RBACEventService.log_role_permission_changed(
                        role, changes["added"], changes["removed"], request.user,
                    )

        messages.success(request, f"Matrix updated: {len(to_add)} granted, {len(to_remove)} revoked.")
        return redirect("accounts:role_matrix")
