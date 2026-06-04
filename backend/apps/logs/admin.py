from django.contrib import admin
from .models import MailLog


@admin.register(MailLog)
class MailLogAdmin(admin.ModelAdmin):
    list_display = ["event_type", "tenant", "source", "result", "created_at"]
    list_filter = ["event_type", "result"]
    search_fields = ["source", "tenant__name"]
    readonly_fields = ["id", "tenant", "event_type", "source", "result", "ip_address", "metadata", "created_at"]
