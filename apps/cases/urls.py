"""URL configuration for the cases app (template views)."""

from django.urls import path

from apps.cases import template_views

app_name = "cases"

# Template view URLs — used by /cases/
urlpatterns = [
    path("", template_views.case_inbox, name="case_inbox"),
    path("<int:pk>/", template_views.case_console, name="case_console"),
    path("<int:pk>/reprocess/", template_views.reprocess_case, name="reprocess_case"),
    path("create-for-invoice/<int:invoice_pk>/", template_views.create_case_for_invoice, name="create_case_for_invoice"),
]
