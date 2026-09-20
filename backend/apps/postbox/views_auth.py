"""
PostBox authentication endpoints.

    POST /api/postbox/auth/login/       mailbox address + password -> session
    POST /api/postbox/auth/logout/      end this session
    POST /api/postbox/auth/logout-all/  end every session for this mailbox
    GET  /api/postbox/auth/me/          who is signed in, and their mailbox

There is no signup here, no organization creation and no password recovery.
PostBox is for people who already have a mailbox, and a mailbox password is
reset by their organization's MateMail administrator — emailing a reset link
to the mailbox somebody cannot open is not a recovery mechanism.
"""
from __future__ import annotations

import logging

from rest_framework import serializers
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import auth as postbox_auth
from .models import PostBoxPreference, PostBoxSession
from .serializers import MailboxProfileSerializer

logger = logging.getLogger(__name__)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)
    remember = serializers.BooleanField(required=False, default=False)


class PostBoxLoginView(APIView):
    """
    Stage one and only. A correct mailbox password opens a session.

    Unlike the Platform Console there is no second factor here: this is a
    mailbox, and requiring an emailed code to read email is circular. The
    controls that apply instead are the rate limits, the generic failure
    message and the session model.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        postbox_auth.enforce_signin_limits(request, data["email"])

        try:
            raw, session = postbox_auth.sign_in(
                data["email"],
                data["password"],
                request=request,
                remember=data["remember"],
            )
        except postbox_auth.MailboxUnavailable as exc:
            return Response({"detail": exc.message}, status=401)

        mailbox = session.mailbox

        # The folders a mailbox needs are created on first sign-in and are
        # idempotent afterwards, so a brand-new mailbox opens with Sent,
        # Drafts, Trash, Junk, Archive and Scheduled rather than only INBOX.
        # A failure here must not block sign-in — the mailbox still works.
        try:
            from . import imap

            with imap.open_mailbox(mailbox.email) as connection:
                created = connection.ensure_standard_folders()
            if created:
                logger.info("PostBox created folders %s for %s", created, mailbox.pk)
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            logger.warning("PostBox folder provisioning failed for %s: %r", mailbox.pk, exc)

        PostBoxPreference.objects.get_or_create(mailbox=mailbox)

        response = Response({
            "mailbox": MailboxProfileSerializer(mailbox).data,
            "session": {
                "id": str(session.id),
                "expires_at": session.expires_at.isoformat(),
                "remembered": session.remembered,
            },
        })
        return postbox_auth.set_session_cookie(response, raw, session)


class PostBoxLogoutView(APIView):
    """End this session. Idempotent — signing out twice is not an error."""

    permission_classes = [AllowAny]
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]

    def post(self, request):
        session = getattr(request, "postbox_session", None)
        if session is not None:
            session.revoke()
        return postbox_auth.clear_session_cookie(Response({"detail": "Signed out."}))


class PostBoxLogoutAllView(APIView):
    """
    Sign out everywhere, including here.

    The current session is revoked too. "Sign out everywhere" that left the
    device you pressed it on still signed in would be a surprising reading of
    the word everywhere, and the usual reason for pressing it is that a device
    was lost.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]

    def post(self, request):
        revoked = postbox_auth.revoke_other_sessions(request.mailbox)
        request.postbox_session.revoke()
        logger.info(
            "PostBox: all sessions revoked for mailbox %s (%d others)",
            request.mailbox.pk, revoked,
        )
        return postbox_auth.clear_session_cookie(
            Response({"detail": "Signed out on all devices.", "revoked": revoked + 1})
        )


class PostBoxMeView(APIView):
    """The signed-in mailbox and its preferences. The frontend's boot call."""

    permission_classes = [IsAuthenticated]
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]

    def get(self, request):
        from .serializers import PreferenceSerializer

        preference, _ = PostBoxPreference.objects.get_or_create(mailbox=request.mailbox)
        return Response({
            "mailbox": MailboxProfileSerializer(request.mailbox).data,
            "preferences": PreferenceSerializer(preference).data,
            "session": {
                "id": str(request.postbox_session.id),
                "expires_at": request.postbox_session.expires_at.isoformat(),
            },
        })


class PostBoxSessionListView(APIView):
    """
    Active sessions, for Settings → Security.

    Token hashes are never returned. The current session is marked so somebody
    can tell which row is the device they are holding, which is the difference
    between a useful list and a dangerous one.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]

    def get(self, request):
        sessions = (
            PostBoxSession.objects.for_mailbox(request.mailbox)
            .filter(revoked_at__isnull=True)
            .order_by("-last_seen_at")
        )
        current_id = request.postbox_session.id
        return Response({"results": [
            {
                "id": str(s.id),
                "created_at": s.created_at.isoformat(),
                "last_seen_at": s.last_seen_at.isoformat(),
                "expires_at": s.expires_at.isoformat(),
                "ip_address": s.ip_address,
                "user_agent": s.user_agent,
                "remembered": s.remembered,
                "current": s.id == current_id,
                "active": s.is_active,
            }
            for s in sessions if s.is_active
        ]})


class PostBoxSessionRevokeView(APIView):
    """
    Revoke one session by id.

    Scoped with `for_mailbox`, so a session id belonging to another mailbox is
    a 404 rather than a revocation — the id is a UUID and unguessable, but
    scoping is what makes that irrelevant.
    """

    permission_classes = [IsAuthenticated]
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]

    def delete(self, request, session_id):
        session = (
            PostBoxSession.objects.for_mailbox(request.mailbox)
            .filter(pk=session_id)
            .first()
        )
        if session is None:
            return Response({"detail": "Not found."}, status=404)

        session.revoke()
        response = Response({"detail": "Session ended."})
        if session.id == request.postbox_session.id:
            # Revoking your own session should also clear the cookie, or the
            # browser keeps sending a token that no longer works.
            postbox_auth.clear_session_cookie(response)
        return response
