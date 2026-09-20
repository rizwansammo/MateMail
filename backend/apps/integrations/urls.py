from django.urls import path

from .views import (
    AuthorizationView,
    ConnectStartView,
    ConnectStatusView,
    DisconnectView,
    IntegrationDetailView,
    IntegrationListView,
    IntegrationProfileView,
    IntegrationSendView,
    SignatureListView,
)

urlpatterns = [
    path("", IntegrationListView.as_view(), name="integration-list"),
    path(
        "<uuid:integration_id>/",
        IntegrationDetailView.as_view(),
        name="integration-detail",
    ),
    path(
        "authorize/",
        AuthorizationView.as_view(),
        name="integration-authorize",
    ),
    path(
        "connect/start/",
        ConnectStartView.as_view(),
        name="integration-connect-start",
    ),
    path(
        "connect/status/",
        ConnectStatusView.as_view(),
        name="integration-connect-status",
    ),
    path(
        "external/profile/",
        IntegrationProfileView.as_view(),
        name="integration-profile",
    ),
    path(
        "external/signatures/",
        SignatureListView.as_view(),
        name="integration-signatures",
    ),
    path(
        "external/send/",
        IntegrationSendView.as_view(),
        name="integration-send",
    ),
    path(
        "external/disconnect/",
        DisconnectView.as_view(),
        name="integration-disconnect",
    ),
]
