"""URL configuration for the cases app (template views)."""

from django.urls import path

from apps.cases import template_views

app_name = "cases"

# Template view URLs — used by /cases/
urlpatterns = [
    path("", template_views.case_inbox, name="case_inbox"),
    path("<int:pk>/", template_views.case_console, name="case_console"),
    path("<int:pk>/agent/", template_views.case_agent_view, name="case_agent_view"),
    path("<int:pk>/decide/", template_views.case_decide, name="case_decide"),
    path("<int:pk>/comment/", template_views.case_add_comment, name="case_add_comment"),
    path("<int:pk>/assign/", template_views.case_assign, name="case_assign"),
    path("<int:pk>/reprocess/", template_views.reprocess_case, name="reprocess_case"),
    path("create-for-invoice/<int:invoice_pk>/", template_views.create_case_for_invoice, name="create_case_for_invoice"),
]
