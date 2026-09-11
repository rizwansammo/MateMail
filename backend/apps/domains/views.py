import logging

from django.conf import settings
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.utils import check_domain_limit
from apps.security import ratelimit
from .keystore import encrypt as encrypt_dkim_key
from apps.security.limits import DOMAIN_CHECK_PER_DOMAIN
from apps.logs.models import LogEventType
from apps.logs.utils import log_event
from apps.tenants.permissions import (
    IsEmailVerified,
    TenantReadAdminWrite,
    TenantReadSupportWrite,
)
from .dkim import generate_dkim_keypair
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
        allowed, msg = check_domain_limit(request.tenant)
        if not allowed:
            return Response({"detail": msg}, status=402)

        serializer = DomainCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        selector = getattr(settings, "DKIM_SELECTOR", "mm1")
        private_pem, public_key = generate_dkim_keypair()

        domain = Domain.objects.create(
            tenant=request.tenant,
            domain=serializer.validated_data["domain"],
            dkim_selector=selector,
            dkim_public_key=public_key,
            dkim_private_key=encrypt_dkim_key(private_pem.decode()),
            # Issued up front so the domain page can show the exact TXT record
            # the moment the domain is added.
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

    def delete(self, request, pk):
        domain = self._get_domain(request, pk)
        if not domain:
            return Response({"detail": "Not found."}, status=404)

        # Fire-and-forget deprovision in mail engine
        if domain.mail_engine_provisioned:
            try:
                from apps.mail_engine.tasks import deprovision_domain_task
                deprovision_domain_task.delay(domain.domain)
            except Exception:
                pass

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
