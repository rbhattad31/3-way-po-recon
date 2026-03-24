"""Forms for credit management admin screens."""
from django import forms


class CreditAdjustmentForm(forms.Form):
    """Admin form for adjusting a user's credit account."""

    ACTION_CHOICES = [
        ("add", "Add Credits"),
        ("subtract", "Subtract Credits"),
        ("set_limit", "Set Monthly Limit"),
        ("toggle_active", "Toggle Active Status"),
    ]

    action_type = forms.ChoiceField(choices=ACTION_CHOICES, widget=forms.Select(
        attrs={"class": "form-select form-select-sm"},
    ))
    credits = forms.IntegerField(
        min_value=0,
        required=False,
        widget=forms.NumberInput(attrs={
            "class": "form-control form-control-sm",
            "placeholder": "Number of credits",
        }),
    )
    monthly_limit = forms.IntegerField(
        min_value=0,
        required=False,
        widget=forms.NumberInput(attrs={
            "class": "form-control form-control-sm",
            "placeholder": "Monthly limit (0 = unlimited)",
        }),
    )
    is_active = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )
    remarks = forms.CharField(
        widget=forms.Textarea(attrs={
            "class": "form-control form-control-sm",
            "rows": 2,
            "placeholder": "Reason for this change (required)",
        }),
    )

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get("action_type")
        credits = cleaned.get("credits")
        remarks = cleaned.get("remarks", "").strip()

        if not remarks:
            raise forms.ValidationError("Remarks are required for all credit changes.")

        if action in ("add", "subtract") and (credits is None or credits <= 0):
            raise forms.ValidationError("Credits must be a positive number for add/subtract.")

        if action == "set_limit":
            if cleaned.get("monthly_limit") is None:
                raise forms.ValidationError("Monthly limit is required for set_limit action.")

        return cleaned
