from django.urls import path

from apps.documents.template_views import (
    document_download,
    invoice_detail, invoice_list,
    pending_uploads_status,
    upload_invoice,
)

app_name = "documents"

urlpatterns = [
    path("", invoice_list, name="invoice_list"),
    path("upload/", upload_invoice, name="upload_invoice"),
    path("pending-uploads-status/", pending_uploads_status, name="pending_uploads_status"),
    path("<int:pk>/", invoice_detail, name="invoice_detail"),
    path("download/<int:pk>/", document_download, name="document_download"),
]
