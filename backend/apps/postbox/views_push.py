"""
Native PostBox push endpoints.

    POST   /api/postbox/devices/               register or refresh this installation
    GET    /api/postbox/devices/               this mailbox's registrations
    DELETE /api/postbox/devices/<uuid>/        remove one (sign-out, permission off)

    POST   /api/internal/postbox/push-events/  the Native Engine reports a delivery

The first three are the signed-in mailbox's own, scoped by its PostBox session
exactly like everything else under /api/postbox/. The last is not a PostBox
endpoint at all: it has no user, lives under /api/internal/ (denied at the edge
by nginx) and answers only the engine's dedicated push credential.

A provider token or channel URI is written by POST and never read back: no
response here, and no log line anywhere, contains one.
"""
from __future__ import annotations

import hmac
import logging

from django.conf import settings
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import auth as postbox_auth
from . import push, realtime
from .models import PostBoxPushDevice, PostBoxPushEvent, PushPlatform, PushProvider, PushTokenType
from .push_providers import FCM_TOKEN_RE, is_wns_channel

logger = logging.getLogger(__name__)

#: The only platform/provider pairs that exist.
PROVIDER_FOR_PLATFORM = {
    PushPlatform.ANDROID: PushProvider.FCM,
    PushPlatform.WINDOWS: PushProvider.WNS,
}
TOKEN_TYPES = {
    PushProvider.FCM: {PushTokenType.REGISTRATION_TOKEN, PushTokenType.FID},
    PushProvider.WNS: {PushTokenType.CHANNEL_URI},
}
DEFAULT_TOKEN_TYPE = {
    PushProvider.FCM: PushTokenType.REGISTRATION_TOKEN,
    PushProvider.WNS: PushTokenType.CHANNEL_URI,
}

_UINT32_MAX = 4_294_967_295


def device_json(device: PostBoxPushDevice) -> dict:
    """What a registration looks like from outside. Never the token."""
    return {
        "id": str(device.id),
        "platform": device.platform,
        "provider": device.provider,
        "token_type": device.token_type,
        "enabled": device.enabled,
        "created_at": device.created_at.isoformat(),
        "updated_at": device.updated_at.isoformat(),
        "last_seen_at": device.last_seen_at.isoformat(),
    }


class DeviceRegistrationSerializer(serializers.Serializer):
    installation_id = serializers.UUIDField()
    platform = serializers.ChoiceField(choices=PushPlatform.choices)
    provider = serializers.ChoiceField(choices=PushProvider.choices)
    token_type = serializers.ChoiceField(choices=PushTokenType.choices, required=False)
    token = serializers.CharField(max_length=4096, trim_whitespace=False)

    def validate(self, data):
        provider = data["provider"]
        if PROVIDER_FOR_PLATFORM[data["platform"]] != provider:
            raise serializers.ValidationError(
                {"provider": "Android registers with fcm, Windows with wns."}
            )
        token_type = data.get("token_type") or DEFAULT_TOKEN_TYPE[provider]
        if token_type not in TOKEN_TYPES[provider]:
            raise serializers.ValidationError({"token_type": "Not a token type of this provider."})
        token = data["token"]
        if provider == PushProvider.FCM and not FCM_TOKEN_RE.match(token):
            raise serializers.ValidationError({"token": "Not an FCM registration token."})
        # Checked by shape only - nothing is fetched to validate a channel.
        if provider == PushProvider.WNS and not is_wns_channel(token):
            raise serializers.ValidationError(
                {"token": "A WNS channel is an https URI on notify.windows.com."}
            )
        data["token_type"] = token_type
        return data


class DeviceListView(APIView):
    """This mailbox's push registrations; POST registers or refreshes one."""

    permission_classes = [IsAuthenticated]
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]

    def get(self, request):
        postbox_auth.require_active_mailbox_permission(request, "read")
        devices = PostBoxPushDevice.objects.for_mailbox(request.mailbox)
        return Response({"results": [device_json(d) for d in devices]})

    def post(self, request):
        postbox_auth.require_active_mailbox_permission(request, "read")
        serializer = DeviceRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            device, created = push.register_device(
                mailbox=request.mailbox,
                session=request.postbox_session,
                installation_id=data["installation_id"],
                platform=data["platform"],
                provider=data["provider"],
                token_type=data["token_type"],
                token=data["token"],
            )
        except push.TooManyDevices:
            return Response(
                {"detail": "This mailbox has too many devices registered for notifications."},
                status=409,
            )
        logger.info(
            "PostBox push device %s %s for mailbox %s",
            device.id, "registered" if created else "refreshed", request.mailbox.pk,
        )
        return Response(device_json(device), status=201 if created else 200)


