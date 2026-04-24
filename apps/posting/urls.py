"""Template URL routes for posting app."""
from django.urls import path

from apps.posting.template_views import (
    posting_approve,
    posting_detail,
    posting_reject,
    posting_retry,
    posting_submit,
    posting_workbench,
    reference_import_list,
    trigger_direct_erp_import,
)

app_name = "posting"

urlpatterns = [
    path("", posting_workbench, name="posting-workbench"),
    path("<int:pk>/", posting_detail, name="posting-detail"),
    path("<int:pk>/approve/", posting_approve, name="posting-approve"),
    path("<int:pk>/reject/", posting_reject, name="posting-reject"),
    path("<int:pk>/submit/", posting_submit, name="posting-submit"),
    path("<int:pk>/retry/", posting_retry, name="posting-retry"),
    path("imports/", reference_import_list, name="posting-imports"),
    path("imports/trigger-direct/", trigger_direct_erp_import, name="posting-trigger-direct-import"),
]
