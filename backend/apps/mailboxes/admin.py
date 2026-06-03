from django.contrib import admin
from .models import Mailbox


@admin.register(Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    list_display = ("email", "tenant", "status", "quota_mb", "storage_used_mb", "mail_engine_provisioned", "created_at")
    list_filter = ("status", "mail_engine_provisioned")
    search_fields = ("email", "full_name", "tenant__name")
    readonly_fields = ("id", "email", "created_at", "updated_at", "last_login")
