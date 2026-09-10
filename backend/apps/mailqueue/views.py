import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from apps.tenants.permissions import HasTenantAccess, IsTenantAdmin
from apps.mail_engine.errors import MailEngineError
from apps.mail_engine.factory import get_adapter
from .models import QueueMessage, QueueStatus
from .serializers import QueueMessageSerializer

logger = logging.getLogger(__name__)


class QueueMessageListView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def get(self, request):
        tenant = request.tenant
        qs = QueueMessage.objects.for_tenant(tenant)

        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        else:
            # Default: active (non-delivered, non-cancelled) messages
            qs = qs.filter(status__in=[QueueStatus.PENDING, QueueStatus.DEFERRED, QueueStatus.FAILED])

        return Response(QueueMessageSerializer(qs[:200], many=True).data)


class QueueMessageCancelView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdmin]

    def post(self, request, pk):
        tenant = request.tenant
        try:
            msg = QueueMessage.objects.for_tenant(tenant).get(pk=pk)
        except QueueMessage.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if msg.status in (QueueStatus.DELIVERED, QueueStatus.CANCELLED):
            return Response({"detail": "Message is already delivered or cancelled."}, status=status.HTTP_400_BAD_REQUEST)

        # cancel_queue_message is idempotent by id, so a failure here is a real
        # problem rather than "already gone". Report it instead of marking the
        # message cancelled while it is still queued for delivery.
        if msg.engine_message_id:
            try:
                get_adapter().cancel_queue_message(msg.engine_message_id)
            except MailEngineError as exc:
                logger.error("Queue cancel failed for %s: %s", msg.pk, exc.log_message)
                return Response({"detail": exc.customer_message}, status=503)

        msg.status = QueueStatus.CANCELLED
        msg.save(update_fields=["status"])
        return Response(QueueMessageSerializer(msg).data)
