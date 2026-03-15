"""DRF API views for RBAC management."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.rbac_models import (
    Role, Permission, RolePermission, UserRole, UserPermissionOverride,
)
from apps.accounts.rbac_services import RBACEventService
from apps.accounts.serializers import (
    UserListSerializer, UserDetailSerializer, UserUpdateSerializer,
    RoleListSerializer, RoleDetailSerializer, RoleCreateUpdateSerializer,
    PermissionSerializer,
    RolePermissionMatrixUpdateSerializer,
    UserRoleSerializer, UserRoleAssignSerializer,
    UserPermissionOverrideSerializer, UserPermissionOverrideCreateSerializer,
)
from apps.core.permissions import HasPermissionCode


# ============================================================================
# User management API
# ============================================================================

class UserViewSet(viewsets.ModelViewSet):
    """User management API — list, detail, update, role/override management."""

    queryset = User.objects.all().order_by("email")
    permission_classes = [IsAuthenticated, HasPermissionCode]
    required_permission = "users.manage"
    filterset_fields = ["is_active", "role", "department"]
    search_fields = ["email", "first_name", "last_name", "department"]
    ordering_fields = ["email", "created_at", "last_login"]

    def get_serializer_class(self):
        if self.action == "list":
            return UserListSerializer
        if self.action in ("update", "partial_update"):
            return UserUpdateSerializer
        return UserDetailSerializer

    def perform_create(self, serializer):
        user = serializer.save()
        RBACEventService.log_user_created(user, self.request.user)

    def perform_update(self, serializer):
        instance = serializer.instance
        old_active = instance.is_active
        old_values = {
            "first_name": instance.first_name,
            "last_name": instance.last_name,
            "department": getattr(instance, "department", ""),
        }
        user = serializer.save()
        if old_active != user.is_active:
            RBACEventService.log_user_status_change(
                user, user.is_active, self.request.user,
            )
        new_values = {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "department": getattr(user, "department", ""),
        }
        if old_values != new_values:
            RBACEventService.log_user_updated(user, old_values, self.request.user)

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"detail": "User deletion is not allowed. Deactivate instead."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # --- Role assignment sub-actions ---

    @action(detail=True, methods=["get"], url_path="roles")
    def list_roles(self, request, pk=None):
        user = self.get_object()
        user_roles = (
            UserRole.objects
            .filter(user=user)
            .select_related("role", "assigned_by")
            .order_by("-is_primary", "role__rank")
        )
        return Response(UserRoleSerializer(user_roles, many=True).data)

    @action(detail=True, methods=["post"], url_path="roles/assign")
    def assign_role(self, request, pk=None):
        user = self.get_object()
        serializer = UserRoleAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        role = get_object_or_404(Role, id=serializer.validated_data["role_id"])
        is_primary = serializer.validated_data["is_primary"]
        expires_at = serializer.validated_data.get("expires_at")

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
                old_primary_code = user.role
                UserRole.objects.filter(user=user, is_primary=True).exclude(pk=user_role.pk).update(is_primary=False)
                user_role.is_primary = True
                user_role.save(update_fields=["is_primary", "updated_at"])
                user.role = role.code
                user.save(update_fields=["role", "updated_at"])
                user.clear_permission_cache()
                if old_primary_code != role.code:
                    RBACEventService.log_primary_role_changed(user, old_primary_code, role.code, request.user)

            RBACEventService.log_role_assigned(user, role, request.user, is_primary)

        return Response(
            UserRoleSerializer(user_role).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="roles/remove")
    def remove_role(self, request, pk=None):
        user = self.get_object()
        role_id = request.data.get("role_id")
        if not role_id:
            return Response({"detail": "role_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        user_role = get_object_or_404(UserRole, user=user, role_id=role_id)
        role = user_role.role

        # Prevent removing last admin
        if role.code == "ADMIN":
            admin_count = UserRole.objects.filter(
                role__code="ADMIN", is_active=True
            ).exclude(user=user).count()
            if admin_count == 0:
                return Response(
                    {"detail": "Cannot remove the last Admin role assignment."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            user_role.is_active = False
            user_role.save(update_fields=["is_active", "updated_at"])
            user.clear_permission_cache()
            RBACEventService.log_role_removed(user, role, request.user)

            # If this was the primary role, assign next active role as primary
            if user_role.is_primary:
                next_role = (
                    UserRole.objects.filter(user=user, is_active=True)
                    .select_related("role")
                    .order_by("role__rank")
                    .first()
                )
                if next_role:
                    next_role.is_primary = True
                    next_role.save(update_fields=["is_primary", "updated_at"])
                    user.role = next_role.role.code
                    user.save(update_fields=["role", "updated_at"])

        return Response(status=status.HTTP_204_NO_CONTENT)

    # --- Permission override sub-actions ---

    @action(detail=True, methods=["get"], url_path="overrides")
    def list_overrides(self, request, pk=None):
        user = self.get_object()
        overrides = (
            UserPermissionOverride.objects
            .filter(user=user)
            .select_related("permission", "assigned_by")
        )
        return Response(UserPermissionOverrideSerializer(overrides, many=True).data)

    @action(detail=True, methods=["post"], url_path="overrides/create")
    def create_override(self, request, pk=None):
        user = self.get_object()
        serializer = UserPermissionOverrideCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        permission = get_object_or_404(Permission, id=serializer.validated_data["permission_id"])

        with transaction.atomic():
            override, _ = UserPermissionOverride.objects.update_or_create(
                user=user, permission=permission,
                defaults={
                    "override_type": serializer.validated_data["override_type"],
                    "reason": serializer.validated_data.get("reason", ""),
                    "assigned_by": request.user,
                    "expires_at": serializer.validated_data.get("expires_at"),
                    "is_active": True,
                },
            )
            user.clear_permission_cache()
            RBACEventService.log_user_permission_override(
                user, permission.code,
                serializer.validated_data["override_type"],
                request.user,
                serializer.validated_data.get("reason", ""),
            )

        return Response(UserPermissionOverrideSerializer(override).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="overrides/remove")
    def remove_override(self, request, pk=None):
        user = self.get_object()
        override_id = request.data.get("override_id")
        if not override_id:
            return Response({"detail": "override_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        override = get_object_or_404(UserPermissionOverride, id=override_id, user=user)
        perm_code = override.permission.code
        override.is_active = False
        override.save(update_fields=["is_active", "updated_at"])
        user.clear_permission_cache()
        RBACEventService.log_override_removed(user, perm_code, self.request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ============================================================================
# Role management API
# ============================================================================

class RoleViewSet(viewsets.ModelViewSet):
    """Role management API."""

    queryset = Role.objects.all().order_by("rank", "code")
    permission_classes = [IsAuthenticated, HasPermissionCode]
    required_permission = "roles.manage"
    filterset_fields = ["is_active", "is_system_role"]
    search_fields = ["code", "name"]

    def get_serializer_class(self):
        if self.action == "list":
            return RoleListSerializer
        if self.action in ("create", "update", "partial_update"):
            return RoleCreateUpdateSerializer
        return RoleDetailSerializer

    def perform_create(self, serializer):
        role = serializer.save()
        RBACEventService.log_role_created(role, self.request.user)

    def perform_update(self, serializer):
        old_values = {"name": serializer.instance.name, "description": serializer.instance.description}
        role = serializer.save()
        RBACEventService.log_role_updated(role, old_values, self.request.user)

    def destroy(self, request, *args, **kwargs):
        role = self.get_object()
        if role.is_system_role:
            return Response(
                {"detail": "Cannot delete a system role. Deactivate instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Soft-deactivate instead of hard delete
        role.is_active = False
        role.save(update_fields=["is_active", "updated_at"])
        RBACEventService.log_role_deactivated(role, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="clone")
    def clone_role(self, request, pk=None):
        source_role = self.get_object()
        new_code = request.data.get("code", f"{source_role.code}_COPY")
        new_name = request.data.get("name", f"{source_role.name} (Copy)")

        if Role.objects.filter(code=new_code).exists():
            return Response(
                {"detail": f"Role with code '{new_code}' already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            new_role = Role.objects.create(
                code=new_code.upper(),
                name=new_name,
                description=source_role.description,
                is_system_role=False,
                rank=source_role.rank + 1,
            )
            # Copy permissions
            source_perms = RolePermission.objects.filter(role=source_role, is_allowed=True)
            for rp in source_perms:
                RolePermission.objects.create(
                    role=new_role, permission=rp.permission, is_allowed=True,
                )
            RBACEventService.log_role_created(new_role, request.user)

        return Response(RoleDetailSerializer(new_role).data, status=status.HTTP_201_CREATED)


# ============================================================================
# Permission catalog API
# ============================================================================

class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only API for the permission catalog."""

    queryset = Permission.objects.filter(is_active=True).order_by("module", "action")
    serializer_class = PermissionSerializer
    permission_classes = [IsAuthenticated, HasPermissionCode]
    required_permission = "roles.manage"
    filterset_fields = ["module", "is_active"]
    search_fields = ["code", "name"]


