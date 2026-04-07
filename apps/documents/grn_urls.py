from django.urls import path

from apps.documents.template_views import grn_create, grn_detail, grn_edit, grn_list

urlpatterns = [
    path("", grn_list, name="grn_list"),
    path("add/", grn_create, name="grn_create"),
    path("<int:pk>/", grn_detail, name="grn_detail"),
    path("<int:pk>/edit/", grn_edit, name="grn_edit"),
]
