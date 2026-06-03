from django.urls import path

from .views import (
    AdminStatsView,
    AdminTenantActivateView,
    AdminTenantDetailView,
    AdminTenantListView,
    AdminTenantPlanView,
    AdminTenantSuspendView,
)

urlpatterns = [
    path("stats/", AdminStatsView.as_view(), name="admin-stats"),
    path("tenants/", AdminTenantListView.as_view(), name="admin-tenant-list"),
    path("tenants/<uuid:pk>/", AdminTenantDetailView.as_view(), name="admin-tenant-detail"),
    path("tenants/<uuid:pk>/suspend/", AdminTenantSuspendView.as_view(), name="admin-tenant-suspend"),
    path("tenants/<uuid:pk>/activate/", AdminTenantActivateView.as_view(), name="admin-tenant-activate"),
    path("tenants/<uuid:pk>/plan/", AdminTenantPlanView.as_view(), name="admin-tenant-plan"),
]
