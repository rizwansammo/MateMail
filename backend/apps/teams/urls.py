from django.urls import path
from . import views

urlpatterns = [
    path("invites/", views.TeamInviteListView.as_view(), name="invite-list"),
    path("invites/preview/", views.TeamInvitePreviewView.as_view(), name="invite-preview"),
    path("invites/accept/", views.TeamInviteAcceptView.as_view(), name="invite-accept"),
    path("invites/<uuid:invite_id>/", views.TeamInviteRevokeView.as_view(), name="invite-revoke"),
    path("apikeys/", views.APIKeyListView.as_view(), name="apikey-list"),
    path("apikeys/<uuid:key_id>/", views.APIKeyRevokeView.as_view(), name="apikey-revoke"),
]
