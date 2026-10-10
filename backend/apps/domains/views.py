import logging

from django.conf import settings
from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.utils import check_domain_limit, reserve_resource_slot
from apps.tenants.policy import MailNotPermitted, assert_can_use_mail
from apps.security import ratelimit
from apps.security.limits import DOMAIN_CHECK_PER_DOMAIN
from apps.logs.models import LogEventType
from apps.logs.utils import log_event
from apps.tenants.permissions import (
    IsEmailVerified,
    TenantReadAdminWrite,
    TenantReadSupportWrite,
)
from .verification import (
    DomainNotVerified,
    assert_provisionable,
    ensure_verification_token,
    generate_verification_token,
    rotate_verification_token,
    verify_domain_ownership,
)
from .models import Domain
from .serializers import DomainCreateSerializer, DomainSerializer

logger = logging.getLogger(__name__)


class DomainListCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def get(self, request):
        domains = Domain.objects.for_tenant(request.tenant).order_by("-added_at")
        return Response(DomainSerializer(domains, many=True).data)

    def post(self, request):
        # GATE: an unapproved, rejected, suspended or cancelled workspace may
        # not touch the Mail Engine. One authoritative check (apps.tenants.
        # policy) rather than a status comparison repeated per endpoint.
        try:
            assert_can_use_mail(request.tenant)
        except MailNotPermitted as exc:
            logger.info(
                "Domain creation refused for tenant %s: %s",
                request.tenant.id, exc.reason_code,
            )
            return Response({"detail": exc.customer_message}, status=403)

        serializer = DomainCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        selector = getattr(settings, "DKIM_SELECTOR", "mm1")

        # DEC-007r stage 1: MateMail no longer generates DKIM keys.
        #
        # Django used to mint an RSA keypair here and keep the private half.
        # That key signed nothing — no engine ever held it — so the DKIM record
        # shown to the customer was decorative, while the private key was a real
        # liability sitting in our database. The engine now generates the pair
        # at provisioning time and keeps the private half; MateMail records only
        # the public material.
        #
        # Both DKIM fields therefore start empty. Provisioning is gated on
        # ownership verification (P3a), so nothing is displayed as publishable
        # before the domain is proven anyway.
        # The cap is checked and the row created under the tenant lock. Checking
        # first and creating after is a check-then-act race: two requests both
        # read a count one below the cap and both succeed.
        with reserve_resource_slot(request.tenant, check_domain_limit) as slot:
            if not slot.allowed:
                return Response({"detail": slot.message}, status=402)

            domain = Domain.objects.create(
                tenant=request.tenant,
                domain=serializer.validated_data["domain"],
                dkim_selector=selector,
                # Issued up front so the domain page can show the exact TXT
                # record the moment the domain is added.
                verification_token=generate_verification_token(),
            )

        # GATE: a newly added domain is unverified, so NO provisioning is
        # queued here. Provisioning happens only after ownership is proved.
        # This is the first of four gates; the others are the manual provision
        # endpoint, the Celery task, and mailbox creation.
        #
        # A DNS *health* check is still queued: it reads public records and
        # reveals nothing, so it is safe before ownership is proved and gives
        # the customer useful feedback while they add the TXT record.
        try:
            from apps.dnshealth.tasks import check_domain_dns
            check_domain_dns.delay(str(domain.id))
        except Exception:
            logger.warning("Could not queue DNS check for %s", domain.domain)

        log_event(request.tenant, LogEventType.DOMAIN_ADDED, request=request, domain=domain)
        return Response(DomainSerializer(domain).data, status=201)


class DomainDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def _get_domain(self, request, pk):
        return Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()

    def get(self, request, pk):
        domain = self._get_domain(request, pk)
        if not domain:
            return Response({"detail": "Not found."}, status=404)
        return Response(DomainSerializer(domain).data)

    @transaction.atomic
    def delete(self, request, pk):
        # Serialize deletes against domain-level security opt-in and verification.
        domain = Domain.objects.for_tenant(request.tenant).select_for_update().filter(pk=pk).first()
        if not domain:
            return Response({"detail": "Not found."}, status=404)

        from apps.transport_security.models import (
            DomainTransportSecurity, TransportSecurityLifecycle,
        )
        # A domain row is the durable ownership link for its HTTPS policy.
        # Fail closed for pending, active, error, or partially provisioned sites.
        # A separate managed offboarding workflow must deal with MTA-STS caches.
        if DomainTransportSecurity.objects.filter(domain=domain).exclude(
            enabled=False, lifecycle=TransportSecurityLifecycle.DISABLED,
        ).exists():
            return Response({
                "detail": (
                    "Advanced transport security is configured for this domain. "
                    "Cancel an unprovisioned request or contact platform support "
                    "for safe policy offboarding before deleting this domain."
                )
            }, status=409)

        # ── Queue engine cleanup BEFORE deleting the local row, and fail closed
        #    if it cannot be queued. ────────────────────────────────────────────
        #
        # This task is what removes the domain's DKIM signing key. The engine
        # keeps that key after the domain itself is gone and issues it to
        # whoever registers the name next, so losing the task loses custody of a
        # private key that can sign mail as this domain.
        #
        # The local row is the only durable record that cleanup is owed. Delete
        # it before the task is safely queued and there is nothing left to
        # reconcile against: no domain in MateMail, a live signing key in the
        # engine, and no job anywhere that will ever remove it. Logging and
        # deleting anyway — which this did first — records the problem in a file
        # nobody reads while creating exactly that state.
        #
        # So a broker failure blocks the delete. A customer who cannot remove a
        # domain for a few minutes is a far smaller problem than an orphaned
        # signing key, and it is recoverable by retrying; the orphan is not.
        #
        # Enqueued unconditionally, not only when `mail_engine_provisioned` is
        # set. That flag is cleared at the START of a re-provision, so a domain
        # can hold engine state — including a key — while the flag reads False.
        # The task is idempotent and costs nothing for a domain the engine never
        # held: deleting an absent domain and an absent key both succeed,
        # verified against the live engine.
        try:
            from apps.mail_engine.tasks import deprovision_domain_task
            deprovision_domain_task.delay(domain.domain)
        except Exception as exc:
            logger.error(
                "SECURITY: refusing to delete domain %s (tenant %s) — engine "
                "deprovisioning could not be queued: %s. The local record is "
                "being kept deliberately: it is the only durable reference to a "
                "DKIM signing key that may still exist in the Mail Engine.",
                domain.domain, request.tenant.id, exc,
            )
            return Response(
                {
                    "detail": (
                        "This domain could not be removed right now. Nothing has "
                        "been changed — please try again in a few minutes."
                    )
                },
                status=503,
            )

        # From here the cleanup is durably queued. If the local delete now fails,
        # the engine loses the domain while MateMail keeps the row — recoverable
        # by reprovisioning, and strictly preferable to the reverse.
        log_event(request.tenant, LogEventType.DOMAIN_DELETED, request=request, domain=domain)
        domain.delete()
        return Response(status=204)


class DomainProvisionView(APIView):
    """POST /api/domains/{id}/provision/ — manually re-trigger mail engine provisioning."""
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def post(self, request, pk):
        domain = Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()
        if not domain:
            return Response({"detail": "Not found."}, status=404)

        # GATE: refuse before anything is queued. 409 rather than 403 — the
        # caller has permission, the resource is in the wrong state.
        try:
            assert_provisionable(domain)
        except DomainNotVerified as exc:
            logger.info(
                "Provision refused for unverified domain %s (tenant %s)",
                domain.domain, request.tenant.id,
            )
            return Response({"detail": exc.customer_message}, status=409)

        # Reset error so Celery task retries cleanly
        domain.mail_engine_provisioned = False
        domain.mail_engine_error = ""
        domain.save(update_fields=["mail_engine_provisioned", "mail_engine_error"])

        try:
            from apps.mail_engine.tasks import provision_domain_task
            provision_domain_task.delay(str(domain.id))
            return Response({"status": "queued"})
        except Exception as exc:
            logger.error("Failed to queue provision_domain_task: %s", exc)
            return Response({"detail": "Failed to queue provisioning task."}, status=503)


