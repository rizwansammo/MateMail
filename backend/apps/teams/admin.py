from django.contrib import admin
from .models import APIKey, TeamInvite


@admin.register(TeamInvite)
class TeamInviteAdmin(admin.ModelAdmin):
    list_display = ("email", "tenant", "role", "is_revoked", "accepted_at", "expires_at", "created_at")
    list_filter = ("role", "is_revoked")
    search_fields = ("email", "tenant__name")
    readonly_fields = ("token_hash", "created_at")


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "key_prefix", "is_active", "last_used_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "tenant__name", "key_prefix")
    readonly_fields = ("key_hash", "key_prefix", "created_at", "last_used_at")
