"""Release 4 Phase 6F: expose only operator-visible capability booleans.

This never opens IMAP or returns credentials, grants, report data, settings
values or sensitive mailbox messages. Permissions remain enforced per action.
"""
from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.permissions import IsPlatformAdmin


@method_decorator(never_cache, name="dispatch")
class PlatformMailCenterCapabilitiesView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        if request.query_params:
            return Response({"detail": "Unsupported capability filter."}, status=400)

        enabled = bool(getattr(settings, "PLATFORM_MAIL_CENTER_ENABLED", False))
        imap_ready = bool(getattr(settings, "POSTBOX_MASTER_PASSWORD", ""))
        read = enabled and imap_ready
        return Response({
            "operations": read,
            "sending": (
                read
                and bool(getattr(settings, "PLATFORM_MAIL_CENTER_SEND_ENABLED", False))
                and bool(getattr(settings, "EMAIL_HOST", ""))
            ),
            "sensitive_access": (
                read
                and bool(getattr(settings, "PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED", False))
            ),
            "reporting": (
                enabled
                and bool(getattr(settings, "PLATFORM_MAIL_CENTER_REPORTS_ENABLED", False))
            ),
        })
