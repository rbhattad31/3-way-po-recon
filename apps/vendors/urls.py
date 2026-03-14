from django.urls import path

from apps.vendors.template_views import vendor_detail, vendor_list

app_name = "vendors"

urlpatterns = [
    path("", vendor_list, name="vendor_list"),
    path("<int:pk>/", vendor_detail, name="vendor_detail"),
]
