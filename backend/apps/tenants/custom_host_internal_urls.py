from django.urls import path

from .custom_host_views import (
    CustomHostnameActivationPendingInternalView,
    CustomHostnameAuthorizeInternalView,
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
    path("authorize/", CustomHostnameAuthorizeInternalView.as_view(), name="custom-hostname-authorize"),
    path("<uuid:pk>/state/", CustomHostnameStateInternalView.as_view(), name="custom-hostname-state"),
]
