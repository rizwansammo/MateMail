import logging
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from apps.tenants.permissions import IsTenantAdmin
from apps.mail_engine.factory import get_adapter
from .models import QuarantineMessage, QuarantineStatus
from .serializers import QuarantineMessageSerializer

logger = logging.getLogger(__name__)


class QuarantineListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request):
        tenant = request.tenant
        qs = QuarantineMessage.objects.for_tenant(tenant)

        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        else:
            qs = qs.filter(status=QuarantineStatus.HELD)

        sender = request.query_params.get("sender")
        if sender:
            qs = qs.filter(sender__icontains=sender)

        return Response(QuarantineMessageSerializer(qs[:200], many=True).data)


class QuarantineReleaseView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def post(self, request, pk):
        tenant = request.tenant
        try:
            msg = QuarantineMessage.objects.for_tenant(tenant).get(pk=pk)
        except QuarantineMessage.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if msg.status != QuarantineStatus.HELD:
            return Response({"detail": "Only held messages can be released."}, status=status.HTTP_400_BAD_REQUEST)

        if msg.engine_message_id:
            try:
                adapter = get_adapter()
                adapter.release_quarantine_item(msg.engine_message_id)
            except Exception:
                logger.warning("Could not release quarantine item %s from mail engine.", msg.engine_message_id)

        msg.status = QuarantineStatus.RELEASED
        msg.actioned_at = timezone.now()
        msg.save(update_fields=["status", "actioned_at"])
        return Response(QuarantineMessageSerializer(msg).data)


class QuarantineDeleteView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def delete(self, request, pk):
        tenant = request.tenant
        try:
            msg = QuarantineMessage.objects.for_tenant(tenant).get(pk=pk)
        except QuarantineMessage.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        msg.status = QuarantineStatus.DELETED
        msg.actioned_at = timezone.now()
        msg.save(update_fields=["status", "actioned_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
