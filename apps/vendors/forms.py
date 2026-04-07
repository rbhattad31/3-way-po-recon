"""Django forms for Vendor management screens."""
from django import forms

from apps.vendors.models import Vendor


class VendorForm(forms.ModelForm):
    """Create or edit a Vendor record."""

    class Meta:
        model = Vendor
        fields = [
            "code", "name", "tax_id", "country", "currency",
            "payment_terms", "contact_email", "address",
        ]
        widgets = {
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. VEND-001"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Vendor full name"}),
            "tax_id": forms.TextInput(attrs={"class": "form-control", "placeholder": "GSTIN / VAT / Tax ID"}),
            "country": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. India"}),
            "currency": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. USD"}),
            "payment_terms": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Net 30"}),
            "contact_email": forms.EmailInput(attrs={"class": "form-control", "placeholder": "vendor@example.com"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Registered address"}),
        }
        labels = {
            "code": "Vendor Code",
            "tax_id": "Tax ID / GSTIN",
        }
