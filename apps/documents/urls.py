from django.urls import path

from apps.documents.template_views import grn_list, invoice_detail, invoice_list, po_list, upload_invoice

app_name = "documents"

urlpatterns = [
    path("", invoice_list, name="invoice_list"),
    path("upload/", upload_invoice, name="upload_invoice"),
    path("<int:pk>/", invoice_detail, name="invoice_detail"),
    path("purchase-orders/", po_list, name="po_list"),
    path("grns/", grn_list, name="grn_list"),
]
