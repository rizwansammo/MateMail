from django.urls import path

from apps.dnshealth.views import DomainCheckDNSView, DomainDNSRecordsView
from .views import DomainDetailView, DomainListCreateView, DomainProvisionView

urlpatterns = [
    path("", DomainListCreateView.as_view(), name="domain-list"),
    path("<uuid:pk>/", DomainDetailView.as_view(), name="domain-detail"),
    path("<uuid:pk>/records/", DomainDNSRecordsView.as_view(), name="domain-dns-records"),
    path("<uuid:pk>/check/", DomainCheckDNSView.as_view(), name="domain-check-dns"),
    path("<uuid:pk>/provision/", DomainProvisionView.as_view(), name="domain-provision"),
]
