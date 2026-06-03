from django.contrib import admin
from .models import QuarantineMessage


@admin.register(QuarantineMessage)
class QuarantineMessageAdmin(admin.ModelAdmin):
    list_display = ("sender", "recipient", "subject", "spam_score", "status", "received_at")
    list_filter = ("status",)
    search_fields = ("sender", "recipient", "subject")
    readonly_fields = ("id", "received_at")
