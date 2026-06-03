from django.contrib import admin
from .models import Domain


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "status", "dns_health_score", "mail_engine_provisioned", "added_at")
    list_filter = ("status", "mail_engine_provisioned")
    search_fields = ("domain", "tenant__name")
    readonly_fields = ("id", "added_at", "verified_at", "updated_at")
