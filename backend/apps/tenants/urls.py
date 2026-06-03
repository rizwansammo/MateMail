from django.urls import path

from .views import (
    OnboardingStatusView,
    WorkspaceCreateView,
    WorkspaceDetailView,
    WorkspaceListView,
    WorkspaceMemberDetailView,
    WorkspaceMemberListView,
    WorkspaceStatsView,
    WorkspaceSwitchView,
)

urlpatterns = [
    path("", WorkspaceListView.as_view(), name="workspace-list"),
    path("create/", WorkspaceCreateView.as_view(), name="workspace-create"),
    path("switch/", WorkspaceSwitchView.as_view(), name="workspace-switch"),
    path("<uuid:pk>/", WorkspaceDetailView.as_view(), name="workspace-detail"),
    path("<uuid:pk>/onboarding/", OnboardingStatusView.as_view(), name="workspace-onboarding"),
    path("<uuid:pk>/stats/", WorkspaceStatsView.as_view(), name="workspace-stats"),
    path("<uuid:pk>/members/", WorkspaceMemberListView.as_view(), name="workspace-members"),
    path("<uuid:pk>/members/<uuid:member_id>/", WorkspaceMemberDetailView.as_view(), name="workspace-member-detail"),
]
