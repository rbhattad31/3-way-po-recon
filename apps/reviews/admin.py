from django.contrib import admin
from apps.reviews.models import ReviewAssignment, ReviewComment, ManualReviewAction, ReviewDecision


class CommentInline(admin.TabularInline):
    model = ReviewComment
    extra = 0
    readonly_fields = ("author", "body", "is_internal", "created_at")


class ActionInline(admin.TabularInline):
    model = ManualReviewAction
    extra = 0
    readonly_fields = ("performed_by", "action_type", "field_name", "old_value", "new_value", "reason", "created_at")


@admin.register(ReviewAssignment)
class ReviewAssignmentAdmin(admin.ModelAdmin):
    list_display = ("id", "reconciliation_result", "assigned_to", "status", "priority", "created_at")
    list_filter = ("status", "priority")
    search_fields = ("reconciliation_result__invoice__invoice_number",)
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    inlines = [CommentInline, ActionInline]


@admin.register(ReviewDecision)
class ReviewDecisionAdmin(admin.ModelAdmin):
    list_display = ("id", "assignment", "decision", "decided_by", "decided_at")
    list_filter = ("decision",)
    readonly_fields = ("decided_at",)
