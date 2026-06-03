from django.contrib import admin
from .models import BackupJob


@admin.register(BackupJob)
class BackupJobAdmin(admin.ModelAdmin):
    list_display = ("tenant", "scope", "status", "size_mb", "started_at", "completed_at")
    list_filter = ("status", "scope")
    search_fields = ("tenant__name",)
    readonly_fields = ("id", "created_at")
