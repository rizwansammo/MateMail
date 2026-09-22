"""
PostBox authentication: who is signed in, and what they are allowed to be.

THE IDENTITY IS A MAILBOX, NOT A USER
    A MateMail Workspace account and a mailbox are different things. An
    organization administrator has the first; an employee has the second; a
    person may have both, or either, and one says nothing about the other.

    So a PostBox session is bound to a `Mailbox` row and nothing else. There is
    no request field naming a mailbox, no way to switch mailbox after signing
    in, and the Workspace JWT is not accepted here as proof of anything. The
    only way to obtain a PostBox session is to present that mailbox's own
    password to Dovecot.

WHAT IS CHECKED, AND WHEN
    Authorisation is re-checked on EVERY request, not only at sign-in. A
    mailbox suspended, an organization suspended or a mailbox deleted while
    somebody is reading their mail must take effect immediately — the window
    between those two moments is exactly when it matters.
"""
from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.tenants.dedicated import tenant_matches_request
from apps.security import ratelimit
from apps.security.client_ip import get_client_ip

from . import imap
from .models import PostBoxSession

logger = logging.getLogger(__name__)

#: The cookie name is prefixed `__Host-` deliberately. A browser only accepts
#: that prefix when the cookie is Secure, has Path=/ and carries NO Domain
#: attribute — which means it is locked to exactly postbox.matemail.online and
#: cannot be set by, or sent to, any other subdomain. It is the one cookie
#: attribute a sibling host cannot override.
SESSION_COOKIE_NAME = "__Host-postbox_session"

#: One message for every sign-in failure. Wrong password, unknown address,
#: disabled mailbox and suspended organization must be indistinguishable, or
#: the login form becomes a way to enumerate a company's staff.
GENERIC_SIGNIN_FAILURE = "Those details are not correct."


class MailboxUnavailable(Exception):
    """The mailbox exists but may not be used right now. Carries a safe message."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def resolve_mailbox(address: str) -> Mailbox | None:
    """The mailbox row for an address, or None. Never raises on a bad address."""
    normalised = (address or "").strip().lower()
    if not normalised or "@" not in normalised:
        return None
    return (
        Mailbox.objects.select_related("tenant", "domain")
        .filter(email__iexact=normalised)
        .first()
    )


def assert_mailbox_may_sign_in(mailbox: Mailbox) -> None:
    """
    Every reason a provisioned mailbox may still not be used.

    Raises `MailboxUnavailable`. The caller converts it to the same generic
    message as a wrong password — the distinction exists for the logs, not for
    the person at the form.
    """
    if mailbox.status == MailboxStatus.SUSPENDED:
        raise MailboxUnavailable("This mailbox has been suspended by MateMail.")
    if mailbox.status != MailboxStatus.ACTIVE:
        raise MailboxUnavailable("This mailbox is not active.")
    if not mailbox.mail_engine_provisioned:
        raise MailboxUnavailable("This mailbox is still being set up.")

    tenant = mailbox.tenant
    if tenant is None:
        raise MailboxUnavailable("This mailbox is not attached to an organization.")
    # `can_use_mail` is the single authority on whether an organization may use
    # mail at all — suspended, unapproved or rejected all fail here, and the
    # rule is not re-implemented.
    if not tenant.can_use_mail:
        raise MailboxUnavailable("This organization's mail service is not active.")


def sign_in(address: str, password: str, *, request=None, remember: bool = False):
    """
    Verify a mailbox password and start a session.

    Returns (raw_token, session). Raises `MailboxUnavailable` for every
    failure, always with the generic message.

    The password reaches Dovecot and nothing else. It is not written to the
    database, not cached, not put in the session row, and not kept in memory
    past this call — which is the whole reason the master identity exists.
    """
    client_ip = get_client_ip(request) if request is not None else None
    mailbox = resolve_mailbox(address)
    if mailbox is not None and request is not None and not tenant_matches_request(request, mailbox.tenant):
        # Treat a mailbox from another tenant exactly like an unknown address on
        # a dedicated customer hostname. We still perform the password check
        # below so the hostname cannot be used as a mailbox-enumeration oracle.
        mailbox = None

    # The password check runs even for an unknown address, so a request for a
    # non-existent mailbox costs the same time as a real one. Skipping it is a
    # timing oracle that answers "does this address exist".
    authenticated = imap.authenticate((address or "").strip(), password)

    if mailbox is None or not authenticated:
        logger.info(
            "PostBox sign-in refused for %s from %s (known=%s, auth=%s)",
            _redact(address), client_ip, mailbox is not None, authenticated,
        )
        raise MailboxUnavailable(GENERIC_SIGNIN_FAILURE)

    try:
        assert_mailbox_may_sign_in(mailbox)
    except MailboxUnavailable as exc:
        # Logged with the real reason, answered with the generic one.
        logger.info("PostBox sign-in blocked for %s: %s", _redact(address), exc.message)
        raise MailboxUnavailable(GENERIC_SIGNIN_FAILURE) from exc

    raw, session = PostBoxSession.issue(
        mailbox,
        remembered=remember,
        user_agent=(request.META.get("HTTP_USER_AGENT", "") if request else ""),
        ip_address=client_ip,
    )

    # Authoritative last-login belongs to the mailbox record, which the
    # Workspace and the Platform Console both read.
    Mailbox.objects.filter(pk=mailbox.pk).update(last_login=timezone.now())

    logger.info("PostBox session opened for %s from %s", _redact(address), client_ip)
    return raw, session


def session_for_token(raw_token: str) -> PostBoxSession | None:
    """An active session for this token, or None. Expiry and revocation included."""
    if not raw_token:
        return None
    session = (
        PostBoxSession.objects.select_related("mailbox", "mailbox__tenant", "mailbox__domain")
        .filter(token_hash=PostBoxSession.hash_token(raw_token))
        .first()
    )
    if session is None or not session.is_active:
        return None
    return session


class PostBoxSessionAuthentication(authentication.BaseAuthentication):
    """
    DRF authentication for PostBox, backed by the session cookie.

    Deliberately NOT JWT. A PostBox session must be revocable the instant an
    operator or the owner says so, and a self-contained token cannot be
    revoked without a server-side list — at which point it is a session with
    extra steps. `request.postbox_session` and `request.mailbox` are what the
    views read; nothing downstream ever takes a mailbox from the request body.
    """

    def authenticate(self, request):
        raw = request.COOKIES.get(SESSION_COOKIE_NAME)
        if not raw:
            return None

        session = session_for_token(raw)
        if session is None:
            raise exceptions.AuthenticationFailed("Your session has ended. Please sign in again.")

        mailbox = session.mailbox
        try:
            # Re-checked per request, not merely at sign-in: a suspension has
            # to take effect while somebody is reading, not at their next login.
            assert_mailbox_may_sign_in(mailbox)
        except MailboxUnavailable as exc:
            session.revoke()
            raise exceptions.AuthenticationFailed(exc.message) from exc

        session.touch()
        request.postbox_session = session
        request.mailbox = mailbox

        # DRF needs *something* user-shaped. `PostBoxIdentity` is not a Django
        # user and is not authenticated against `accounts.User` — returning a
        # real User here would quietly make a mailbox look like a Workspace
        # login to every permission class in the project.
        return (PostBoxIdentity(mailbox, session), None)


class PostBoxIdentity:
    """
    The authenticated principal: one mailbox.

    Implements just enough of Django's user protocol for DRF, and deliberately
    nothing more. `is_staff`, `is_superuser` and `is_platform_admin` are all
    False and always will be — a mailbox session must never satisfy a
    permission written for a Workspace or Platform account.
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False
    is_platform_admin = False

    def __init__(self, mailbox: Mailbox, session: PostBoxSession):
        self.mailbox = mailbox
        self.session = session
        self.email = mailbox.email
        self.pk = mailbox.pk
        self.id = mailbox.pk

    def __str__(self):
        return self.email

    def has_perm(self, *args, **kwargs) -> bool:
        return False

    def has_module_perms(self, *args, **kwargs) -> bool:
        return False


