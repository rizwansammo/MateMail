import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.utils import check_alias_limit, reserve_resource_slot
from apps.domains.models import Domain\nfrom apps.domains.verification import DomainNotVerified, assert_provisionable
from apps.mail_engine.errors import MailEngineError
from apps.mailboxes.models import Mailbox
from apps.tenants.permissions import IsEmailVerified, IsTenantAdmin, TenantReadAdminWrite
from apps.tenants.policy import MailNotPermitted, assert_can_use_mail
from .models import Alias, AliasStatus
from .serializers import AliasCreateSerializer, AliasSerializer

logger = logging.getLogger(__name__)


def _alias_destinations(alias) -> tuple[str, ...]:
    """
    The alias's final destination set.

    Which address an alias delivers to is MateMail product logic, so it is
    resolved here and handed to the adapter as a finished instruction.
    """
    target = (
        alias.destination_mailbox.email
        if alias.destination_mailbox
        else alias.destination_address
    )
    return (target,) if target else ()


def _apply_alias(alias) -> None:
    """Push an alias's desired state to the Mail Engine and record the result."""
    from apps.mail_engine.dto import AliasSpec
    from apps.mail_engine.factory import get_adapter

    destinations = _alias_destinations(alias)
    if not destinations:
        raise MailEngineError("alias has no destination", operation="ensure_alias")

    get_adapter().ensure_alias(
        AliasSpec(
            address=alias.source_address,
            destinations=destinations,
            active=alias.status == AliasStatus.ACTIVE,
        )
    )
    alias.mail_engine_provisioned = True
    alias.save(update_fields=["mail_engine_provisioned"])


class AliasListCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite, IsEmailVerified]

    def get(self, request):
        aliases = (
            Alias.objects
            .for_tenant(request.tenant)
            .select_related("domain", "destination_mailbox")
        )
        return Response(AliasSerializer(aliases, many=True).data)

    def post(self, request):
        # GATE: an alias is a live receiving and sending address in the engine,
        # so it is subject to the same policy as a mailbox or a domain.
        try:
            assert_can_use_mail(request.tenant)
        except MailNotPermitted as exc:
            logger.info(
                "Alias creation refused for tenant %s: %s",
                request.tenant.id, exc.reason_code,
            )
            return Response({"detail": exc.customer_message}, status=403)

        serializer = AliasCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        domain = Domain.objects.for_tenant(request.tenant).filter(pk=data["domain_id"]).first()
        if not domain:
            return Response({"domain_id": "Domain not found in this workspace."}, status=400)

        try:
            assert_provisionable(domain)
        except DomainNotVerified as exc:
            return Response({"domain_id": exc.customer_message}, status=409)

        local_part = data["source_local_part"].strip().lower()
        source_address = f"{local_part}@{domain.domain}"

        if Mailbox.objects.for_tenant(request.tenant).filter(email=source_address).exists():
            return Response(
                {"source_local_part": "A mailbox already exists with this address."},
                status=400,
            )
        if Alias.objects.for_tenant(request.tenant).filter(source_address=source_address).exists():
            return Response(
                {"source_local_part": "An alias already exists with this address."},
                status=400,
            )

        destination_mailbox = None
        destination_address = ""

        if data.get("destination_mailbox_id"):
            destination_mailbox = (
                Mailbox.objects.for_tenant(request.tenant)
                .filter(pk=data["destination_mailbox_id"])
                .first()
            )
            if not destination_mailbox:
                return Response({"destination_mailbox_id": "Mailbox not found."}, status=400)
        else:
            destination_address = data["destination_address"]

        # Aliases were previously uncapped entirely. Checked and created under
        # the tenant lock, like every other plan-limited resource.
        with reserve_resource_slot(request.tenant, check_alias_limit) as slot:
            if not slot.allowed:
                return Response({"detail": slot.message}, status=402)

            alias = Alias.objects.create(
                tenant=request.tenant,
                domain=domain,
                source_address=source_address,
                destination_mailbox=destination_mailbox,
                destination_address=destination_address,
                status=AliasStatus.ACTIVE,
            )

        try:
            _apply_alias(alias)
        except MailEngineError as exc:
            logger.warning("Alias provisioning failed for %s: %s", source_address, exc.log_message)
        except Exception:
            logger.exception("Unexpected error provisioning alias %s", source_address)

        return Response(AliasSerializer(alias).data, status=201)


class AliasDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def _get(self, request, pk):
        return (
            Alias.objects
            .for_tenant(request.tenant)
            .select_related("domain", "destination_mailbox")
            .filter(pk=pk)
            .first()
        )

    def get(self, request, pk):
        alias = self._get(request, pk)
        if not alias:
            return Response({"detail": "Not found."}, status=404)
        return Response(AliasSerializer(alias).data)

    def delete(self, request, pk):
        alias = self._get(request, pk)
        if not alias:
            return Response({"detail": "Not found."}, status=404)

        if alias.mail_engine_provisioned:
            from apps.mail_engine.factory import get_adapter

            try:
                get_adapter().delete_alias(alias.source_address)
            except MailEngineError as exc:
                # delete_alias is idempotent, so this is a transport/refusal
                # problem rather than "already gone". Removing our record anyway
                # would orphan the alias in the engine, so refuse.
                logger.error("Alias removal failed for %s: %s", alias.source_address, exc.log_message)
                return Response({"detail": exc.customer_message}, status=503)

        alias.delete()
        return Response(status=204)


class AliasStatusView(APIView):
    """PATCH /api/aliases/{id}/status/ — enable or disable."""
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def patch(self, request, pk):
        alias = (
            Alias.objects
            .for_tenant(request.tenant)
            .select_related("domain", "destination_mailbox")
            .filter(pk=pk)
            .first()
        )
        if not alias:
            return Response({"detail": "Not found."}, status=404)

        new_status = request.data.get("status")
        if new_status not in ("active", "disabled"):
            return Response({"status": "Must be 'active' or 'disabled'."}, status=400)

        if alias.status == new_status:
            return Response(AliasSerializer(alias).data)

        if new_status == "active":
            try:
                assert_can_use_mail(request.tenant)
            except MailNotPermitted as exc:
                return Response({"detail": exc.customer_message}, status=403)

        # Set the desired status first so the instruction we push reflects it,
        # then persist only if the engine accepted the change.
        previous_status = alias.status
        alias.status = new_status

        if alias.mail_engine_provisioned:
            try:
                _apply_alias(alias)
            except MailEngineError as exc:
                alias.status = previous_status
                logger.error("Alias status sync failed for %s: %s", alias.source_address, exc.log_message)
                return Response({"detail": exc.customer_message}, status=503)

        alias.save(update_fields=["status", "updated_at"])
        return Response(AliasSerializer(alias).data)
