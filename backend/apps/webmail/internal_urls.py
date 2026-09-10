"""
Internal-only webmail routes.

Mounted at /api/internal/webmail/ so the edge nginx rule that denies
/api/internal/ actually covers them. These endpoints authenticate with
INTERNAL_API_SECRET and are called by the webmail app over the internal
network, never by a browser.
"""
from django.urls import path

from .views import WebmailTokenValidateView

urlpatterns = [
    path("validate-token/", WebmailTokenValidateView.as_view(), name="webmail-validate-token"),
]
