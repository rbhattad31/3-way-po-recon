from django.contrib import admin

from apps.copilot.models import CopilotMessage, CopilotSession, CopilotSessionArtifact


class CopilotMessageInline(admin.TabularInline):
    model = CopilotMessage
    extra = 0
    readonly_fields = ("message_type", "content", "token_count", "created_at")
    fields = ("message_type", "content", "token_count", "created_at")


@admin.register(CopilotSession)
class CopilotSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "user", "status", "is_pinned", "linked_case", "last_message_at")
    list_filter = ("status", "is_pinned", "is_archived")
    search_fields = ("title", "user__email")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [CopilotMessageInline]


@admin.register(CopilotMessage)
class CopilotMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "message_type", "created_at")
    list_filter = ("message_type",)
    readonly_fields = ("created_at",)


@admin.register(CopilotSessionArtifact)
class CopilotSessionArtifactAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "artifact_type", "linked_object_type", "linked_object_id")
    list_filter = ("artifact_type",)
