from django.contrib import admin
from .models import Domain


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "status", "dns_health_score", "mail_engine_provisioned", "added_at")
    list_filter = ("status", "mail_engine_provisioned")
    search_fields = ("domain", "tenant__name")
    readonly_fields = ("id", "added_at", "verified_at", "updated_at", "has_dkim_private_key")

    # The DKIM private key is the domain's mail-signing secret. Excluding it keeps
    # it out of both the change form and the change-form POST handler, so staff
    # can neither read nor overwrite it here. Use has_dkim_private_key to confirm
    # a key exists without disclosing it.
    exclude = ("dkim_private_key",)

    @admin.display(boolean=True, description="DKIM private key present")
    def has_dkim_private_key(self, obj):
        # Presence only. The value is encrypted at rest and must never be
        # rendered here — the admin is the easiest place to leak a signing key.
        return obj.has_dkim_private_key
