from django.urls import path

from apps.dnshealth.views import DomainCheckDNSView, DomainDNSRecordsView
from .views import (
    DomainDetailView,
    DomainListCreateView,
    DomainProvisionView,
    DomainRotateVerificationTokenView,
    DomainVerifyOwnershipView,
)

urlpatterns = [
    path("", DomainListCreateView.as_view(), name="domain-list"),
    path("<uuid:pk>/", DomainDetailView.as_view(), name="domain-detail"),
    path("<uuid:pk>/records/", DomainDNSRecordsView.as_view(), name="domain-dns-records"),
    path("<uuid:pk>/check/", DomainCheckDNSView.as_view(), name="domain-check-dns"),
    path("<uuid:pk>/provision/", DomainProvisionView.as_view(), name="domain-provision"),
    path(
        "<uuid:pk>/verify-ownership/",
        DomainVerifyOwnershipView.as_view(),
        name="domain-verify-ownership",
    ),
    path(
        "<uuid:pk>/rotate-verification-token/",
        DomainRotateVerificationTokenView.as_view(),
        name="domain-rotate-verification-token",
    ),
]