class DeviceDetailView(APIView):
    """
    Remove one registration.

    Scoped with `for_mailbox`, so another mailbox's registration id is a 404 -
    the ids are unguessable UUIDs, and scoping makes that irrelevant.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]

    def delete(self, request, device_id):
        postbox_auth.require_active_mailbox_permission(request, "read")
        deleted, _ = (
            PostBoxPushDevice.objects.for_mailbox(request.mailbox).filter(pk=device_id).delete()
        )
        if not deleted:
            return Response({"detail": "Not found."}, status=404)
        return Response(status=204)


# ── the engine's report ─────────────────────────────────────────────────────

def _ingest_authorized(request) -> bool:
    """The engine's dedicated push credential, compared in constant time."""
    expected = getattr(settings, "POSTBOX_PUSH_INGEST_SECRET", "")
    if not expected:
        return False
    provided = request.META.get("HTTP_X_POSTBOX_PUSH_SECRET", "")
    return hmac.compare_digest(provided.encode(), expected.encode())


class PushEventSerializer(serializers.Serializer):
    """
    Exactly what the engine sends. Anything else is refused, not dropped: a
    report carrying a subject or a sender means something upstream has started
    leaking content, and that should fail loudly.
    """

    event_id = serializers.UUIDField()
    event = serializers.ChoiceField(choices=("new_mail", "mailbox_changed"))
    mailbox = serializers.EmailField(max_length=254)
    folder = serializers.CharField(max_length=255, trim_whitespace=False)
    uid_validity = serializers.IntegerField(
        min_value=1, max_value=_UINT32_MAX, required=False, allow_null=True
    )
    uid = serializers.IntegerField(min_value=1, max_value=_UINT32_MAX, required=False, allow_null=True)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError({"detail": f"Unknown field(s): {', '.join(unknown)}"})
        return super().to_internal_value(data)

    def validate_folder(self, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise serializers.ValidationError("Not a folder name.")
        return value

    def validate(self, data):
        # Either both identify the message or neither does. Half an identity
        # would let the app guess at the other half.
        if (data.get("uid_validity") is None) != (data.get("uid") is None):
            raise serializers.ValidationError("uid_validity and uid go together.")
        return data


class PushEventIngestView(APIView):
    """
    POST /api/internal/postbox/push-events/

    Validate, store once, queue - and answer. It never waits for FCM, WNS, IMAP
    or anything else: those belong to Celery, and the engine's relay is waiting.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    #: A real report is about 200 bytes.
    MAX_BODY_BYTES = 4096

    def post(self, request):
        if not _ingest_authorized(request):
            return Response({"detail": "Forbidden."}, status=403)
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = self.MAX_BODY_BYTES + 1
        if length > self.MAX_BODY_BYTES:
            return Response({"detail": "Too large."}, status=413)

        serializer = PushEventSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        mailbox = postbox_auth.resolve_mailbox(data["mailbox"])
        if mailbox is not None:
            try:
                postbox_auth.assert_mailbox_may_sign_in(mailbox)
            except postbox_auth.MailboxUnavailable:
                mailbox = None
        if mailbox is None:
            return Response({"detail": "Unknown mailbox."}, status=404)

        # Mailbox-state changes are ephemeral browser wake-ups. They are not
        # remote mobile notifications and therefore do not need a retained
        # PostBoxPushEvent row. New-mail keeps the existing durable push path.
        if data["event"] == "mailbox_changed":
            event_id = str(data["event_id"])
            realtime.publish(
                mailbox,
                event_id=event_id,
                kind="mailbox_changed",
                folder=data["folder"],
                uid_validity=data.get("uid_validity"),
                uid=data.get("uid"),
            )
            return Response(
                {"accepted": True, "event_id": event_id, "duplicate": False},
                status=202,
            )

        with transaction.atomic():
            event, created = push.ingest(
                mailbox=mailbox,
                event_id=data["event_id"],
                event_type=data["event"],
                folder=data["folder"],
                uid_validity=data.get("uid_validity"),
                uid=data.get("uid"),
            )
        if event.mailbox_id != mailbox.pk:
            # The same id for a different mailbox is not a repeat. It cannot
            # happen by accident, so it is refused rather than merged.
            logger.warning("PostBox push: event %s conflicts with a stored event", event.event_id)
            return Response({"detail": "Conflict."}, status=409)

        # Wake active web clients independently of FCM/WNS delivery. Redis is
        # only a transient signal; the clients re-read IMAP as the authority.
        realtime.publish(
            mailbox,
            event_id=str(event.event_id),
            kind="new_mail",
            folder=event.folder,
            uid_validity=event.uid_validity,
            uid=event.uid,
        )
        return Response(
            {"accepted": True, "event_id": str(event.event_id), "duplicate": not created},
            status=202,
        )
