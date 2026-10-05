from django.urls import path

from .custom_host_views import (
    CustomHostnameActivationPendingInternalView,
    CustomHostnameAuthorizeInternalView,
    CustomHostnameDeactivationPendingInternalView,
    CustomHostnamePendingInternalView,
    CustomHostnameStateInternalView,
)

urlpatterns = [
    path("pending/", CustomHostnamePendingInternalView.as_view(), name="custom-hostname-pending"),
    path(
        "activation-pending/",
        CustomHostnameActivationPendingInternalView.as_view(),
        name="custom-hostname-activation-pending",
    ),
    path(
        "deactivation-pending/",
        CustomHostnameDeactivationPendingInternalView.as_view(),
        name="custom-hostname-deactivation-pending",
    ),
    path("authorize/", CustomHostnameAuthorizeInternalView.as_view(), name="custom-hostname-authorize"),
    path("<uuid:pk>/state/", CustomHostnameStateInternalView.as_view(), name="custom-hostname-state"),
]
