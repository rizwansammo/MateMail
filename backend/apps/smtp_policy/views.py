"""
Internal SMTP policy endpoints — consulted by the MateMail Mail Engine.

These endpoints are where MateMail enforces its own policy on mail flow:
suspension, sender identity and rate limits are MateMail's decisions, not
engine configuration. The engine asks; MateMail answers.

All endpoints are secured by INTERNAL_API_SECRET (X-Internal-Secret header),
are mounted under /api/internal/, and are denied at the edge by nginx. They are
never reachable by tenants.

Engine-side wiring (component names retained because engineers configuring the
Mail Engine need them):

    SMTP component (Postfix)
        A policy-daemon bridge speaks the check_policy_service protocol over TCP
        and translates it to HTTP calls against these endpoints.
        NOTE: the restriction ordering required for these checks to actually run
        is not yet correct — see PROJECT_STATUS.md blocker 2, scheduled for P5.

    IMAP component (Dovecot)
        Consulted on SASL success to apply the same outbound policy.
"""
import hmac
import logging

from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.utils import get_plan
from apps.domains.models import Domain, DomainStatus
from apps.tenants.policy import (
    MailNotPermitted,
    assert_can_send_mail,
    is_temporary_denial,
    mail_denial_reason,
)
from apps.aliases.models import Alias, AliasStatus
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


def _is_platform_sender(address: str) -> bool:
    """
    Is this MateMail's own service identity rather than a customer?

    Compared against the configured list, normalised the same way the request
    values are. A display name (`MateMail <noreply@...>`) is stripped, because
    DEFAULT_FROM_EMAIL carries one and the setting may be copied from it.
    """
    configured = getattr(settings, "PLATFORM_SENDER_ADDRESSES", []) or []
    known = set()
    for entry in configured:
        entry = (entry or "").strip().lower()
        if "<" in entry and entry.endswith(">"):
            entry = entry[entry.rindex("<") + 1:-1].strip()
        if entry:
            known.add(entry)
    return address in known


