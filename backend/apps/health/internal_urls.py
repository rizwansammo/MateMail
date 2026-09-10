"""
Internal-only health routes.

Mounted at /api/internal/health/ so the edge nginx rule that denies
/api/internal/ covers them. Authenticates with INTERNAL_API_SECRET.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.health_internal, name="health-internal"),
]
