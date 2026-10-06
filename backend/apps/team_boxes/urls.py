from django.urls import path

from .views import (
    TeamBoxDetailView,
    TeamBoxListCreateView,
    TeamBoxMemberDetailView,
    TeamBoxMemberListCreateView,
    TeamBoxReprovisionView,
    TeamBoxStatusView,
)

urlpatterns = [
    path("", TeamBoxListCreateView.as_view(), name="team-box-list"),
    path("<uuid:pk>/", TeamBoxDetailView.as_view(), name="team-box-detail"),
    path("<uuid:pk>/status/", TeamBoxStatusView.as_view(), name="team-box-status"),
    path("<uuid:pk>/reprovision/", TeamBoxReprovisionView.as_view(), name="team-box-reprovision"),
    path("<uuid:pk>/members/", TeamBoxMemberListCreateView.as_view(), name="team-box-members"),
    path(
        "<uuid:pk>/members/<uuid:member_pk>/",
        TeamBoxMemberDetailView.as_view(),
        name="team-box-member-detail",
    ),
]
