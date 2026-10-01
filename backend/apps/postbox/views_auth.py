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

from apps.tenants.dedicated import tenant_matches_request

from . import auth as postbox_auth
from .models import PostBoxPreference, PostBoxSession
from .serializers import MailboxProfileSerializer

logger = logging.getLogger(__name__)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(trim_whitespace=False)
    remember = serializers.BooleanField(required=False, default=False)


class AccountSwitchSerializer(serializers.Serializer):
    session_id = serializers.UUIDField()


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
        postbox_auth.set_session_cookie(response, raw, session)
        postbox_auth.remember_session_on_device(response, request, raw, session)
        return response


class PostBoxLogoutView(APIView):
    """End this session. Idempotent — signing out twice is not an error."""

    permission_classes = [AllowAny]
    authentication_classes = [postbox_auth.PostBoxSessionAuthentication]

    def post(self, request):
        session = getattr(request, "postbox_session", None)
        response = Response({"detail": "Signed out."})
        if session is not None:
            session.revoke()
            postbox_auth.clear_saved_account_cookie(response, session.id)
        return postbox_auth.clear_session_cookie(response)


class PostBoxAccountListView(APIView):
    """
    Accounts already authenticated on this browser.

    Only mailbox profile metadata is returned. The retained session capability
    never leaves HttpOnly cookies, so JavaScript cannot copy or export it.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request):
        retained, stale = postbox_auth.saved_account_sessions(request)

        active_raw = request.COOKIES.get(postbox_auth.SESSION_COOKIE_NAME)
        active = postbox_auth.session_for_token(active_raw) if active_raw else None
        if active is not None:
            if not tenant_matches_request(request, active.mailbox.tenant):
                active = None
            else:
                try:
                    postbox_auth.assert_mailbox_may_sign_in(active.mailbox)
                except postbox_auth.MailboxUnavailable:
                    active.revoke()
                    active = None

        # Current first, then most recently used. One mailbox appears once even
        # if an old browser version left two retained sessions behind.
        candidates = []
        if active is not None:
            candidates.append((postbox_auth.saved_account_cookie_name(active.id), active_raw, active))
        candidates.extend(
            sorted(retained, key=lambda row: row[2].last_seen_at, reverse=True)
        )

        seen_mailboxes = set()
        rows = []
        duplicate_sessions = []
        for name, raw_token, session in candidates:
            if session.mailbox_id in seen_mailboxes:
                if active is None or session.id != active.id:
                    duplicate_sessions.append((name, session))
                continue
            seen_mailboxes.add(session.mailbox_id)
            rows.append({
                "session_id": str(session.id),
                "mailbox": MailboxProfileSerializer(session.mailbox).data,
                "expires_at": session.expires_at.isoformat(),
                "remembered": session.remembered,
                "current": bool(active is not None and session.id == active.id),
            })

        response = Response({
            "results": rows,
            "current_session_id": str(active.id) if active is not None else None,
        })

        for name in stale:
            response.delete_cookie(name, path="/")
        for name, duplicate in duplicate_sessions:
            duplicate.revoke()
            response.delete_cookie(name, path="/")

        # Promote a pre-feature active session into the switcher. Its absolute
        # expiry is unchanged; this does not silently extend authentication.
        if active is not None:
            postbox_auth.set_saved_account_cookie(response, active_raw, active)
        return response


class PostBoxAccountSwitchView(APIView):
    """Activate another independently authenticated session retained on this browser."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = AccountSwitchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wanted = serializer.validated_data["session_id"]

        retained, stale = postbox_auth.saved_account_sessions(request)
        selected = next(
            (
                (raw_token, session)
                for _, raw_token, session in retained
                if session.id == wanted
            ),
            None,
        )

        response = Response(
            {"detail": "That account is no longer available on this device."},
            status=401,
        )
        for name in stale:
            response.delete_cookie(name, path="/")

        if selected is None:
            postbox_auth.clear_saved_account_cookie(response, wanted)
            return response

        raw_token, session = selected
        response = Response({
            "mailbox": MailboxProfileSerializer(session.mailbox).data,
            "session": {
                "id": str(session.id),
                "expires_at": session.expires_at.isoformat(),
                "remembered": session.remembered,
            },
        })
        postbox_auth.set_session_cookie(response, raw_token, session)
        postbox_auth.remember_session_on_device(response, request, raw_token, session)
        return response


class PostBoxLogoutDeviceView(APIView):
    """Sign out every PostBox account retained by this browser, but no other device."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request):
        retained, _ = postbox_auth.saved_account_sessions(request)
        revoked = set()
        response = Response({"detail": "Signed out of all accounts on this device."})

        for name in list(request.COOKIES):
            if name.startswith(postbox_auth.SAVED_ACCOUNT_COOKIE_PREFIX):
                response.delete_cookie(name, path="/")

        for _, _, session in retained:
            if session.id not in revoked:
                session.revoke()
                revoked.add(session.id)

        active_raw = request.COOKIES.get(postbox_auth.SESSION_COOKIE_NAME)
        active = postbox_auth.session_for_token(active_raw) if active_raw else None
        if active is not None and active.id not in revoked:
            active.revoke()

        postbox_auth.clear_session_cookie(response)
        return response


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
        response = Response({"detail": "Signed out on all devices.", "revoked": revoked + 1})
        for name, _, session in postbox_auth.saved_account_sessions(request)[0]:
            if session.mailbox_id == request.mailbox.id:
                response.delete_cookie(name, path="/")
        postbox_auth.clear_session_cookie(response)
        return response


def mail_client_settings(address: str) -> dict:
    """
    What to type into Outlook, Apple Mail or Thunderbird.

    Derived from `MAIL_HOSTNAME` rather than written out in the frontend, so
    there is one answer and it follows the deployment. The ports and
    encryption are fixed by what the Native Engine actually listens on
    (DEC-042): IMAP 993 implicit TLS, submission 587 STARTTLS. There is no
    POP3 and no 465, so neither is offered here — a settings page that lists
    a port the server does not answer on produces a support ticket.

    The username is the full address every time. MateMail authenticates on
    the whole address, and a client configured with the local part alone
    fails with a password error that says nothing about the real cause.
    """
    from django.conf import settings as django_settings

    host = django_settings.MAIL_HOSTNAME
    return {
        "username": address,
        "imap": {"server": host, "port": 993, "encryption": "SSL/TLS"},
        "smtp": {
            "server": host,
            "port": 587,
            "encryption": "STARTTLS",
            "auth_required": True,
        },
    }


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
            "mail_client": mail_client_settings(request.mailbox.email),
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
