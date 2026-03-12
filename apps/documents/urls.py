from django.urls import path

from apps.documents.template_views import document_download, grn_detail, grn_list, invoice_detail, invoice_list, po_detail, po_list, upload_invoice

app_name = "documents"

urlpatterns = [
    path("", invoice_list, name="invoice_list"),
    path("upload/", upload_invoice, name="upload_invoice"),
    path("<int:pk>/", invoice_detail, name="invoice_detail"),
    path("download/<int:pk>/", document_download, name="document_download"),
    path("purchase-orders/", po_list, name="po_list"),
    path("purchase-orders/<int:pk>/", po_detail, name="po_detail"),
    path("grns/", grn_list, name="grn_list"),
    path("grns/<int:pk>/", grn_detail, name="grn_detail"),
]
