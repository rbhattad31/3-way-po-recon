from django.urls import path

from apps.reviews.template_views import add_comment, assignment_detail, assignment_list, decide

app_name = "reviews"

urlpatterns = [
    path("", assignment_list, name="assignment_list"),
    path("<int:pk>/", assignment_detail, name="assignment_detail"),
    path("<int:pk>/decide/", decide, name="decide"),
    path("<int:pk>/comment/", add_comment, name="add_comment"),
]
