"""Human review workflow models."""
from django.conf import settings
from django.db import models

from apps.core.enums import ReviewActionType, ReviewStatus
from apps.core.models import BaseModel, TimestampMixin


class ReviewAssignment(BaseModel):
    """Links a reconciliation result to a reviewer."""

    reconciliation_result = models.ForeignKey(
        "reconciliation.ReconciliationResult", on_delete=models.CASCADE, related_name="review_assignments"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="review_assignments"
    )
    status = models.CharField(max_length=20, choices=ReviewStatus.choices, default=ReviewStatus.PENDING, db_index=True)
    priority = models.PositiveSmallIntegerField(default=5, help_text="1=highest, 10=lowest")
    due_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        db_table = "reviews_assignment"
        ordering = ["priority", "-created_at"]
        verbose_name = "Review Assignment"
        verbose_name_plural = "Review Assignments"
        indexes = [
            models.Index(fields=["status"], name="idx_revassign_status"),
            models.Index(fields=["assigned_to"], name="idx_revassign_user"),
            models.Index(fields=["priority"], name="idx_revassign_priority"),
        ]

    def __str__(self) -> str:
        return f"Review #{self.pk} – Result {self.reconciliation_result_id} – {self.status}"


class ReviewComment(TimestampMixin):
    """Reviewer comment on a review assignment."""

    assignment = models.ForeignKey(ReviewAssignment, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    body = models.TextField()
    is_internal = models.BooleanField(default=True, help_text="Internal vs. visible to vendor")

    class Meta:
        db_table = "reviews_comment"
        ordering = ["created_at"]
        verbose_name = "Review Comment"
        verbose_name_plural = "Review Comments"

    def __str__(self) -> str:
        return f"Comment by {self.author} on Review #{self.assignment_id}"


class ManualReviewAction(TimestampMixin):
    """Every discrete action taken on a review (correct field, approve, etc.)."""

    assignment = models.ForeignKey(ReviewAssignment, on_delete=models.CASCADE, related_name="actions")
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action_type = models.CharField(max_length=30, choices=ReviewActionType.choices, db_index=True)
    field_name = models.CharField(max_length=100, blank=True, default="", help_text="Field corrected, if applicable")
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    reason = models.TextField(blank=True, default="")

    class Meta:
        db_table = "reviews_action"
        ordering = ["-created_at"]
        verbose_name = "Manual Review Action"
        verbose_name_plural = "Manual Review Actions"
        indexes = [
            models.Index(fields=["action_type"], name="idx_revaction_type"),
        ]

    def __str__(self) -> str:
        return f"{self.action_type} by {self.performed_by} on Review #{self.assignment_id}"


class ReviewDecision(TimestampMixin):
    """Final decision on a review assignment."""

    assignment = models.OneToOneField(ReviewAssignment, on_delete=models.CASCADE, related_name="decision")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    decision = models.CharField(max_length=20, choices=ReviewStatus.choices)
    reason = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reviews_decision"
        ordering = ["-decided_at"]
        verbose_name = "Review Decision"
        verbose_name_plural = "Review Decisions"

    def __str__(self) -> str:
        return f"Decision {self.decision} on Review #{self.assignment_id}"
