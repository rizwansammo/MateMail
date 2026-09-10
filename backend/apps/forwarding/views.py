import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.mailboxes.models import Mailbox
from apps.tenants.permissions import IsTenantAdmin, TenantReadAdminWrite
from .models import ForwardingRule, ForwardingStatus
from .serializers import ForwardingRuleCreateSerializer, ForwardingRuleSerializer

logger = logging.getLogger(__name__)


class ForwardingRuleListCreateView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def get(self, request):
        rules = (
            ForwardingRule.objects
            .for_tenant(request.tenant)
            .select_related("source_mailbox")
        )
        return Response(ForwardingRuleSerializer(rules, many=True).data)

    def post(self, request):
        serializer = ForwardingRuleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        mailbox = (
            Mailbox.objects.for_tenant(request.tenant)
            .filter(pk=data["source_mailbox_id"])
            .first()
        )
        if not mailbox:
            return Response({"source_mailbox_id": "Mailbox not found."}, status=400)

        if ForwardingRule.objects.for_tenant(request.tenant).filter(
            source_mailbox=mailbox,
            destination_email=data["destination_email"],
        ).exists():
            return Response(
                {"destination_email": "A forwarding rule to that address already exists."},
                status=400,
            )

        rule = ForwardingRule.objects.create(
            tenant=request.tenant,
            source_mailbox=mailbox,
            destination_email=data["destination_email"],
            keep_copy=data["keep_copy"],
            status=ForwardingStatus.ACTIVE,
        )

        try:
            from apps.mail_engine.factory import get_adapter
            result = get_adapter().provision_forwarding(rule)
            rule.mail_engine_provisioned = result.success
            rule.save(update_fields=["mail_engine_provisioned"])
            if not result.success:
                logger.warning("Forwarding provision failed for rule %s: %s", rule.id, result.message)
        except Exception as exc:
            logger.error("Forwarding provision error for rule %s: %s", rule.id, exc)

        return Response(ForwardingRuleSerializer(rule).data, status=201)


class ForwardingRuleDetailView(APIView):
    permission_classes = [IsAuthenticated, TenantReadAdminWrite]

    def _get_rule(self, request, pk):
        return (
            ForwardingRule.objects
            .for_tenant(request.tenant)
            .select_related("source_mailbox")
            .filter(pk=pk)
            .first()
        )

    def get(self, request, pk):
        rule = self._get_rule(request, pk)
        if not rule:
            return Response({"detail": "Not found."}, status=404)
        return Response(ForwardingRuleSerializer(rule).data)

    def delete(self, request, pk):
        rule = self._get_rule(request, pk)
        if not rule:
            return Response({"detail": "Not found."}, status=404)

        # Deprovision BEFORE delete so adapter can query remaining rules
        if rule.mail_engine_provisioned:
            try:
                from apps.mail_engine.factory import get_adapter
                get_adapter().delete_forwarding(rule)
            except Exception as exc:
                logger.warning("Forwarding deprovision error for rule %s: %s", rule.id, exc)

        rule.delete()
        return Response(status=204)


class ForwardingRuleStatusView(APIView):
    """PATCH /api/forwarding/{id}/status/ — active, paused, or disabled."""
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def patch(self, request, pk):
        rule = (
            ForwardingRule.objects
            .for_tenant(request.tenant)
            .select_related("source_mailbox")
            .filter(pk=pk)
            .first()
        )
        if not rule:
            return Response({"detail": "Not found."}, status=404)

        new_status = request.data.get("status")
        if new_status not in ("active", "paused", "disabled"):
            return Response({"status": "Must be 'active', 'paused', or 'disabled'."}, status=400)

        if rule.status == new_status:
            return Response(ForwardingRuleSerializer(rule).data)

        rule.status = new_status
        rule.save(update_fields=["status", "updated_at"])

        # Re-sync all active rules for this mailbox after status change
        if rule.mail_engine_provisioned:
            try:
                from apps.mail_engine.factory import get_adapter
                get_adapter().provision_forwarding(rule)
            except Exception as exc:
                logger.warning("Forwarding status re-sync failed for rule %s: %s", rule.id, exc)

        return Response(ForwardingRuleSerializer(rule).data)
