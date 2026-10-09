from django.urls import path

from .internal_views import (
    PendingTransportSecurityView, AuthorizeTransportSecurityView,
    StateTransportSecurityView,
)

urlpatterns = [
    path("pending/", PendingTransportSecurityView.as_view(), name="transport-security-pending"),
    path("authorize/", AuthorizeTransportSecurityView.as_view(), name="transport-security-authorize"),
    path("<uuid:pk>/state/", StateTransportSecurityView.as_view(), name="transport-security-state"),
]
