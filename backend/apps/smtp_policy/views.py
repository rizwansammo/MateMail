"""
Internal SMTP policy endpoints — called by Postfix (via policy daemon bridge) and Dovecot.

All endpoints are secured by INTERNAL_API_SECRET (X-Internal-Secret header).
They are never exposed to tenants; nginx must block public access to /api/internal/.

Postfix integration (Phase 15):
    A small Python daemon script reads the Postfix policy protocol (text over TCP/Unix socket)
    and translates it to HTTP calls against these endpoints.

Dovecot integration (Phase 15):
    Configure passdb { driver = dict } pointing to the validate endpoint,
    or use a Lua policy script that calls /api/internal/smtp/outbound/ on SASL success.
"""
import hmac
import logging

from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.models import Domain, DomainStatus
from apps.mailboxes.models import Mailbox, MailboxStatus
from .rate_limits import MailRateLimiter

logger = logging.getLogger(__name__)
_limiter = MailRateLimiter()


def _authorized(request) -> bool:
    """Verify the shared internal secret is present and correct (constant time)."""
    expected = getattr(settings, "INTERNAL_API_SECRET", "")
    if not expected:
        logger.error("INTERNAL_API_SECRET not configured — rejecting internal call")
        return False
    provided = request.META.get("HTTP_X_INTERNAL_SECRET", "")
    # compare_digest avoids leaking a byte-by-byte match through timing.
    return hmac.compare_digest(provided.encode(), expected.encode())


class InboundPolicyView(APIView):
    """
    POST /api/internal/smtp/inbound/

    Postfix calls this before accepting an inbound message to verify the recipient
    domain is hosted and active in MateMail.

    Request body: {"recipient": "user@example.com"}
    Response:     {"action": "OK"|"REJECT", "reason": "..."}
    """
    permission_classes = [AllowAny]

    def post(self, request):
        if not _authorized(request):
            return Response({"action": "REJECT", "reason": "Unauthorized"}, status=403)

        recipient = request.data.get("recipient", "").lower().strip()
        if not recipient or "@" not in recipient:
            return Response({"action": "REJECT", "reason": "Invalid recipient address"}, status=400)

        domain_name = recipient.split("@", 1)[1]

        domain = Domain.objects.filter(domain=domain_name).select_related("tenant").first()
        if not domain:
            return Response({"action": "REJECT", "reason": f"Domain not hosted here: {domain_name}"})

        if domain.status != DomainStatus.ACTIVE:
            return Response({"action": "REJECT", "reason": f"Domain not active (status: {domain.status})"})

        if not domain.tenant.can_send_mail:
            return Response({"action": "REJECT", "reason": "Account suspended"})

        mailbox = Mailbox.objects.filter(email=recipient).first()
        if not mailbox:
            return Response({"action": "REJECT", "reason": "Mailbox does not exist"})

        if mailbox.status == MailboxStatus.SUSPENDED:
            return Response({"action": "REJECT", "reason": "Mailbox suspended"})

        if mailbox.status == MailboxStatus.DISABLED:
            return Response({"action": "REJECT", "reason": "Mailbox disabled"})

        return Response({"action": "OK", "reason": ""})


class OutboundPolicyView(APIView):
    """
    POST /api/internal/smtp/outbound/

    Called by Postfix submission (port 587) after SASL authentication.
    Enforces:
    - Sender address must match the authenticated SASL username (no impersonation)
    - Tenant must be active (not suspended or cancelled)
    - Mailbox must be active
    - Sending domain must be active
    - Rate limits not exceeded

    Request body: {"sender": "user@example.com", "sasl_username": "user@example.com"}
    Response:     {"action": "OK"|"REJECT"|"DEFER", "reason": "..."}
    """
    permission_classes = [AllowAny]

    def post(self, request):
        if not _authorized(request):
            return Response({"action": "REJECT", "reason": "Unauthorized"}, status=403)

        sender = request.data.get("sender", "").lower().strip()
        sasl_username = request.data.get("sasl_username", "").lower().strip()

        if not sender or not sasl_username:
            return Response({"action": "REJECT", "reason": "sender and sasl_username are required"})

        # Enforce sender = authenticated user — never allow impersonation or open relay
        if sender != sasl_username:
            logger.warning("Outbound policy: sender mismatch sender=%s sasl=%s", sender, sasl_username)
            return Response({
                "action": "REJECT",
                "reason": "Sender address must match the authenticated account",
            })

        mailbox = (
            Mailbox.objects
            .filter(email=sender)
            .select_related("domain", "tenant")
            .first()
        )
        if not mailbox:
            return Response({"action": "REJECT", "reason": "Sender mailbox not found"})

        if mailbox.status == MailboxStatus.SUSPENDED:
            return Response({"action": "REJECT", "reason": "Mailbox suspended"})

        if mailbox.status == MailboxStatus.DISABLED:
            return Response({"action": "REJECT", "reason": "Mailbox disabled"})

        if not mailbox.tenant.can_send_mail:
            return Response({"action": "REJECT", "reason": "Account suspended"})

        if mailbox.domain.status != DomainStatus.ACTIVE:
            return Response({"action": "REJECT", "reason": "Sending domain is not active"})

        # Rate limiting — DEFER (not REJECT) so the client can retry later
        allowed, reason = _limiter.check_and_record(
            mailbox_email=mailbox.email,
            domain_name=mailbox.domain.domain,
            tenant_id=str(mailbox.tenant_id),
        )
        if not allowed:
            return Response({"action": "DEFER", "reason": reason})

        return Response({"action": "OK", "reason": ""})


class RateLimitStatusView(APIView):
    """
    GET /api/internal/smtp/rate-limits/?email=user@example.com

    Returns current counter values for the three rate limit scopes.
    Used by the admin dashboard and internal monitoring.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        if not _authorized(request):
            return Response({"detail": "Unauthorized"}, status=403)

        email = request.query_params.get("email", "").lower().strip()
        if not email:
            return Response({"detail": "email query param required"}, status=400)

        mailbox = Mailbox.objects.filter(email=email).select_related("domain").first()
        if not mailbox:
            return Response({"detail": "Mailbox not found"}, status=404)

        counts = _limiter.get_counts(
            mailbox_email=email,
            domain_name=mailbox.domain.domain,
            tenant_id=str(mailbox.tenant_id),
        )
        limits = {"mailbox": 100, "domain": 500, "tenant": 2000}
        return Response({
            scope: {"current": counts[scope], "limit": limits[scope]}
            for scope in counts
        })
