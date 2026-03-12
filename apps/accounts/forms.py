"""Django forms for RBAC management screens."""
from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.accounts.rbac_models import Role, Permission, UserRole, UserPermissionOverride


class UserCreateForm(forms.ModelForm):
    """Create a new user with email, name, role, and initial password."""

    password1 = forms.CharField(
        label="Password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm Password",
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
    )
    initial_role = forms.ModelChoiceField(
        queryset=Role.objects.filter(is_active=True).order_by("rank"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Optionally assign a role on creation",
    )

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "department"]
        widgets = {
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "department": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise ValidationError("Passwords do not match.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


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
