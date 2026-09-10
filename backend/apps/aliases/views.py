import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox
from apps.tenants.permissions import IsTenantAdmin, TenantReadAdminWrite
from .models import Alias, AliasStatus
from .serializers import AliasCreateSerializer, AliasSerializer

logger = logging.getLogger(__name__)


class AliasListCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def get(self, request):
        aliases = (
            Alias.objects
            .for_tenant(request.tenant)
            .select_related("domain", "destination_mailbox")
        )
        return Response(AliasSerializer(aliases, many=True).data)

    def post(self, request):
        serializer = AliasCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        domain = Domain.objects.for_tenant(request.tenant).filter(pk=data["domain_id"]).first()
        if not domain:
            return Response({"domain_id": "Domain not found in this workspace."}, status=400)

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

        alias = Alias.objects.create(
            tenant=request.tenant,
            domain=domain,
            source_address=source_address,
            destination_mailbox=destination_mailbox,
            destination_address=destination_address,
            status=AliasStatus.ACTIVE,
        )

        try:
            from apps.mail_engine.factory import get_adapter
            result = get_adapter().provision_alias(alias)
            if result.success:
                alias.mail_engine_provisioned = True
            else:
                logger.warning("Alias provision failed for %s: %s", source_address, result.message)
            alias.save(update_fields=["mail_engine_provisioned"])
        except Exception as exc:
            logger.error("Alias provision error for %s: %s", source_address, exc)

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
            try:
                from apps.mail_engine.factory import get_adapter
                get_adapter().delete_alias(alias)
            except Exception as exc:
                logger.warning("Alias deprovision error for %s: %s", alias.source_address, exc)

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

        if alias.mail_engine_provisioned:
            try:
                from apps.mail_engine.factory import get_adapter
                get_adapter().update_alias_active(alias, new_status == "active")
            except Exception as exc:
                logger.warning("Alias status sync failed for %s: %s", alias.source_address, exc)

        alias.status = new_status
        alias.save(update_fields=["status", "updated_at"])
        return Response(AliasSerializer(alias).data)
