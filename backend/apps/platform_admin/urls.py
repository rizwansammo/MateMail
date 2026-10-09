from django.urls import path
from apps.dmarc_reports.views import PlatformDmarcReportsView
from apps.tls_reports.views import PlatformTlsReceiverHealthView

from .auth_views import (
    PlatformForgotPasswordView,
    PlatformLoginView,
    PlatformResendView,
    PlatformResetPasswordView,
    PlatformVerifyView,
)
from .global_views import (
    PlatformAliasListView,
    PlatformAuditActionsView,
    PlatformAuditLogListView,
    PlatformDomainListView,
    PlatformForwardingListView,
    PlatformMailLogListView,
    PlatformMailboxListView,
    PlatformPlanListView,
    PlatformQuarantineListView,
    PlatformQueueListView,
    PlatformSearchView,
)
from .ops_views import (
    PlatformAliasDisableView,
    PlatformBackupStatusView,
    PlatformForwardingDisableView,
    PlatformHealthView,
    PlatformQuarantineActionView,
    PlatformQueueCancelView,
)
from .owner_views import (
    PlatformOwnerPasswordRecoveryView,
    PlatformOwnerRevokeSessionsView,
    PlatformOwnerView,
)
from .views import (
    AdminMailboxSuspendView,
    AdminPendingTenantsView,
    AdminStatsView,
    AdminTenantApproveView,
    AdminTenantOutboundView,
    AdminTenantRejectView,
    AdminTenantActivateView,
    AdminTenantDetailView,
    AdminTenantListView,
    AdminTenantPlanView,
    AdminTenantSuspendView,
)

urlpatterns = [
    # ── Platform Console authentication ─────────────────────────────────────
    # Unauthenticated by necessity: these are how a session is obtained. Only
    # `verify/` issues one, and only after the emailed code checks out.
    path("auth/login/", PlatformLoginView.as_view(), name="platform-auth-login"),
    path("auth/verify/", PlatformVerifyView.as_view(), name="platform-auth-verify"),
    path("auth/resend/", PlatformResendView.as_view(), name="platform-auth-resend"),
    path("auth/forgot-password/", PlatformForgotPasswordView.as_view(),
         name="platform-auth-forgot-password"),
    path("auth/reset-password/", PlatformResetPasswordView.as_view(),
         name="platform-auth-reset-password"),

    path("stats/", AdminStatsView.as_view(), name="admin-stats"),
    path("dmarc-reports/", PlatformDmarcReportsView.as_view(), name="platform-dmarc-reports"),
    path("tls-reporting/health/", PlatformTlsReceiverHealthView.as_view(), name="platform-tls-health"),
    path("tenants/", AdminTenantListView.as_view(), name="admin-tenant-list"),
    # Before <uuid:pk>/ so "pending" is never parsed as an id.
    path("tenants/pending/", AdminPendingTenantsView.as_view(), name="admin-tenant-pending"),
    path("tenants/<uuid:pk>/", AdminTenantDetailView.as_view(), name="admin-tenant-detail"),
    path("tenants/<uuid:pk>/suspend/", AdminTenantSuspendView.as_view(), name="admin-tenant-suspend"),
    path("tenants/<uuid:pk>/activate/", AdminTenantActivateView.as_view(), name="admin-tenant-activate"),
    path("tenants/<uuid:pk>/plan/", AdminTenantPlanView.as_view(), name="admin-tenant-plan"),
    path("tenants/<uuid:pk>/approve/", AdminTenantApproveView.as_view(), name="admin-tenant-approve"),
    path("tenants/<uuid:pk>/reject/", AdminTenantRejectView.as_view(), name="admin-tenant-reject"),
    path("tenants/<uuid:pk>/outbound/", AdminTenantOutboundView.as_view(), name="admin-tenant-outbound"),
    # ── Primary Owner recovery ──────────────────────────────────────────────
    # The subject is always <pk>'s owner. No request field names a user, so
    # none can be pointed at a different account.
    path("tenants/<uuid:pk>/owner/", PlatformOwnerView.as_view(),
         name="platform-owner"),
    path("tenants/<uuid:pk>/owner/password-recovery/",
         PlatformOwnerPasswordRecoveryView.as_view(),
         name="platform-owner-password-recovery"),
    path("tenants/<uuid:pk>/owner/revoke-sessions/",
         PlatformOwnerRevokeSessionsView.as_view(),
         name="platform-owner-revoke-sessions"),

    # POST suspends, DELETE releases.
    path("mailboxes/<uuid:pk>/suspend/", AdminMailboxSuspendView.as_view(), name="admin-mailbox-suspend"),

    # ── Platform-wide reads ─────────────────────────────────────────────────
    path("domains/", PlatformDomainListView.as_view(), name="platform-domains"),
    path("mailboxes/", PlatformMailboxListView.as_view(), name="platform-mailboxes"),
    path("aliases/", PlatformAliasListView.as_view(), name="platform-aliases"),
    path("forwarding/", PlatformForwardingListView.as_view(), name="platform-forwarding"),
    path("queue/", PlatformQueueListView.as_view(), name="platform-queue"),
    path("quarantine/", PlatformQuarantineListView.as_view(), name="platform-quarantine"),
    path("logs/", PlatformMailLogListView.as_view(), name="platform-logs"),
    path("audit/", PlatformAuditLogListView.as_view(), name="platform-audit"),
    path("audit/actions/", PlatformAuditActionsView.as_view(), name="platform-audit-actions"),
    path("plans/", PlatformPlanListView.as_view(), name="platform-plans"),
    path("search/", PlatformSearchView.as_view(), name="platform-search"),
    path("health/", PlatformHealthView.as_view(), name="platform-health"),
    path("backups/", PlatformBackupStatusView.as_view(), name="platform-backups"),

    # ── Oversight actions ───────────────────────────────────────────────────
    # POST disables / releases, DELETE reverses it.
    path("aliases/<uuid:pk>/disable/", PlatformAliasDisableView.as_view(),
         name="platform-alias-disable"),
    path("forwarding/<uuid:pk>/disable/", PlatformForwardingDisableView.as_view(),
         name="platform-forwarding-disable"),
    path("quarantine/<uuid:pk>/action/", PlatformQuarantineActionView.as_view(),
         name="platform-quarantine-action"),
    path("queue/<uuid:pk>/cancel/", PlatformQueueCancelView.as_view(),
         name="platform-queue-cancel"),
]
