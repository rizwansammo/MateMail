from django.contrib import admin
from .models import Tenant, TenantMembership


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "plan", "owner", "created_at")
    list_filter = ("status", "plan")
    search_fields = ("name", "slug", "owner__email")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "role", "status", "created_at")
    list_filter = ("role", "status")
    search_fields = ("user__email", "tenant__name")
    readonly_fields = ("id", "created_at", "updated_at")
