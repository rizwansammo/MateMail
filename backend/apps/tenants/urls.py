from django.urls import path

from .security_views import WorkspaceTwoFactorPolicyView
from .admin_hub_mfa_views import WorkspaceAdminHubMfaPolicyView
from .views import (
    OnboardingStatusView,
    WorkspaceDetailView,
    WorkspaceListView,
    WorkspaceMemberDetailView,
    WorkspaceMemberListView,
    WorkspaceStatsView,
)

urlpatterns = [
    path("", WorkspaceListView.as_view(), name="workspace-list"),
    path("<uuid:pk>/", WorkspaceDetailView.as_view(), name="workspace-detail"),
    path("<uuid:pk>/onboarding/", OnboardingStatusView.as_view(), name="workspace-onboarding"),
    path("<uuid:pk>/security/2fa/", WorkspaceTwoFactorPolicyView.as_view(), name="workspace-2fa-policy"),
    path("<uuid:pk>/stats/", WorkspaceStatsView.as_view(), name="workspace-stats"),
    path("<uuid:pk>/members/", WorkspaceMemberListView.as_view(), name="workspace-members"),
    path("<uuid:pk>/members/<uuid:member_id>/", WorkspaceMemberDetailView.as_view(), name="workspace-member-detail"),
    path("<uuid:pk>/members/<uuid:member_id>/hub-mfa/", WorkspaceAdminHubMfaPolicyView.as_view(), name="workspace-admin-hub-mfa"),
]
