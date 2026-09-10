import logging

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.mail_engine.errors import MailEngineError
from apps.mailboxes.models import Mailbox
from apps.tenants.permissions import IsTenantAdmin, TenantReadAdminWrite
from .models import ForwardingRule, ForwardingStatus
from .serializers import ForwardingRuleCreateSerializer, ForwardingRuleSerializer
from .services import apply_forwarding

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

        # The service layer resolves the mailbox's complete destination set
        # (active rules + keep_copy) and hands the adapter one final instruction.
        try:
            apply_forwarding(mailbox)
            rule.mail_engine_provisioned = True
            rule.save(update_fields=["mail_engine_provisioned"])
        except MailEngineError as exc:
            logger.error("Forwarding activation failed for rule %s: %s", rule.id, exc.log_message)
            # The rule exists but is not live in the engine. Say so, rather than
            # reporting a success the customer's mail flow will not reflect.
            return Response(
                {**ForwardingRuleSerializer(rule).data, "detail": exc.customer_message},
                status=202,
            )

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

        # Compute the state that will apply once this rule is gone and push it
        # first. excluding_rule_pk means we no longer depend on delete ordering,
        # which is what the old "deprovision before delete" comment worked around.
        mailbox = rule.source_mailbox
        try:
            apply_forwarding(mailbox, excluding_rule_pk=rule.pk)
        except MailEngineError as exc:
            logger.error("Forwarding removal failed for rule %s: %s", rule.id, exc.log_message)
            # Deleting our record now would leave mail still being forwarded.
            return Response({"detail": exc.customer_message}, status=503)

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

        previous_status = rule.status
        rule.status = new_status
        rule.save(update_fields=["status", "updated_at"])

        # Re-resolve the whole mailbox's forwarding: pausing one rule changes the
        # destination set, and the engine holds a single combined instruction.
        try:
            apply_forwarding(rule.source_mailbox)
        except MailEngineError as exc:
            rule.status = previous_status
            rule.save(update_fields=["status", "updated_at"])
            logger.error("Forwarding status sync failed for rule %s: %s", rule.id, exc.log_message)
            return Response({"detail": exc.customer_message}, status=503)

        return Response(ForwardingRuleSerializer(rule).data)
