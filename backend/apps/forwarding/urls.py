from django.urls import path

from .views import ForwardingRuleDetailView, ForwardingRuleListCreateView, ForwardingRuleStatusView

urlpatterns = [
    path("", ForwardingRuleListCreateView.as_view(), name="forwarding-list"),
    path("<uuid:pk>/", ForwardingRuleDetailView.as_view(), name="forwarding-detail"),
    path("<uuid:pk>/status/", ForwardingRuleStatusView.as_view(), name="forwarding-status"),
]
