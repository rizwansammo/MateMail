"""Phase 6F: read-only grant inventory for a recently verified superuser.

The Phase 6D self-grants listing intentionally excludes other administrators.
Grant revocation requires a UUID, so the operator needs an audited way to list
active grants without enumerating platform users, tenants or mailboxes.
"""
from django.conf import settings
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.permissions import IsPlatformAdmin
from .audit import record_platform_action
from .mail_center_purpose import recent_platform_otp
from .models import PlatformSystemMailboxGrant


@method_decorator(never_cache, name="dispatch")
class PlatformMailCenterGrantInventoryView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        if not getattr(settings, "PLATFORM_MAIL_CENTER_ENABLED", False):
            return Response({"detail": "Platform Mail Center is not enabled."}, status=503)
        if not getattr(settings, "PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED", False):
            return Response({"detail": "Purpose mailbox access is not enabled."}, status=503)
        if not request.user.is_superuser or not recent_platform_otp(request):
            return Response({"detail": "A verified platform superuser is required."}, status=403)
        if request.query_params:
            return Response({"detail": "Unsupported grant filter."}, status=400)
        grants = (
            PlatformSystemMailboxGrant.objects.filter(
                revoked_at__isnull=True, expires_at__gt=timezone.now(),
            ).select_related("grantee").order_by("expires_at", "pk")[:100]
        )
        rows = [{
            "grant_id": str(grant.pk),
            "role": grant.role,
            "grantee_id": str(grant.grantee_id),
            "grantee_email": grant.grantee.email,
            "expires_at": grant.expires_at,
        } for grant in grants]
        record_platform_action(
            actor=request.user, request=request, action="mail_center.grants_inventory_read",
            target_label="platform_mail_permissions", metadata={"count": len(rows)},
        )
        return Response({"results": rows, "truncated": len(rows) == 100})