class InboundPolicyView(APIView):
    """
    POST /api/internal/smtp/inbound/

    The Mail Engine's SMTP component calls this before accepting an inbound
    message, to verify the recipient domain is hosted and active in MateMail.

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

        # ── Permanent vs temporary ──────────────────────────────────────────
        #
        # Everything below used to be a flat REJECT, which a sending server
        # reads as 5xx: give up now and return the message to whoever wrote it.
        # For a condition that resolves — a suspension lifted, a payment made —
        # that destroys mail rather than delaying it, and tells the sender the
        # address does not work.
        #
        # So a condition that will change DEFERs (4xx): the remote server holds
        # the message and retries for days, and it is delivered when the
        # workspace comes back. Only a settled answer rejects.
        if domain.status != DomainStatus.ACTIVE:
            # A domain is deactivated for reasons that are nearly always
            # temporary — suspension, re-provisioning — and it is still a
            # domain MateMail hosts. Defer rather than bounce.
            return Response({
                "action": "DEFER",
                "reason": "Domain is not currently accepting mail",
            })

        # Inbound uses can_use_mail, NOT can_send_mail. A workspace whose
        # outbound was disabled as an abuse response should still RECEIVE mail:
        # cutting inbound punishes the people writing to them, and loses mail
        # that was never the problem.
        denial = mail_denial_reason(domain.tenant)
        if denial:
            return Response({
                "action": "DEFER" if is_temporary_denial(denial) else "REJECT",
                "reason": "Account not active",
            })

        mailbox = Mailbox.objects.filter(email=recipient).first()
        if not mailbox:
            # ── Not a mailbox: it may still be a valid alias ─────────────────
            #
            # An alias has no Mailbox row, so a mailbox-only lookup rejected
            # every alias address as nonexistent — permanently bouncing all mail
            # to every alias the product lets customers create.
            #
            # An alias is checked here rather than merged into the query above
            # because the two are genuinely different objects with different
            # states: an alias can be disabled while its destination mailbox is
            # perfectly fine.
            alias = (
                Alias.objects.filter(source_address=recipient)
                .select_related("tenant")
                .first()
            )
            if alias is None:
                # Genuinely permanent, and the one case that should be: the
                # address does not exist. Deferring would make MateMail a
                # backscatter source and hide typos from senders for days.
                return Response({"action": "REJECT", "reason": "Mailbox does not exist"})

            if alias.status != AliasStatus.ACTIVE:
                # The customer's own switch, and one they flip back. A pause,
                # not a statement that the address never existed.
                return Response({
                    "action": "DEFER",
                    "reason": "Address is not accepting mail",
                })

            # The alias belongs to the domain this recipient is on, and the
            # domain's workspace has already passed the policy check above. The
            # engine expands the destination itself, so there is nothing further
            # for MateMail to decide.
            return Response({"action": "OK", "reason": ""})

        if mailbox.status == MailboxStatus.SUSPENDED:
            # A platform abuse action, and reversible. The account holder's
            # incoming mail should survive it.
            return Response({"action": "DEFER", "reason": "Mailbox is not accepting mail"})

        if mailbox.status == MailboxStatus.DISABLED:
            # The tenant's own choice, and the one they undo themselves. Still
            # deferred rather than bounced: "disabled" is a pause, not a
            # statement that the address never existed.
            return Response({"action": "DEFER", "reason": "Mailbox is not accepting mail"})

        return Response({"action": "OK", "reason": ""})


class OutboundPolicyView(APIView):
    """
    POST /api/internal/smtp/outbound/

    Called by the Mail Engine's submission service after SASL authentication,
    through the Postfix policy bridge. Enforces, in order:

    1. the platform sender's own allowance and limit (MateMail's service identity)
    2. the authenticated SASL username resolves to a mailbox MateMail hosts
    3. that mailbox is neither suspended nor disabled
    4. its workspace is approved, active, and not outbound-disabled
    5. the envelope sender is the authenticated mailbox, or an alias it is
       explicitly authorized to send as
    6. the sending domain is active
    7. plan rate limits

    Request body::

        {"sender": "...", "sasl_username": "...", "stage": "rcpt"|"end_of_data"}

    Response: ``{"action": "OK"|"REJECT"|"DEFER", "reason": "..."}``

    ## The two stages, and where the counter moves

    Postfix consults this endpoint twice for one submission, and the difference
    matters because one of the two must not count.

    ``stage="rcpt"``
        Runs once per RCPT TO. Authorization only — no counter is touched. A
        message to five recipients reaches this five times, so counting here
        would charge a sender five messages for sending one. Its value is
        rejecting a bad submission before the body is transferred.

    ``stage="end_of_data"`` (or omitted)
        Runs once per message, after DATA. This is where the rate limit is
        checked and recorded, so one submission costs exactly one message.

    A missing ``stage`` performs the full check including the counter, so a
    caller that does not know about stages cannot accidentally skip the limit.

    REJECT is permanent, DEFER is "try again later". Outbound refusals are
    permanent by design — see the policy check below — with one exception: a
    rate limit DEFERs, because the sender is within policy and merely early.
    """
    permission_classes = [AllowAny]

    #: The stage that authorizes but does not count.
    STAGE_RCPT = "rcpt"

    def post(self, request):
        if not _authorized(request):
            return Response({"action": "REJECT", "reason": "Unauthorized"}, status=403)

        sender = request.data.get("sender", "").lower().strip()
        sasl_username = request.data.get("sasl_username", "").lower().strip()
        stage = str(request.data.get("stage", "")).lower().strip()
        counts = stage != self.STAGE_RCPT

        if not sender or not sasl_username:
            return Response({"action": "REJECT", "reason": "sender and sasl_username are required"})

        # ── The platform sender ─────────────────────────────────────────────
        #
        # MateMail's own service identity sends verification emails, password
        # resets and invitations through this same submission path (DEC-013).
        # It has no Mailbox, no Domain and no tenant, so every check below it
        # would reject it — and account recovery for every customer would stop
        # the moment this service is enforced in the engine's restrictions.
        #
        # The allowance is keyed on the AUTHENTICATED identity, not on the
        # envelope sender, and then requires the sender to equal it exactly.
        # Keying it on the sender alone would mean anyone who can set a MAIL
        # FROM could claim the exemption; keying it on the authenticated
        # account means the exemption is only ever available to whoever holds
        # the platform credential, and only for its own address.
        #
        # It is narrow in the other direction too: one exact address, never a
        # domain or a subdomain. `anything@mail.matemail.online` would hand the
        # exemption to every future mailbox on the platform's own sending
        # domain.
        #
        # Tenant policy is skipped because there is no tenant to have a policy,
        # which also means an abuse response against one customer cannot
        # silence MateMail's own password resets. Rate limiting is NOT skipped.
        if _is_platform_sender(sasl_username):
            if sender != sasl_username:
                logger.warning(
                    "Outbound policy: platform credential used for sender %s", sender
                )
                return Response({
                    "action": "REJECT",
                    "reason": "Sender address must match the authenticated account",
                })
            if counts:
                allowed, reason = _limiter.check_and_record_platform(sender=sender)
                if not allowed:
                    return Response({"action": "DEFER", "reason": reason})
            return Response({"action": "OK", "reason": ""})

        # ── The authenticated identity ──────────────────────────────────────
        #
        # Everything below is decided about the mailbox that AUTHENTICATED, not
        # the address in MAIL FROM. Looking the policy up by envelope sender —
        # which this did first — means an attacker chooses which record their
        # own request is judged against.
        auth_mailbox = (
            Mailbox.objects
            .filter(email=sasl_username)
            .select_related("domain", "tenant")
            .first()
        )
        if not auth_mailbox:
            return Response({"action": "REJECT", "reason": "Sender mailbox not found"})

        if auth_mailbox.status == MailboxStatus.SUSPENDED:
            return Response({"action": "REJECT", "reason": "Mailbox suspended"})

        if auth_mailbox.status == MailboxStatus.DISABLED:
            return Response({"action": "REJECT", "reason": "Mailbox disabled"})

        # The authoritative policy: approval, status, and the outbound kill
        # switch, in one place shared with every provisioning path.
        #
        # Deliberately REJECT even for reasons the inbound path treats as
        # temporary. The asymmetry is the point: inbound mail belongs to a third
        # party who should not lose it over our customer's billing problem, so it
        # is held and retried. Outbound mail belongs to the customer, who is
        # sitting in front of a mail client and is better served by an immediate,
        # clear refusal than by a message that silently queues for days.
        #
        # It also matters for abuse. Deferring a suspended workspace's outbound
        # would accumulate a spool of exactly the mail we suspended them for,
        # and release it the moment the suspension lifted.
        try:
            assert_can_send_mail(auth_mailbox.tenant)
        except MailNotPermitted as exc:
            logger.info(
                "Outbound refused for %s: %s", auth_mailbox.email, exc.reason_code
            )
            return Response({"action": "REJECT", "reason": exc.customer_message})

        # ── Envelope sender authorization ───────────────────────────────────
        #
        # The authenticated account may send as itself, and as any alias it is
        # explicitly authorized for. Nothing else.
        sending_domain = auth_mailbox.domain

        if sender != sasl_username:
            # An alias authorizes sending only when it resolves to THIS mailbox.
            # `destination_mailbox` is a foreign key, so the match is on the row
            # itself rather than on a string that happens to look similar — and
            # because an alias belongs to a domain, and a verified domain
            # belongs to exactly one tenant, there is no query shape here that
            # can reach across tenants.
            #
            # This mirrors what the engine itself enforces at MAIL FROM through
            # `reject_authenticated_sender_login_mismatch` and its sender ACL,
            # where an alias whose `goto` is the logged-in mailbox is a
            # permitted sender. MateMail being stricter than the engine would
            # reject alias mail the engine had already accepted; being looser
            # would be a policy the engine then refuses. They must agree.
            alias = (
                Alias.objects
                .filter(
                    source_address=sender,
                    destination_mailbox=auth_mailbox,
                    status=AliasStatus.ACTIVE,
                )
                .select_related("domain")
                .first()
            )
            if alias is None:
                logger.warning(
                    "Outbound policy: %s is not authorized to send as %s",
                    sasl_username, sender,
                )
                return Response({
                    "action": "REJECT",
                    "reason": "Sender address must match the authenticated account",
                })
            sending_domain = alias.domain

        if sending_domain.status != DomainStatus.ACTIVE:
            return Response({"action": "REJECT", "reason": "Sending domain is not active"})

        # ── Rate limiting ───────────────────────────────────────────────────
        #
        # DEFER, not REJECT, so a sender over its limit retries later instead of
        # losing the message. Limits come from the workspace's plan; a limiter
        # outage also defers (fail closed, do not lose mail).
        #
        # Keyed on the AUTHENTICATED mailbox, never on the envelope sender.
        # Keying on the sender would let one account spread its hourly quota
        # across every alias it holds, which is precisely the cheap-identity
        # problem the alias cap exists to prevent.
        if counts:
            allowed, reason = _limiter.check_and_record(
                mailbox_email=auth_mailbox.email,
                tenant_id=str(auth_mailbox.tenant_id),
                plan=get_plan(auth_mailbox.tenant),
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

        mailbox = (
            Mailbox.objects.filter(email=email)
            .select_related("domain", "tenant")
            .first()
        )
        if not mailbox:
            return Response({"detail": "Mailbox not found"}, status=404)

        # Limits come from the plan; this endpoint no longer keeps its own copy
        # of the numbers, which could and did drift from the limiter's.
        return Response(_limiter.status_for(
            mailbox_email=email,
            tenant_id=str(mailbox.tenant_id),
            plan=get_plan(mailbox.tenant),
        ))
