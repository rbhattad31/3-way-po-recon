"""Template context processors."""
from apps.core.enums import ReviewStatus
from apps.reviews.models import ReviewAssignment


def pending_reviews(request):
    if request.user.is_authenticated:
        count = ReviewAssignment.objects.filter(
            status__in=[ReviewStatus.PENDING, ReviewStatus.ASSIGNED, ReviewStatus.IN_REVIEW]
        ).count()
        return {"pending_review_count": count}
    return {"pending_review_count": 0}
