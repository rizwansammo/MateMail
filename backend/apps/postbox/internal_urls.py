"""
PostBox's internal routes, mounted at /api/internal/postbox/.

Under /api/internal/ so the edge nginx rule that denies that prefix covers
them. They answer no user: only the Native Engine, with its own credential.
"""
from django.urls import path

from .views_push import PushEventIngestView

urlpatterns = [
    path("push-events/", PushEventIngestView.as_view(), name="postbox-push-ingest"),
]
