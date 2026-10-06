from django.urls import path

from .views import (
    ForwardGroupDetailView,
    ForwardGroupListCreateView,
    ForwardGroupMemberDetailView,
    ForwardGroupMemberListCreateView,
    ForwardGroupPolicyView,
    ForwardGroupReprovisionView,
    ForwardGroupSenderDetailView,
    ForwardGroupSenderListCreateView,
    ForwardGroupStatusView,
)

urlpatterns = [
    path("", ForwardGroupListCreateView.as_view(), name="forward-group-list"),
    path("<uuid:pk>/", ForwardGroupDetailView.as_view(), name="forward-group-detail"),
    path("<uuid:pk>/status/", ForwardGroupStatusView.as_view(), name="forward-group-status"),
    path("<uuid:pk>/policy/", ForwardGroupPolicyView.as_view(), name="forward-group-policy"),
    path("<uuid:pk>/reprovision/", ForwardGroupReprovisionView.as_view(), name="forward-group-reprovision"),
    path("<uuid:pk>/members/", ForwardGroupMemberListCreateView.as_view(), name="forward-group-members"),
    path("<uuid:pk>/members/<uuid:member_pk>/", ForwardGroupMemberDetailView.as_view(), name="forward-group-member-detail"),
    path("<uuid:pk>/senders/", ForwardGroupSenderListCreateView.as_view(), name="forward-group-senders"),
    path("<uuid:pk>/senders/<uuid:sender_pk>/", ForwardGroupSenderDetailView.as_view(), name="forward-group-sender-detail"),
]
