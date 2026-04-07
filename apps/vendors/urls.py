from django.urls import path

from apps.vendors.template_views import vendor_create, vendor_detail, vendor_edit, vendor_list

app_name = "vendors"

urlpatterns = [
    path("", vendor_list, name="vendor_list"),
    path("add/", vendor_create, name="vendor_create"),
    path("<int:pk>/", vendor_detail, name="vendor_detail"),
    path("<int:pk>/edit/", vendor_edit, name="vendor_edit"),
]
