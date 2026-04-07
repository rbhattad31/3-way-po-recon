from django.urls import path

from apps.documents.template_views import po_create, po_detail, po_edit, po_list

urlpatterns = [
    path("", po_list, name="po_list"),
    path("add/", po_create, name="po_create"),
    path("<int:pk>/", po_detail, name="po_detail"),
    path("<int:pk>/edit/", po_edit, name="po_edit"),
]
