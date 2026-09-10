"""
Public health routes only.

The detailed operator endpoint lives in internal_urls.py, mounted under
/api/internal/health/ — do not add it here, or the edge rule that denies
/api/internal/ will no longer protect it.
"""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.health, name="health"),
]
