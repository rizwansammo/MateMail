"""Phase 6A: read-only Platform Mail Center for the fixed Operations identity.

Only platform administrators may enter. This deliberately does not use tenant
mailbox IDs or client-supplied mailbox addresses, and cannot send, delete,
mark messages read, or change IMAP folders.
"""
import logging

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.postbox import imap
from apps.tenants.permissions import IsPlatformAdmin

from .audit import record_platform_action

logger = logging.getLogger(__name__)

OPERATIONS_MAILBOX = "operations@system.matemail.pro"
MAX_FOLDERS = 50
MAX_PAGE_SIZE = 50
MAX_PAGE = 100


@method_decorator(never_cache, name="dispatch")
class PlatformMailCenterBase(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    http_method_names = ["get", "head", "options"]

    def gate(self):
        if not getattr(settings, "PLATFORM_MAIL_CENTER_ENABLED", False):
            return Response({"detail": "Platform Mail Center is not enabled."}, status=503)
        if not getattr(settings, "POSTBOX_MASTER_PASSWORD", ""):
            return Response({"detail": "Platform Mail Center is unavailable."}, status=503)
        return None

    @staticmethod
    def unavailable():
        return Response({"detail": "Platform Mail Center is temporarily unavailable."}, status=503)


class PlatformMailCenterFoldersView(PlatformMailCenterBase):
    """Only selectable folders from a hardcoded platform-owned mailbox."""

    def get(self, request):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        if request.query_params:
            return Response({"detail": "No folder filters are supported."}, status=400)
        try:
            with imap.open_mailbox(OPERATIONS_MAILBOX) as connection:
                available = [f for f in connection.list_folders() if f.selectable]
                rows = []
                for folder in available[:MAX_FOLDERS]:
                    total, unseen = connection.folder_counts(folder.name)
                    rows.append({
                        "name": folder.name,
                        "role": folder.role,
                        "messages": total,
                        "unseen": unseen,
                    })
        except Exception:
            logger.warning("Platform Mail Center folders could not be read.")
            return self.unavailable()
        record_platform_action(
            actor=request.user, request=request, action="mail_center.folders_read",
            target_label=OPERATIONS_MAILBOX,
            metadata={"folder_count": len(rows)},
        )
        return Response({
            "mailbox": OPERATIONS_MAILBOX, "results": rows,
            "has_more": len(available) > MAX_FOLDERS,
        })


class PlatformMailCenterMessagesView(PlatformMailCenterBase):
    """Bounded headers-only IMAP page. No message bodies or read flag changes."""

    def get(self, request):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        allowed_params = {"folder", "page", "page_size"}
        if set(request.query_params) - allowed_params:
            return Response({"detail": "Unsupported mail center filter."}, status=400)

        folder_name = request.query_params.get("folder", "INBOX")
        if not isinstance(folder_name, str) or not folder_name or len(folder_name) > 255:
            return Response({"detail": "Invalid folder."}, status=400)
        try:
            page = int(request.query_params.get("page", "1"))
            size = int(request.query_params.get("page_size", "25"))
        except (ValueError, TypeError):
            return Response({"detail": "Invalid pagination."}, status=400)
        if not (1 <= page <= MAX_PAGE and 1 <= size <= MAX_PAGE_SIZE):
            return Response({"detail": "Pagination is out of range."}, status=400)

        try:
            with imap.open_mailbox(OPERATIONS_MAILBOX) as connection:
                chosen = next(
                    (item for item in connection.list_folders()
                     if item.selectable and item.name == folder_name),
                    None,
                )
                if chosen is None:
                    return Response({"detail": "Folder not found."}, status=404)
                selected = connection.select(folder_name, readonly=True)
                if selected.uid_validity <= 0:
                    return self.unavailable()
                uids = connection.search_uids(["ALL"], newest=True)
                start = (page - 1) * size
                summaries = connection.fetch_summaries(uids[start:start + size])
                rows = [
                    {
                        "uid": msg.uid,
                        "subject": msg.subject,
                        "from_name": msg.from_name,
                        "from_address": msg.from_address,
                        "date": msg.date,
                        "seen": msg.seen,
                        "has_attachments": msg.has_attachments,
                        "size": msg.size,
                    }
                    for msg in summaries
                ]
        except Exception:
            logger.warning("Platform Mail Center messages could not be read.")
            return self.unavailable()
        record_platform_action(
            actor=request.user, request=request, action="mail_center.messages_read",
            target_label=OPERATIONS_MAILBOX,
            metadata={"folder_role": chosen.role or "custom", "page": page, "count": len(rows)},
        )
        return Response({
            "mailbox": OPERATIONS_MAILBOX,
            "folder": folder_name,
            "uid_validity": selected.uid_validity,
            "page": page, "page_size": size,
            "total": len(uids), "has_next": start + size < len(uids),
            "results": rows,
        })
