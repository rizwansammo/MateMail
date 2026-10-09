"""
PostBox authentication: who is signed in, and what they are allowed to be.

THE IDENTITY IS A MAILBOX, NOT A USER
    A MateMail Workspace account and a mailbox are different things. An
    organization administrator has the first; an employee has the second; a
    person may have both, or either, and one says nothing about the other.

    So every PostBox session is bound to exactly one Mailbox row and nothing
    else. Multi-account switching does not weaken that boundary: the browser
    may retain several independently authenticated session capabilities, but
    only one is active on a request and switching merely activates another
    already-authenticated session. No request can name an arbitrary mailbox,
    and a Workspace JWT is never accepted as proof of mailbox access.

WHAT IS CHECKED, AND WHEN
    Authorisation is re-checked on EVERY request, not only at sign-in. A
    mailbox suspended, an organization suspended or a mailbox deleted while
    somebody is reading their mail must take effect immediately — the window
    between those two moments is exactly when it matters.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.mail_directory.models import AccessGrantKind, MailboxAccessGrant
from apps.mail_directory.services import MailboxPermissions
from apps.mailboxes.models import Mailbox, MailboxKind, MailboxStatus
from apps.tenants.host_binding import tenant_matches_request
from apps.security import ratelimit
from apps.security.client_ip import get_client_ip

from . import imap
from .models import PostBoxPushDevice, PostBoxSession

logger = logging.getLogger(__name__)

#: The cookie name is prefixed `__Host-` deliberately. A browser only accepts
#: that prefix when the cookie is Secure, has Path=/ and carries NO Domain
#: attribute — which means it is locked to exactly postbox.matemail.pro and
#: cannot be set by, or sent to, any other subdomain. It is the one cookie
#: attribute a sibling host cannot override.
SESSION_COOKIE_NAME = "__Host-postbox_session"

#: Retained account sessions use one opaque HttpOnly cookie per independently
#: authenticated mailbox. JavaScript can list accounts only through the API;
#: it never receives a session token. Each name is bound to the session UUID so
#: a copied value under a different cookie name is rejected.
SAVED_ACCOUNT_COOKIE_PREFIX = "__Host-postbox_account_"
MAX_SAVED_ACCOUNTS = 8

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
    if mailbox.kind != MailboxKind.PERSONAL:
        raise MailboxUnavailable("This mailbox cannot be signed into directly.")
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
        PostBoxSession.objects.select_related(
            "mailbox",
            "mailbox__tenant",
            "mailbox__domain",
            "active_mailbox",
            "active_mailbox__tenant",
            "active_mailbox__domain",
        )
        .filter(token_hash=PostBoxSession.hash_token(raw_token))
        .first()
    )
    if session is None or not session.is_active:
        return None
    return session


def _clear_active_mailbox(session: PostBoxSession) -> None:
    if session.active_mailbox_id is not None:
        session.active_mailbox = None
        session.save(update_fields=["active_mailbox"])


def _access_grant_type_for_target(target: Mailbox) -> str:
    if target.kind == MailboxKind.TEAM_BOX:
        return AccessGrantKind.TEAM_BOX
    if target.kind == MailboxKind.PERSONAL:
        return AccessGrantKind.DELEGATION
    raise MailboxUnavailable("That mailbox cannot be opened through this session.")


def _shared_mailbox_permissions(
    identity: Mailbox,
    target: Mailbox,
) -> MailboxPermissions:
    if identity.tenant_id != target.tenant_id:
        return MailboxPermissions()

    grant_type = _access_grant_type_for_target(target)
    grant = (
        MailboxAccessGrant.objects
        .filter(
            tenant_id=target.tenant_id,
            grantee_mailbox=identity,
            target_mailbox=target,
            grant_type=grant_type,
            active=True,
        )
        .first()
    )
    if grant is None:
        return MailboxPermissions()

    return MailboxPermissions(
        can_read=grant.can_read,
        can_manage=grant.can_manage,
        can_send_as=grant.can_send_as,
        can_send_on_behalf=grant.can_send_on_behalf,
    )


# Kept as a small compatibility helper for Phase D callers/tests.
def _team_box_permissions(identity: Mailbox, target: Mailbox) -> MailboxPermissions:
    if target.kind != MailboxKind.TEAM_BOX:
        return MailboxPermissions()
    return _shared_mailbox_permissions(identity, target)


def _assert_shared_mailbox_available(
    identity: Mailbox,
    target: Mailbox,
) -> MailboxPermissions:
    grant_type = _access_grant_type_for_target(target)

    if identity.tenant_id != target.tenant_id:
        raise MailboxUnavailable("That mailbox is not in this organization.")
    if target.status != MailboxStatus.ACTIVE:
        raise MailboxUnavailable("That mailbox is not active.")
    if not target.mail_engine_provisioned:
        raise MailboxUnavailable("That mailbox is still being set up.")
    if target.tenant is None or not target.tenant.can_use_mail:
        raise MailboxUnavailable("This organization's mail service is not active.")

    permissions = _shared_mailbox_permissions(identity, target)
    if not any((
        permissions.can_read,
        permissions.can_manage,
        permissions.can_send_as,
        permissions.can_send_on_behalf,
    )):
        label = "TeamBox" if grant_type == AccessGrantKind.TEAM_BOX else "delegated mailbox"
        raise MailboxUnavailable(f"You no longer have access to that {label}.")
    return permissions


def active_mailbox_for_session(
    session: PostBoxSession,
) -> tuple[Mailbox, MailboxPermissions]:
    """
    Resolve the mailbox currently being used without changing who authenticated.

    A revoked grant must never make an in-flight destructive request fall back
    to the personal mailbox. The caller gets a refusal for this request; the
    stored shared-mailbox selection is cleared so the next request returns to the
    signed-in personal mailbox.
    """
    identity = session.mailbox
    target = session.active_mailbox
    if target is None or target.pk == identity.pk:
        return identity, MailboxPermissions.owner()

    try:
        permissions = _assert_shared_mailbox_available(identity, target)
    except MailboxUnavailable:
        _clear_active_mailbox(session)
        raise

    return target, permissions


def switch_active_mailbox(
    session: PostBoxSession,
    target: Mailbox | None,
) -> tuple[Mailbox, MailboxPermissions]:
    identity = session.mailbox

    if target is None or target.pk == identity.pk:
        _clear_active_mailbox(session)
        return identity, MailboxPermissions.owner()

    permissions = _assert_shared_mailbox_available(identity, target)
    session.active_mailbox = target
    session.save(update_fields=["active_mailbox"])
    return target, permissions


def require_active_mailbox_permission(request, permission: str) -> None:
    """
    Enforce one cross-mailbox permission after session authentication.

    Personal mailbox access to itself is intrinsic. TeamBox and Delegation
    grants are resolved fresh on every request by PostBoxSessionAuthentication.
    """
    if getattr(request, "postbox_active_mailbox_invalid", False):
        raise exceptions.PermissionDenied(
            "Your mailbox access changed. Retry this action from your personal mailbox."
        )

    identity = getattr(request, "identity_mailbox", None)
    mailbox = getattr(request, "mailbox", None)
    if identity is None or mailbox is None or identity.pk == mailbox.pk:
        return

    permissions = getattr(request, "mailbox_permissions", MailboxPermissions())
    allowed = {
        "read": permissions.can_read,
        "manage": permissions.can_manage,
        "send": permissions.can_send_as or permissions.can_send_on_behalf,
        "read_or_send": (
            permissions.can_read
            or permissions.can_send_as
            or permissions.can_send_on_behalf
        ),
        "read_and_send": (
            permissions.can_read
            and (permissions.can_send_as or permissions.can_send_on_behalf)
        ),
        "manage_and_send": (
            permissions.can_manage
            and (permissions.can_send_as or permissions.can_send_on_behalf)
        ),
    }.get(permission, False)

    if not allowed:
        raise exceptions.PermissionDenied(
            "Your mailbox permission does not allow that action."
        )


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

        identity = session.mailbox
        try:
            # Re-checked per request, not merely at sign-in: a suspension has
            # to take effect while somebody is reading, not at their next login.
            assert_mailbox_may_sign_in(identity)
        except MailboxUnavailable as exc:
            session.revoke()
            raise exceptions.AuthenticationFailed(exc.message) from exc

        active_mailbox_invalid = False
        try:
            mailbox, permissions = active_mailbox_for_session(session)
        except MailboxUnavailable:
            # The stale shared-mailbox selection has already been cleared. Keep the
            # personal authentication capability alive for recovery endpoints
            # such as Me, mailbox switch and logout, but mark this request so
            # mailbox-content endpoints refuse it instead of accidentally
            # replaying an action against the personal mailbox.
            active_mailbox_invalid = True
            mailbox, permissions = identity, MailboxPermissions.owner()

        session.touch()
        request.postbox_session = session
        request.identity_mailbox = identity
        request.mailbox = mailbox
        request.mailbox_permissions = permissions
        request.postbox_active_mailbox_invalid = active_mailbox_invalid

        # DRF's principal is always the mailbox that authenticated the session,
        # never the TeamBox currently being viewed.
        return (PostBoxIdentity(identity, session), None)


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
    every other hostname.
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


def saved_account_cookie_name(session_id) -> str:
    """Cookie name for one retained mailbox session on this browser."""
    return SAVED_ACCOUNT_COOKIE_PREFIX + str(session_id)


def set_saved_account_cookie(response, raw_token: str, session: PostBoxSession):
    """
    Retain one independently authenticated mailbox session for account switching.

    This capability is deliberately stricter than the active navigation cookie:
    SameSite=Strict means it is sent only after the browser is already on the
    PostBox site. It remains HttpOnly, Secure and host-only, and expires at the
    same fixed deadline as the underlying session.
    """
    from django.conf import settings

    response.set_cookie(
        saved_account_cookie_name(session.id),
        raw_token,
        max_age=max(0, int((session.expires_at - timezone.now()).total_seconds())),
        secure=not settings.DEBUG,
        httponly=True,
        samesite="Strict",
        path="/",
    )
    return response


def clear_saved_account_cookie(response, session_id):
    response.delete_cookie(saved_account_cookie_name(session_id), path="/")
    return response


def saved_account_sessions(request):
    """
    Return valid retained sessions plus stale cookie names.

    The session's mailbox eligibility is re-checked here just as it is on
    normal authenticated requests.
    """
    valid = []
    stale = []
    for name, raw_token in request.COOKIES.items():
        if not name.startswith(SAVED_ACCOUNT_COOKIE_PREFIX):
            continue

        session = session_for_token(raw_token)
        if session is None or saved_account_cookie_name(session.id) != name:
            stale.append(name)
            continue
        if not tenant_matches_request(request, session.mailbox.tenant):
            stale.append(name)
            continue
        try:
            assert_mailbox_may_sign_in(session.mailbox)
        except MailboxUnavailable:
            session.revoke()
            stale.append(name)
            continue
        valid.append((name, raw_token, session))
    return valid, stale


def remember_session_on_device(response, request, raw_token: str, session: PostBoxSession):
    """
    Keep this mailbox available in the account switcher without storing a password.

    The previously active mailbox is promoted before the active cookie is
    replaced, which makes Add another account safe even for sessions created
    before multi-account support existed. Duplicate sessions for the same
    mailbox are revoked, and a small hard cap prevents unbounded cookie growth.
    """
    retained, stale = saved_account_sessions(request)
    for name in stale:
        response.delete_cookie(name, path="/")

    active_raw = request.COOKIES.get(SESSION_COOKIE_NAME)
    active = session_for_token(active_raw) if active_raw else None
    if active is not None and tenant_matches_request(request, active.mailbox.tenant):
        try:
            assert_mailbox_may_sign_in(active.mailbox)
        except MailboxUnavailable:
            active.revoke()
        else:
            if all(existing.id != active.id for _, _, existing in retained):
                retained.append((saved_account_cookie_name(active.id), active_raw, active))

    others = []
    for name, old_raw, old_session in retained:
        if old_session.id == session.id:
            continue
        if old_session.mailbox_id == session.mailbox_id:
            old_session.revoke()
            response.delete_cookie(name, path="/")
            continue
        others.append((name, old_raw, old_session))

    others.sort(key=lambda row: row[2].last_seen_at, reverse=True)
    keep = others[: max(0, MAX_SAVED_ACCOUNTS - 1)]
    for name, _, old_session in others[len(keep):]:
        old_session.revoke()
        response.delete_cookie(name, path="/")

    for _, old_raw, old_session in keep:
        set_saved_account_cookie(response, old_raw, old_session)
    set_saved_account_cookie(response, raw_token, session)
    return response


def revoke_other_sessions(mailbox: Mailbox, keep: PostBoxSession | None = None) -> int:
    """End every session for this mailbox except, optionally, the current one."""
    query = PostBoxSession.objects.filter(mailbox=mailbox, revoked_at__isnull=True)
    if keep is not None:
        query = query.exclude(pk=keep.pk)
    with transaction.atomic():
        # Their push registrations go with them, as in PostBoxSession.revoke.
        PostBoxPushDevice.objects.filter(session__in=query).delete()
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
