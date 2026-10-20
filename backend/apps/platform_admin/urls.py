from django.urls import path
from apps.dmarc_reports.views import PlatformDmarcReportsView
from apps.tls_reports.views import PlatformTlsReceiverHealthView

from .mail_center_capabilities import PlatformMailCenterCapabilitiesView
from .mail_center_grants_inventory import PlatformMailCenterGrantInventoryView
from .mail_center_reporting import PlatformMailCenterReportOverviewView
from .mail_center_purpose import (
    PlatformPurposeGrantListView, PlatformPurposeGrantCreateView,
    PlatformPurposeGrantRevokeView, PlatformPurposeFoldersView,
    PlatformPurposeMessagesView, PlatformPurposeMessageDetailView,
    PlatformPurposeAttachmentView,
)
from .mail_center_actions import (
    PlatformOperationsSendView, PlatformOperationsDraftSaveView,
    PlatformOperationsDraftDeleteView,
)
from .mail_center_views import (
    PlatformMailCenterFoldersView, PlatformMailCenterMessagesView,
    PlatformMailCenterMessageDetailView, PlatformMailCenterAttachmentView,
)

from .system_mail_views import (
    PlatformSystemMailConfigurationView,
    PlatformSystemMailReadinessView,
    PlatformSystemMailHistoryView,
    PlatformSystemMailStageView,
    PlatformSystemMailDkimPrepareView,
    PlatformSystemMailDnsReadinessView,
)

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
from .owner_emergency_views import (
    PlatformOwnerEmergencyCasesView,
    PlatformOwnerEmergencyCaseActionView,
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

    path("tenants/<uuid:pk>/owner/emergency/",
         PlatformOwnerEmergencyCasesView.as_view(), name="platform-owner-emergency"),
    path("tenants/<uuid:pk>/owner/emergency/<uuid:case_id>/<str:action>/",
         PlatformOwnerEmergencyCaseActionView.as_view(), name="platform-owner-emergency-action"),

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
    path("mail-center/capabilities/", PlatformMailCenterCapabilitiesView.as_view(), name="platform-mail-center-capabilities"),
    path("mail-center/system-access/manage/", PlatformMailCenterGrantInventoryView.as_view(), name="platform-mail-center-grants-inventory"),
    path("mail-center/reports/overview/", PlatformMailCenterReportOverviewView.as_view(), name="platform-mail-center-report-overview"),
    path("mail-center/system-access/", PlatformPurposeGrantListView.as_view(), name="platform-mail-center-system-access"),
    path("mail-center/system-access/grants/", PlatformPurposeGrantCreateView.as_view(), name="platform-mail-center-grant-create"),
    path("mail-center/system-access/grants/<uuid:grant_id>/", PlatformPurposeGrantRevokeView.as_view(), name="platform-mail-center-grant-revoke"),
    path("mail-center/purpose/<str:role>/folders/", PlatformPurposeFoldersView.as_view(), name="platform-mail-center-purpose-folders"),
    path("mail-center/purpose/<str:role>/messages/", PlatformPurposeMessagesView.as_view(), name="platform-mail-center-purpose-messages"),
    path("mail-center/purpose/<str:role>/messages/<int:uid>/", PlatformPurposeMessageDetailView.as_view(), name="platform-mail-center-purpose-detail"),
    path("mail-center/purpose/<str:role>/messages/<int:uid>/attachments/<int:part_id>/", PlatformPurposeAttachmentView.as_view(), name="platform-mail-center-purpose-attachment"),
    path("mail-center/send/", PlatformOperationsSendView.as_view(), name="platform-mail-center-send"),
    path("mail-center/drafts/", PlatformOperationsDraftSaveView.as_view(), name="platform-mail-center-drafts"),
    path("mail-center/drafts/<int:uid>/", PlatformOperationsDraftDeleteView.as_view(), name="platform-mail-center-draft-delete"),
    path("mail-center/folders/", PlatformMailCenterFoldersView.as_view(), name="platform-mail-center-folders"),
    path("mail-center/messages/<int:uid>/attachments/<int:part_id>/", PlatformMailCenterAttachmentView.as_view(), name="platform-mail-center-attachment"),
    path("mail-center/messages/<int:uid>/", PlatformMailCenterMessageDetailView.as_view(), name="platform-mail-center-message-detail"),
    path("mail-center/messages/", PlatformMailCenterMessagesView.as_view(), name="platform-mail-center-messages"),
    path("system-mail/configuration/", PlatformSystemMailConfigurationView.as_view(), name="platform-system-mail-configuration"),
    path("system-mail/readiness/", PlatformSystemMailReadinessView.as_view(), name="platform-system-mail-readiness"),
    path("system-mail/dns-readiness/", PlatformSystemMailDnsReadinessView.as_view(), name="platform-system-mail-dns-readiness"),
    path("system-mail/history/", PlatformSystemMailHistoryView.as_view(), name="platform-system-mail-history"),
    path("system-mail/stage/", PlatformSystemMailStageView.as_view(), name="platform-system-mail-stage"),
    path("system-mail/prepare-dkim/", PlatformSystemMailDkimPrepareView.as_view(), name="platform-system-mail-prepare-dkim"),

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