def _enforce_domain_check_limit(domain):
    """
    Throttle DNS-resolving checks per domain.

    Raising DRF's Throttled rather than returning a Response gets the
    Retry-After header set by the framework's exception handler, and keeps the
    body shape identical to every other throttled endpoint.
    """
    decision = ratelimit.hit(
        DOMAIN_CHECK_PER_DOMAIN.bucket,
        str(domain.id),
        limit=DOMAIN_CHECK_PER_DOMAIN.limit,
        window=DOMAIN_CHECK_PER_DOMAIN.window,
    )
    if not decision.allowed:
        raise Throttled(
            wait=decision.retry_after,
            detail=(
                "This domain has been checked too many times in the last hour. "
                "DNS changes can take a while to propagate — please wait before "
                "checking again."
            ),
        )


class DomainVerifyOwnershipView(APIView):
    """
    POST /api/domains/{id}/verify-ownership/

    Checks the _matemail-verify TXT record and, on success, claims the domain
    for this tenant. Safe to retry: the token is unchanged by a failed check,
    so a customer waiting on DNS propagation just tries again.

    Support may run a check (it is diagnostic and non-destructive); only
    owner/admin may rotate a token.
    """

    permission_classes = [IsAuthenticated, TenantReadSupportWrite]

    def post(self, request, pk):
        domain = Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()
        if not domain:
            return Response({"detail": "Not found."}, status=404)

        # Each check resolves DNS on the customer's behalf. Limiting per
        # domain rather than per user means a workspace cannot spread the same
        # hammering across its members.
        _enforce_domain_check_limit(domain)

        # A domain added before P3 has no token until now.
        ensure_verification_token(domain)

        verified, message = verify_domain_ownership(domain)

        # Ownership verification is the event that unlocks the mail service.
        # Queue provisioning immediately so DKIM material is generated without
        # requiring the customer to discover and press a separate retry button.
        # The task is idempotent and independently re-checks both tenant policy
        # and ownership at its boundary, so a duplicate queue is safe.
        if verified and not domain.mail_engine_provisioned:
            try:
                from apps.mail_engine.tasks import provision_domain_task

                provision_domain_task.delay(str(domain.id))
                if message == "Domain ownership verified.":
                    message = "Domain ownership verified. Mail service setup started."
            except Exception as exc:
                logger.error(
                    "Ownership verified for %s but provisioning could not be queued: %s",
                    domain.domain,
                    exc,
                )
                message = (
                    "Domain ownership verified, but mail service setup could not "
                    "be started automatically. Use Retry provisioning."
                )

        log_event(
            request.tenant,
            LogEventType.DOMAIN_OWNERSHIP_VERIFIED if verified
            else LogEventType.DOMAIN_OWNERSHIP_FAILED,
            result="success" if verified else "failed",
            request=request,
            domain=domain,
            metadata={"domain": domain.domain},
        )

        return Response(
            {
                "verified": verified,
                "detail": message,
                "domain": DomainSerializer(domain).data,
            },
            status=200 if verified else 409,
        )


class DomainRotateVerificationTokenView(APIView):
    """
    POST /api/domains/{id}/rotate-verification-token/

    Issues a new token and returns the domain to PENDING. Separate from the
    check on purpose: rotation invalidates the record the customer already
    published, so it must be a deliberate act, never a side effect of a retry.
    """

    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def post(self, request, pk):
        domain = Domain.objects.for_tenant(request.tenant).filter(pk=pk).first()
        if not domain:
            return Response({"detail": "Not found."}, status=404)

        was_verified = domain.is_ownership_verified
        rotate_verification_token(domain)

        log_event(
            request.tenant,
            LogEventType.DOMAIN_TOKEN_ROTATED,
            request=request,
            domain=domain,
            metadata={"domain": domain.domain, "was_verified": was_verified},
        )

        return Response(
            {
                "detail": (
                    "A new verification token has been issued. Update the TXT "
                    "record in your DNS, then run the check again."
                ),
                "domain": DomainSerializer(domain).data,
            }
        )
