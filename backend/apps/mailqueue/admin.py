from django.contrib import admin
from .models import QueueMessage


@admin.register(QueueMessage)
class QueueMessageAdmin(admin.ModelAdmin):
    list_display = ("sender", "recipient", "status", "retry_count", "queued_at", "next_retry")
    list_filter = ("status",)
    search_fields = ("sender", "recipient", "engine_message_id")
    readonly_fields = ("id", "queued_at")
