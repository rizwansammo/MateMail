from django.contrib import admin
from .models import ForwardingRule


@admin.register(ForwardingRule)
class ForwardingRuleAdmin(admin.ModelAdmin):
    list_display = ("source_mailbox", "destination_email", "keep_copy", "status", "mail_engine_provisioned")
    list_filter = ("status", "keep_copy")
    readonly_fields = ("id", "created_at", "updated_at")
