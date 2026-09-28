"""
PostBox routes, all under /api/postbox/.

One namespace for the whole mailbox-user surface, rather than mailbox
endpoints scattered through the apps that own the underlying models. A person
reading the URL conf can see the entire thing a signed-in mailbox can do.

Folder names appear in paths as `<path:name>` because a folder name may
contain a slash — `Projects/2026` is one folder, not two path segments.
"""
from django.urls import path

from .views_auth import (
    PostBoxLoginView,
    PostBoxLogoutAllView,
    PostBoxLogoutView,
    PostBoxMeView,
    PostBoxSessionListView,
    PostBoxSessionRevokeView,
)
from .views_compose import (
    DraftView,
    ReplyContextView,
    ScheduledDetailView,
    ScheduledListView,
    SendView,
)
from .views_push import DeviceDetailView, DeviceListView
from .views_mail import (
    AttachmentView,
    FolderDetailView,
    FolderListView,
    MessageActionView,
    MessageDetailView,
    MessageListView,
    MessageRawView,
    MessageRemoteImageTrustView,
)
from .views_settings import (
    ContactDetailView,
    ContactListView,
    ForwardingView,
    IdentityListView,
    MailboxAccountView,
    PasswordChangeView,
    PreferenceView,
    RecipientSuggestionView,
    RuleDetailView,
    RuleListView,
    SignatureDetailView,
    SignatureImageView,
    SignatureListView,
    VacationView,
)

urlpatterns = [
    # ── authentication ──────────────────────────────────────────────────────
    path("auth/login/", PostBoxLoginView.as_view(), name="postbox-login"),
    path("auth/logout/", PostBoxLogoutView.as_view(), name="postbox-logout"),
    path("auth/logout-all/", PostBoxLogoutAllView.as_view(), name="postbox-logout-all"),
    path("auth/me/", PostBoxMeView.as_view(), name="postbox-me"),

    # ── mail ────────────────────────────────────────────────────────────────
    path("folders/", FolderListView.as_view(), name="postbox-folders"),
    path("folders/<path:name>/", FolderDetailView.as_view(), name="postbox-folder"),

    path("messages/", MessageListView.as_view(), name="postbox-messages"),
    # Before the generic message route, so "action" is never read as a folder.
    path("messages/action/<str:action>/", MessageActionView.as_view(),
         name="postbox-message-action"),
    path("messages/<path:folder>/<int:uid>/raw/", MessageRawView.as_view(),
         name="postbox-message-raw"),
    path("messages/<path:folder>/<int:uid>/attachments/<str:part_id>/",
         AttachmentView.as_view(), name="postbox-attachment"),
    path("messages/<path:folder>/<int:uid>/reply-context/", ReplyContextView.as_view(),
         name="postbox-reply-context"),
    path("messages/<path:folder>/<int:uid>/remote-images/trust/",
         MessageRemoteImageTrustView.as_view(), name="postbox-remote-image-trust"),
    path("messages/<path:folder>/<int:uid>/", MessageDetailView.as_view(),
         name="postbox-message"),

    # ── composing ───────────────────────────────────────────────────────────
    path("compose/send/", SendView.as_view(), name="postbox-send"),
    path("drafts/", DraftView.as_view(), name="postbox-drafts"),
    path("drafts/<int:uid>/", DraftView.as_view(), name="postbox-draft"),
    path("scheduled/", ScheduledListView.as_view(), name="postbox-scheduled"),
    path("scheduled/<uuid:scheduled_id>/", ScheduledDetailView.as_view(),
         name="postbox-scheduled-detail"),

    # ── settings ────────────────────────────────────────────────────────────
    path("preferences/", PreferenceView.as_view(), name="postbox-preferences"),
    path("account/", MailboxAccountView.as_view(), name="postbox-account"),
    path("identities/", IdentityListView.as_view(), name="postbox-identities"),
    path("forwarding/", ForwardingView.as_view(), name="postbox-forwarding"),

    path("signatures/", SignatureListView.as_view(), name="postbox-signatures"),
    path("signatures/<uuid:pk>/", SignatureDetailView.as_view(), name="postbox-signature"),
    # Bytes in and out for an image signature. Separate from the JSON resource
    # so the upload can be validated as bytes and the download served with a
    # real content type, without base64 riding on every list response.
    path(
        "signatures/<uuid:pk>/image/",
        SignatureImageView.as_view(),
        name="postbox-signature-image",
    ),

    path("contacts/", ContactListView.as_view(), name="postbox-contacts"),
    path("contacts/<uuid:pk>/", ContactDetailView.as_view(), name="postbox-contact"),
    path("contacts/suggest/", RecipientSuggestionView.as_view(), name="postbox-suggest"),

    path("rules/", RuleListView.as_view(), name="postbox-rules"),
    path("rules/<uuid:pk>/", RuleDetailView.as_view(), name="postbox-rule"),
    path("vacation/", VacationView.as_view(), name="postbox-vacation"),

    # ── native push registrations ───────────────────────────────────────────
    path("devices/", DeviceListView.as_view(), name="postbox-devices"),
    path("devices/<uuid:device_id>/", DeviceDetailView.as_view(), name="postbox-device"),

    # ── security ────────────────────────────────────────────────────────────
    path("security/change-password/", PasswordChangeView.as_view(),
         name="postbox-change-password"),
    path("security/sessions/", PostBoxSessionListView.as_view(),
         name="postbox-sessions"),
    path("security/sessions/<uuid:session_id>/", PostBoxSessionRevokeView.as_view(),
         name="postbox-session-revoke"),
]
