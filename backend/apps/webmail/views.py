"""
Webmail SSO (Single Sign-On) bridge.

Flow:
  1. Authenticated tenant user calls GET /api/webmail/sso/?mailbox_id={uuid}
  2. Backend generates a one-time token (32 bytes of URL-safe entropy), stored in Redis
     with a 60-second TTL keyed as "webmail:sso:{token}".
  3. Returns {url: "https://webmail.../...?email=...&sso_token=...", expires_in: 60}
  4. Frontend opens the URL in a new tab.
  5. The webmail front end intercepts the sso_token query param and calls
     POST /api/internal/webmail/validate-token/ to exchange it for a confirmed email.

NOTE (DEC-005r): the long-term webmail is MateMail-built, not an engine-supplied
interface. This bridge also cannot yet complete a login on its own — webmail
needs an authenticated IMAP session and MateMail deliberately stores no mailbox
password. The mechanism (likely a Dovecot master user) is open as TBD-G and is
resolved in P8, not here.
  6. The token is deleted atomically on first use — cannot be replayed.

The internal validate endpoint is secured by INTERNAL_API_SECRET.
nginx must block public access to /api/internal/.
"""
import hmac
import logging
import secrets

import redis
from django.conf import settings
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.tenants.permissions import HasTenantAccess

logger = logging.getLogger(__name__)
_SSO_TTL = 60  # seconds


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def _authorized(request) -> bool:
    expected = getattr(settings, "INTERNAL_API_SECRET", "")
    if not expected:
        logger.error("INTERNAL_API_SECRET not configured — rejecting internal call")
        return False
    provided = request.META.get("HTTP_X_INTERNAL_SECRET", "")
    # compare_digest avoids leaking a byte-by-byte match through timing.
    return hmac.compare_digest(provided.encode(), expected.encode())


class WebmailSSOView(APIView):
    """
    GET /api/webmail/sso/?mailbox_id={uuid}

    Generates a one-time SSO token and returns the webmail auto-login URL.
    The token is valid for 60 seconds and consumed on first use.
    """
    permission_classes = [IsAuthenticated, HasTenantAccess]

    def get(self, request):
        mailbox_id = request.query_params.get("mailbox_id", "").strip()
        if not mailbox_id:
            return Response({"detail": "mailbox_id is required"}, status=400)

        mailbox = (
            Mailbox.objects
            .for_tenant(request.tenant)
            .filter(pk=mailbox_id)
            .select_related("domain")
            .first()
        )
        if not mailbox:
            return Response({"detail": "Mailbox not found"}, status=404)

        if mailbox.status != MailboxStatus.ACTIVE:
            return Response({"detail": "Mailbox is not active"}, status=400)

        if not mailbox.mail_engine_provisioned:
            return Response({"detail": "Mailbox is not yet provisioned in the mail engine"}, status=400)

        token = secrets.token_urlsafe(32)
        _redis().setex(f"webmail:sso:{token}", _SSO_TTL, mailbox.email)

        webmail_base = getattr(settings, "WEBMAIL_BASE_URL", "").rstrip("/")
        url = f"{webmail_base}/?email={mailbox.email}&sso_token={token}"

        return Response({
            "url": url,
            "email": mailbox.email,
            "expires_in": _SSO_TTL,
        })


class WebmailTokenValidateView(APIView):
    """
    POST /api/internal/webmail/validate-token/
    Body: {"token": "...", "email": "user@example.com"}

    Called by the webmail app to validate and consume an SSO token.
    One-time use — the Redis key is deleted atomically on success.
    Requires X-Internal-Secret header.

    Returns:
      200 {"valid": true,  "email": "user@example.com"}
      200 {"valid": false, "reason": "..."}
    """
    permission_classes = [AllowAny]

    def post(self, request):
        if not _authorized(request):
            return Response({"valid": False, "reason": "Unauthorized"}, status=403)

        token = request.data.get("token", "").strip()
        email = request.data.get("email", "").lower().strip()

        if not token or not email:
            return Response({"valid": False, "reason": "token and email are required"}, status=400)

        r = _redis()
        # getdel is atomic — get + delete in a single round-trip (Redis 6.2+)
        stored_email = r.getdel(f"webmail:sso:{token}")

        if stored_email is None:
            logger.info("WebmailSSO: token not found or expired (email=%s)", email)
            return Response({"valid": False, "reason": "Token expired or not found"})

        if stored_email != email:
            logger.warning(
                "WebmailSSO: email mismatch — provided=%s stored=%s", email, stored_email
            )
            return Response({"valid": False, "reason": "Token does not match email"})

        return Response({"valid": True, "email": email})