def set_session_cookie(response, raw_token: str, session: PostBoxSession):
    """
    Attach the session cookie.

    HttpOnly so script cannot read it, Secure so it never crosses plaintext,
    SameSite=Lax so a cross-site form post cannot act as the signed-in
    mailbox while ordinary navigation to PostBox still works. No Domain
    attribute, which is what `__Host-` requires and what keeps the cookie off
    every other matemail.online host.
    """
    from django.conf import settings

    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_token,
        max_age=int((session.expires_at - timezone.now()).total_seconds()),
        secure=not settings.DEBUG,
        httponly=True,
        samesite="Lax",
        path="/",
    )
    return response


def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return response


def revoke_other_sessions(mailbox: Mailbox, keep: PostBoxSession | None = None) -> int:
    """End every session for this mailbox except, optionally, the current one."""
    query = PostBoxSession.objects.filter(mailbox=mailbox, revoked_at__isnull=True)
    if keep is not None:
        query = query.exclude(pk=keep.pk)
    return query.update(revoked_at=timezone.now())


def _redact(address: str) -> str:
    _, _, domain = (address or "").partition("@")
    return f"<user>@{domain}" if domain else "<address>"


# ── abuse limits ────────────────────────────────────────────────────────────

def enforce_signin_limits(request, address: str) -> None:
    """
    Rate-limit sign-in by IP and by address.

    Counted on every attempt rather than only on failure, because each attempt
    costs a real IMAP connection to Dovecot — unlike the Workspace login, where
    the cost is a local hash.
    """
    from rest_framework.exceptions import Throttled

    from apps.security.limits import POSTBOX_SIGNIN_PER_ACCOUNT, POSTBOX_SIGNIN_PER_IP

    client_ip = get_client_ip(request)
    account = (address or "").strip().lower()

    for rule, identity in (
        (POSTBOX_SIGNIN_PER_IP, client_ip),
        (POSTBOX_SIGNIN_PER_ACCOUNT, account),
    ):
        decision = ratelimit.hit(
            rule.bucket, identity, limit=rule.limit, window=rule.window
        )
        if not decision.allowed:
            raise Throttled(
                wait=decision.retry_after,
                detail="Too many sign-in attempts. Please wait and try again.",
            )
