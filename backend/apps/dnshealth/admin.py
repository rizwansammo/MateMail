from django.contrib import admin
from .models import DNSRecordCheck


@admin.register(DNSRecordCheck)
class DNSRecordCheckAdmin(admin.ModelAdmin):
    list_display = ("domain", "record_type", "host", "status", "last_checked")
    list_filter = ("status", "record_type")
    search_fields = ("domain__domain", "host")
    readonly_fields = ("id", "created_at")
