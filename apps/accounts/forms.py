"""Django forms for RBAC management screens."""
from django import forms
from django.core.exceptions import ValidationError

from apps.accounts.models import User, CompanyProfile
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
    company = forms.ModelChoiceField(
        queryset=CompanyProfile.objects.filter(is_active=True).order_by("name"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Assign user to a company (tenant)",
    )
    initial_role = forms.ModelChoiceField(
        queryset=Role.objects.filter(is_active=True).order_by("rank"),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Optionally assign a role on creation",
    )

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "department", "company"]
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
    """Edit user profile (name, department, company, active status)."""

    class Meta:
        model = User
        fields = ["first_name", "last_name", "department", "company", "is_active"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "department": forms.TextInput(attrs={"class": "form-control"}),
            "company": forms.Select(attrs={"class": "form-select"}),
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


class CompanyProfileForm(forms.ModelForm):
    """Create or edit a company profile."""

    class Meta:
        model = CompanyProfile
        fields = [
            "name", "legal_name", "tax_id", "country",
            "state_code", "address", "currency", "website",
            "is_default", "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "legal_name": forms.TextInput(attrs={"class": "form-control"}),
            "tax_id": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. 27AABCU9603R1ZM"}),
            "country": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. IN, AE, US", "maxlength": "10"}),
            "state_code": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. 27 (Maharashtra)"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "currency": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. INR"}),
            "website": forms.URLInput(attrs={"class": "form-control", "placeholder": "https://..."}),
            "is_default": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class TenantProfileForm(forms.ModelForm):
    """Edit the tenant's own CompanyProfile."""

    class Meta:
        model = CompanyProfile
        fields = ["name", "legal_name", "tax_id", "country", "state_code",
                  "address", "currency", "timezone"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "legal_name": forms.TextInput(attrs={"class": "form-control"}),
            "tax_id": forms.TextInput(attrs={"class": "form-control"}),
            "country": forms.TextInput(attrs={"class": "form-control", "placeholder": "IN, AE, US ..."}),
            "state_code": forms.TextInput(attrs={"class": "form-control"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "currency": forms.TextInput(attrs={"class": "form-control"}),
            "timezone": forms.TextInput(attrs={"class": "form-control", "placeholder": "Asia/Kolkata"}),
        }


class InviteUserForm(forms.Form):
    """Invite a new user to the current tenant."""

    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"class": "form-control", "placeholder": "user@example.com"})
    )
    role_code = forms.ChoiceField(
        choices=[
            ("AP_PROCESSOR", "AP Processor"),
            ("REVIEWER", "Reviewer"),
            ("FINANCE_MANAGER", "Finance Manager"),
            ("AUDITOR", "Auditor"),
            ("ADMIN", "Admin"),
        ],
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        from apps.accounts.models import User
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class AcceptInvitationForm(forms.Form):
    """Form for an invited user to complete their registration."""

    first_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    last_name = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"})
    )
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"})
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("confirm_password"):
            raise forms.ValidationError("Passwords do not match.")
        return cleaned
