"""Review template URL patterns (merged from apps.reviews, served at /reviews/)."""

from django.urls import path

from apps.cases.template_views import (
    review_add_comment,
    review_assignment_detail,
    review_assignment_list,
    review_create_assignments,
    review_decide,
)

app_name = "reviews"

urlpatterns = [
    path("", review_assignment_list, name="assignment_list"),
    path("create-assignments/", review_create_assignments, name="create_assignments"),
    path("<int:pk>/", review_assignment_detail, name="assignment_detail"),
    path("<int:pk>/decide/", review_decide, name="decide"),
    path("<int:pk>/comment/", review_add_comment, name="add_comment"),
]
