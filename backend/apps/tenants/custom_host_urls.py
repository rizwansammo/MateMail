from django.urls import path

from .custom_host_views import (
    CustomHostnameDetailView,
    CustomHostnameListCreateView,
    CustomHostnameVerifyView,
)

urlpatterns = [
    path("", CustomHostnameListCreateView.as_view(), name="custom-hostname-list"),
    path("<uuid:pk>/", CustomHostnameDetailView.as_view(), name="custom-hostname-detail"),
    path("<uuid:pk>/verify/", CustomHostnameVerifyView.as_view(), name="custom-hostname-verify"),
]
