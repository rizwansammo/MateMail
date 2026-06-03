from django.urls import path

from .views import MailboxDetailView, MailboxListCreateView, MailboxReProvisionView, MailboxStatusView

urlpatterns = [
    path("", MailboxListCreateView.as_view(), name="mailbox-list"),
    path("<uuid:pk>/", MailboxDetailView.as_view(), name="mailbox-detail"),
    path("<uuid:pk>/status/", MailboxStatusView.as_view(), name="mailbox-status"),
    path("<uuid:pk>/reprovision/", MailboxReProvisionView.as_view(), name="mailbox-reprovision"),
]
