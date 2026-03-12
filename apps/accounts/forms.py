"""Django forms for RBAC management screens."""
from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.rbac_models import Role, Permission, UserRole, UserPermissionOverride


class UserProfileForm(forms.ModelForm):
    """Edit user profile (name, department, active status)."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "department", "is_active"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "department": forms.TextInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class UserRoleAssignForm(forms.Form):
    """Assign a role to a user."""

    role = forms.ModelChoiceField(
        queryset=Role.objects.filter(is_active=True).order_by("rank"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    is_primary = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
        help_text="Leave empty for permanent assignment",
    )


class UserPermissionOverrideForm(forms.Form):
    """Add a permission override for a user."""

    permission = forms.ModelChoiceField(
        queryset=Permission.objects.filter(is_active=True).order_by("module", "action"),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    override_type = forms.ChoiceField(
        choices=[("ALLOW", "Allow"), ("DENY", "Deny")],
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    reason = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2}),
    )
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"class": "form-control", "type": "datetime-local"}),
    )


class RoleForm(forms.ModelForm):
    """Create or edit a role."""

    class Meta:
        model = Role
        fields = ["code", "name", "description", "is_active", "rank"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control", "style": "text-transform:uppercase"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "rank": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
        }

    def clean_code(self):
        code = self.cleaned_data["code"].upper().strip()
        if self.instance and self.instance.pk and self.instance.is_system_role:
            if self.instance.code != code:
                raise ValidationError("Cannot change the code of a system role.")
        return code
