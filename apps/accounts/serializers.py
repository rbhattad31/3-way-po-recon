"""DRF serializers for RBAC management APIs."""
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.rbac_models import (
    Role, Permission, RolePermission, UserRole, UserPermissionOverride,
)


# ============================================================================
# Permission serializers
# ============================================================================

class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "name", "module", "action", "description", "is_active"]
        read_only_fields = ["id"]


class PermissionBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "name", "module"]


# ============================================================================
# Role serializers
# ============================================================================

class RoleListSerializer(serializers.ModelSerializer):
    user_count = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ["id", "code", "name", "description", "is_system_role", "is_active", "rank", "user_count"]
        read_only_fields = ["id"]

    def get_user_count(self, obj):
        return obj.user_roles.filter(is_active=True).count()


class RoleDetailSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    user_count = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = [
            "id", "code", "name", "description", "is_system_role",
            "is_active", "rank", "permissions", "user_count",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_permissions(self, obj):
        rps = (
            RolePermission.objects
            .filter(role=obj, is_allowed=True)
            .select_related("permission")
        )
        return PermissionBriefSerializer(
            [rp.permission for rp in rps], many=True
        ).data

    def get_user_count(self, obj):
        return obj.user_roles.filter(is_active=True).count()


class RoleCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["code", "name", "description", "is_active", "rank"]

    def validate_code(self, value):
        value = value.upper().strip()
        instance = self.instance
        if instance and instance.is_system_role and instance.code != value:
            raise serializers.ValidationError("Cannot change code of a system role.")
        return value


# ============================================================================
# Role-Permission matrix serializers
# ============================================================================

class RolePermissionMatrixEntrySerializer(serializers.Serializer):
    role_id = serializers.IntegerField()
    permission_id = serializers.IntegerField()
    is_allowed = serializers.BooleanField()


class RolePermissionMatrixUpdateSerializer(serializers.Serializer):
    entries = RolePermissionMatrixEntrySerializer(many=True)


# ============================================================================
# UserRole serializers
# ============================================================================

class UserRoleSerializer(serializers.ModelSerializer):
    role_code = serializers.CharField(source="role.code", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True)
    assigned_by_email = serializers.EmailField(source="assigned_by.email", read_only=True, default=None)

    class Meta:
        model = UserRole
        fields = [
            "id", "role", "role_code", "role_name", "is_primary",
            "assigned_by", "assigned_by_email", "assigned_at",
            "expires_at", "is_active",
        ]
        read_only_fields = ["id", "assigned_at"]


class UserRoleAssignSerializer(serializers.Serializer):
    role_id = serializers.IntegerField()
    is_primary = serializers.BooleanField(default=False)
    expires_at = serializers.DateTimeField(required=False, allow_null=True, default=None)

    def validate_role_id(self, value):
        if not Role.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError("Role does not exist or is inactive.")
        return value

    def validate_expires_at(self, value):
        if value and value <= timezone.now():
            raise serializers.ValidationError("Expiry must be in the future.")
        return value


# ============================================================================
# UserPermissionOverride serializers
# ============================================================================

class UserPermissionOverrideSerializer(serializers.ModelSerializer):
    permission_code = serializers.CharField(source="permission.code", read_only=True)
    assigned_by_email = serializers.EmailField(source="assigned_by.email", read_only=True, default=None)

    class Meta:
        model = UserPermissionOverride
        fields = [
            "id", "permission", "permission_code", "override_type",
            "reason", "assigned_by", "assigned_by_email",
            "assigned_at", "expires_at", "is_active",
        ]
        read_only_fields = ["id", "assigned_at"]


class UserPermissionOverrideCreateSerializer(serializers.Serializer):
    permission_id = serializers.IntegerField()
    override_type = serializers.ChoiceField(choices=["ALLOW", "DENY"])
    reason = serializers.CharField(required=False, default="", allow_blank=True)
    expires_at = serializers.DateTimeField(required=False, allow_null=True, default=None)

    def validate_permission_id(self, value):
        if not Permission.objects.filter(id=value, is_active=True).exists():
            raise serializers.ValidationError("Permission does not exist or is inactive.")
        return value


# ============================================================================
# User serializers (RBAC-enhanced)
# ============================================================================

class UserListSerializer(serializers.ModelSerializer):
    primary_role = serializers.SerializerMethodField()
    all_roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "department",
            "role", "primary_role", "all_roles",
            "is_active", "is_staff", "last_login", "created_at",
        ]

    def get_primary_role(self, obj):
        role = obj.get_primary_role()
        if role:
            return {"id": role.id, "code": role.code, "name": role.name}
        return {"code": obj.role, "name": obj.get_role_display()}

    def get_all_roles(self, obj):
        roles = obj.get_all_roles()
        return [{"id": r.id, "code": r.code, "name": r.name} for r in roles]


class UserDetailSerializer(serializers.ModelSerializer):
    primary_role = serializers.SerializerMethodField()
    all_roles = UserRoleSerializer(source="user_roles", many=True, read_only=True)
    effective_permissions = serializers.SerializerMethodField()
    permission_overrides = UserPermissionOverrideSerializer(
        source="permission_overrides", many=True, read_only=True,
    )

    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "department",
            "role", "primary_role", "all_roles",
            "effective_permissions", "permission_overrides",
            "is_active", "is_staff", "last_login",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "email", "created_at", "updated_at", "last_login"]

    def get_primary_role(self, obj):
        role = obj.get_primary_role()
        if role:
            return {"id": role.id, "code": role.code, "name": role.name}
        return {"code": obj.role, "name": obj.get_role_display()}

    def get_effective_permissions(self, obj):
        perms = obj.get_effective_permissions()
        return sorted(perms)


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "department", "is_active"]
