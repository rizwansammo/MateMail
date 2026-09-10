"""
Tenant-facing webmail routes.

The token *validation* endpoint is internal-only and lives in internal_urls.py,
mounted under /api/internal/webmail/ — do not add it here, or the edge rule that
denies /api/internal/ will no longer protect it.
"""
from django.urls import path

from .views import WebmailSSOView

urlpatterns = [
    path("sso/", WebmailSSOView.as_view(), name="webmail-sso"),
]
