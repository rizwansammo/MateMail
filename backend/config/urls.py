from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/health/", include("apps.health.urls")),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/workspaces/", include("apps.tenants.urls")),
    path("api/domains/", include("apps.domains.urls")),
    path("api/mailboxes/", include("apps.mailboxes.urls")),
    path("api/aliases/", include("apps.aliases.urls")),
    path("api/forwarding/", include("apps.forwarding.urls")),
    path("api/billing/", include("apps.billing.urls")),
    path("api/logs/", include("apps.logs.urls")),
    path("api/queue/", include("apps.mailqueue.urls")),
    path("api/quarantine/", include("apps.quarantine.urls")),
    # Webmail SSO — /api/webmail/sso/ (tenant-authenticated) + /api/internal/webmail/validate-token/
    path("api/webmail/", include("apps.webmail.urls")),
    # Internal SMTP policy endpoints — secured by INTERNAL_API_SECRET, blocked publicly by nginx
    path("api/internal/smtp/", include("apps.smtp_policy.urls")),
    # Platform admin endpoints — IsPlatformAdmin permission required
    path("api/platform/", include("apps.platform_admin.urls")),
    # Team invites + API keys
    path("api/teams/", include("apps.teams.urls")),
    # Backup jobs — list, trigger, detail
    path("api/backups/", include("apps.backups.urls")),
]

admin.site.site_header = "MateMail Platform Admin"
admin.site.site_title = "MateMail"
admin.site.index_title = "MateMail Administration"
