from django.urls import path

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
    path("stats/", AdminStatsView.as_view(), name="admin-stats"),
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
    # POST suspends, DELETE releases.
    path("mailboxes/<uuid:pk>/suspend/", AdminMailboxSuspendView.as_view(), name="admin-mailbox-suspend"),
]
