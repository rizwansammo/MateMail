from rest_framework.views import APIView
from rest_framework.response import Response
from apps.tenants.permissions import IsTenantAdmin
from .models import MailLog
from .serializers import MailLogSerializer


class MailLogListView(APIView):
    permission_classes = [IsTenantAdmin]

    def get(self, request):
        qs = MailLog.objects.for_tenant(request.tenant).order_by("-created_at")[:200]
        return Response(MailLogSerializer(qs, many=True).data)
