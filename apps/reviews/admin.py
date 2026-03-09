from django.contrib import admin
from django.utils.html import format_html

from apps.reviews.models import ReviewAssignment, ReviewComment, ManualReviewAction, ReviewDecision


class CommentInline(admin.TabularInline):
    model = ReviewComment
    extra = 0
    fields = ("author", "body", "is_internal", "created_at")
    readonly_fields = ("author", "body", "is_internal", "created_at")


class ActionInline(admin.TabularInline):
    model = ManualReviewAction
    extra = 0
    fields = ("performed_by", "action_type", "field_name", "old_value", "new_value", "reason", "created_at")
    readonly_fields = fields


class DecisionInline(admin.StackedInline):
    model = ReviewDecision
    extra = 0
    readonly_fields = ("decided_by", "decision", "reason", "decided_at")


@admin.register(ReviewAssignment)
class ReviewAssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "id", "reconciliation_result", "assigned_to", "status_badge",
        "priority", "due_date", "comment_count", "action_count", "created_at",
    )
    list_filter = ("status", "priority", "assigned_to")
    search_fields = ("reconciliation_result__invoice__invoice_number", "notes")
    list_per_page = 25
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")
    inlines = [DecisionInline, CommentInline, ActionInline]
    fieldsets = (
        ("Assignment", {"fields": ("reconciliation_result", "assigned_to", "status", "priority", "due_date")}),
        ("Notes", {"fields": ("notes",)}),
        ("Audit", {"fields": ("created_at", "updated_at", "created_by", "updated_by"), "classes": ("collapse",)}),
    )

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {
            "PENDING": "#6c757d", "ASSIGNED": "#0d6efd",
            "IN_REVIEW": "#ffc107", "APPROVED": "#198754",
            "REJECTED": "#dc3545", "REPROCESSED": "#17a2b8",
        }
        c = colours.get(obj.status, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            c, obj.get_status_display(),
        )

    @admin.display(description="Comments")
    def comment_count(self, obj):
        return obj.comments.count()

    @admin.display(description="Actions")
    def action_count(self, obj):
        return obj.actions.count()


@admin.register(ReviewComment)
class ReviewCommentAdmin(admin.ModelAdmin):
    list_display = ("id", "assignment", "author", "body_short", "is_internal", "created_at")
    list_filter = ("is_internal",)
    search_fields = ("body",)
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Comment")
    def body_short(self, obj):
        return obj.body[:120]


@admin.register(ManualReviewAction)
class ManualReviewActionAdmin(admin.ModelAdmin):
    list_display = ("id", "assignment", "performed_by", "action_type", "field_name", "created_at")
    list_filter = ("action_type",)
    search_fields = ("field_name", "reason")
    list_per_page = 25
    readonly_fields = ("created_at", "updated_at")


@admin.register(ReviewDecision)
class ReviewDecisionAdmin(admin.ModelAdmin):
    list_display = ("id", "assignment", "decision_badge", "decided_by", "reason_short", "decided_at")
    list_filter = ("decision",)
    list_per_page = 25
    readonly_fields = ("decided_at", "created_at", "updated_at")

    @admin.display(description="Decision")
    def decision_badge(self, obj):
        colours = {
            "APPROVED": "#198754", "REJECTED": "#dc3545", "REPROCESSED": "#17a2b8",
        }
        c = colours.get(obj.decision, "#6c757d")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            c, obj.get_decision_display(),
        )

    @admin.display(description="Reason")
    def reason_short(self, obj):
        return obj.reason[:120] if obj.reason else "-"
