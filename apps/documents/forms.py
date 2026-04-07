"""Django forms for PurchaseOrder and GoodsReceiptNote management screens."""
from django import forms
from django.forms import inlineformset_factory

from apps.documents.models import GoodsReceiptNote, PurchaseOrder, PurchaseOrderLineItem
from apps.vendors.models import Vendor

PO_STATUS_CHOICES = [
    ("OPEN", "Open"),
    ("CLOSED", "Closed"),
    ("PARTIALLY_RECEIVED", "Partially Received"),
    ("CANCELLED", "Cancelled"),
]

GRN_STATUS_CHOICES = [
    ("RECEIVED", "Received"),
    ("PARTIAL", "Partial"),
    ("PENDING", "Pending"),
    ("REJECTED", "Rejected"),
]


class PurchaseOrderForm(forms.ModelForm):
    """Create or edit a Purchase Order."""

    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.none(),
        required=False,
        empty_label="— Select Vendor —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["vendor"].queryset = Vendor.objects.order_by("name")

    class Meta:
        model = PurchaseOrder
        fields = [
            "po_number", "vendor", "po_date", "currency",
            "total_amount", "tax_amount", "status",
            "buyer_name", "department", "notes",
        ]
        widgets = {
            "po_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. PO-2026-001"}),
            "po_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "currency": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. USD"}),
            "total_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "0.00"}),
            "tax_amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "placeholder": "0.00"}),
            "status": forms.Select(
                choices=PO_STATUS_CHOICES,
                attrs={"class": "form-select"},
            ),
            "buyer_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Buyer name"}),
            "department": forms.TextInput(attrs={"class": "form-control", "placeholder": "Requesting department"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Additional notes"}),
        }
        labels = {
            "po_number": "PO Number",
            "po_date": "PO Date",
            "total_amount": "Total Amount",
            "tax_amount": "Tax Amount",
            "buyer_name": "Buyer Name",
        }


class PurchaseOrderLineItemForm(forms.ModelForm):
    """Single PO line item row — used inside the inline formset."""

    class Meta:
        model = PurchaseOrderLineItem
        fields = [
            "line_number", "item_code", "description",
            "quantity", "unit_price", "unit_of_measure",
            "tax_amount", "line_amount",
        ]
        widgets = {
            "line_number": forms.NumberInput(attrs={
                "class": "form-control form-control-sm text-center", "min": "1", "style": "width:56px",
            }),
            "item_code": forms.TextInput(attrs={
                "class": "form-control form-control-sm", "placeholder": "SKU / Code",
            }),
            "description": forms.TextInput(attrs={
                "class": "form-control form-control-sm", "placeholder": "Item description",
            }),
            "quantity": forms.NumberInput(attrs={
                "class": "form-control form-control-sm line-qty text-end", "step": "0.0001", "min": "0", "placeholder": "0",
            }),
            "unit_price": forms.NumberInput(attrs={
                "class": "form-control form-control-sm line-price text-end", "step": "0.0001", "min": "0", "placeholder": "0.00",
            }),
            "unit_of_measure": forms.TextInput(attrs={
                "class": "form-control form-control-sm text-center", "placeholder": "EA", "style": "width:56px",
            }),
            "tax_amount": forms.NumberInput(attrs={
                "class": "form-control form-control-sm text-end", "step": "0.01", "min": "0", "placeholder": "0.00",
            }),
            "line_amount": forms.NumberInput(attrs={
                "class": "form-control form-control-sm line-amount text-end", "step": "0.01", "placeholder": "0.00",
            }),
        }


# Inline formset: one PO → many PurchaseOrderLineItem rows
POLineItemFormSet = inlineformset_factory(
    PurchaseOrder,
    PurchaseOrderLineItem,
    form=PurchaseOrderLineItemForm,
    extra=1,
    can_delete=True,
    min_num=0,
)


class GoodsReceiptNoteForm(forms.ModelForm):
    """Create or edit a Goods Receipt Note."""

    purchase_order = forms.ModelChoiceField(
        queryset=PurchaseOrder.objects.none(),
        empty_label="— Select Purchase Order —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.none(),
        required=False,
        empty_label="— Select Vendor —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["purchase_order"].queryset = PurchaseOrder.objects.order_by("-po_date")
        self.fields["vendor"].queryset = Vendor.objects.order_by("name")

    class Meta:
        model = GoodsReceiptNote
        fields = [
            "grn_number", "purchase_order", "vendor",
            "receipt_date", "status", "warehouse", "receiver_name",
        ]
        widgets = {
            "grn_number": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. GRN-2026-001"}),
            "receipt_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "status": forms.Select(
                choices=GRN_STATUS_CHOICES,
                attrs={"class": "form-select"},
            ),
            "warehouse": forms.TextInput(attrs={"class": "form-control", "placeholder": "Warehouse location"}),
            "receiver_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Receiver's name"}),
        }
        labels = {
            "grn_number": "GRN Number",
            "receipt_date": "Receipt Date",
            "receiver_name": "Receiver Name",
        }