# ============================================================================
# Role-Permission matrix API
# ============================================================================

class RolePermissionMatrixView(APIView):
    """GET: full matrix. PUT: bulk update matrix entries."""

    permission_classes = [IsAuthenticated, HasPermissionCode]
    required_permission = "roles.manage"

    def get(self, request):
        roles = Role.objects.filter(is_active=True).order_by("rank")
        permissions = Permission.objects.filter(is_active=True).order_by("module", "action")

        # Build matrix: {role_id: set(perm_ids)}
        existing = {}
        for rp in RolePermission.objects.filter(is_allowed=True).values_list("role_id", "permission_id"):
            existing.setdefault(rp[0], set()).add(rp[1])

        matrix = []
        for perm in permissions:
            row = {
                "permission_id": perm.id,
                "code": perm.code,
                "name": perm.name,
                "module": perm.module,
                "roles": {},
            }
            for role in roles:
                row["roles"][str(role.id)] = perm.id in existing.get(role.id, set())
            matrix.append(row)

        role_info = [{"id": r.id, "code": r.code, "name": r.name, "is_system_role": r.is_system_role} for r in roles]
        return Response({"roles": role_info, "matrix": matrix})

    @transaction.atomic
    def put(self, request):
        serializer = RolePermissionMatrixUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        changes_by_role = {}
        for entry in serializer.validated_data["entries"]:
            role_id = entry["role_id"]
            perm_id = entry["permission_id"]
            is_allowed = entry["is_allowed"]

            if is_allowed:
                RolePermission.objects.update_or_create(
                    role_id=role_id, permission_id=perm_id,
                    defaults={"is_allowed": True},
                )
            else:
                RolePermission.objects.filter(
                    role_id=role_id, permission_id=perm_id,
                ).delete()

            changes_by_role.setdefault(role_id, {"added": [], "removed": []})
            key = "added" if is_allowed else "removed"
            perm = Permission.objects.filter(id=perm_id).first()
            if perm:
                changes_by_role[role_id][key].append(perm.code)

        # Audit
        for role_id, changes in changes_by_role.items():
            role = Role.objects.filter(id=role_id).first()
            if role:
                RBACEventService.log_role_permission_changed(
                    role, changes["added"], changes["removed"], request.user,
                )

        return Response({"detail": "Matrix updated."})
