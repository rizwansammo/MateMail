from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/health/", include("apps.health.urls")),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/workspaces/", include("apps.tenants.urls")),
    path("api/custom-hostnames/", include("apps.tenants.custom_host_urls")),
    path("api/domains/", include("apps.domains.urls")),
    path("api/mailboxes/", include("apps.mailboxes.urls")),
    path("api/team-boxes/", include("apps.team_boxes.urls")),
    path("api/aliases/", include("apps.aliases.urls")),
    path("api/forwarding/", include("apps.forwarding.urls")),
    path("api/forward-groups/", include("apps.forward_groups.urls")),
    path("api/delegations/", include("apps.delegations.urls")),
    path("api/billing/", include("apps.billing.urls")),
    path("api/logs/", include("apps.logs.urls")),
    path("api/queue/", include("apps.mailqueue.urls")),
    path("api/quarantine/", include("apps.quarantine.urls")),
    # Webmail SSO — tenant-authenticated
    # MateMail PostBox — the mailbox user's own webmail. Authenticated by a
    # PostBox session cookie bound to one mailbox, never by a Workspace JWT.
    path("api/postbox/", include("apps.postbox.urls")),
    # ── Internal endpoints ────────────────────────────────────────────────────
    # Secured by INTERNAL_API_SECRET *and* denied at the edge by nginx.
    # Everything internal MUST live under /api/internal/ for that rule to apply.
    path("api/internal/smtp/", include("apps.smtp_policy.urls")),
    path("api/internal/health/", include("apps.health.internal_urls")),
    # Root-owned nginx/Certbot provisioner. Separate purpose-specific secret.
    path("api/internal/custom-hostnames/", include("apps.tenants.custom_host_internal_urls")),
    # The Native Engine's new-mail events for native PostBox push. Its own
    # credential (POSTBOX_PUSH_INGEST_SECRET), not INTERNAL_API_SECRET.
    path("api/internal/postbox/", include("apps.postbox.internal_urls")),
    # Platform admin endpoints — IsPlatformAdmin permission required
    path("api/platform/", include("apps.platform_admin.urls")),
    # Team invites + API keys
    path("api/teams/", include("apps.teams.urls")),
    # Tenant-scoped connected apps (SalesHub first-party integration).
    path("api/integrations/", include("apps.integrations.urls")),
    # Backup jobs — list, trigger, detail
    path("api/backups/", include("apps.backups.urls")),

    # Mail-client discovery. At the ROOT, not under /api/, because
    # Outlook constructs `/autodiscover/autodiscover.xml` itself and will
    # not look anywhere else. Last in the list so it can never shadow an
    # API prefix, and the nginx vhost for autodiscover.matemail.online
    # allow-lists exactly these paths and proxies nothing else.
    path("", include("apps.autodiscover.urls")),
]

admin.site.site_header = "MateMail Platform Admin"
admin.site.site_title = "MateMail"
admin.site.index_title = "MateMail Administration"
