from django.urls import path

from .views import InboundPolicyView, OutboundPolicyView, RateLimitStatusView

urlpatterns = [
    path("inbound/",     InboundPolicyView.as_view(),    name="smtp-inbound-policy"),
    path("outbound/",    OutboundPolicyView.as_view(),   name="smtp-outbound-policy"),
    path("rate-limits/", RateLimitStatusView.as_view(),  name="smtp-rate-limits"),
]
