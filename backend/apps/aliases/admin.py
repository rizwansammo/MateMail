from django.contrib import admin
from .models import Alias


@admin.register(Alias)
class AliasAdmin(admin.ModelAdmin):
    list_display = ("source_address", "destination_mailbox", "destination_address", "status", "mail_engine_provisioned")
    list_filter = ("status",)
    search_fields = ("source_address", "destination_address")
    readonly_fields = ("id", "created_at", "updated_at")
