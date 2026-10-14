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

from apps.postbox import imap, mime
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


# Phase 6B bounds apply to both detail and attachment fetches.
# The full RFC822 message is in memory only while handling a request.
MAX_RAW_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
MAX_ATTACHMENTS = 24
MAX_BODY_CHARS = 250_000


def _validated_reference(request, uid):
    """Strict message reference. Never reuse an old UID after UIDVALIDITY rotates."""
    if set(request.query_params) - {"folder", "uid_validity"}:
        return None, Response({"detail": "Unsupported message selector."}, status=400)
    if any(len(request.query_params.getlist(key)) != 1
           for key in request.query_params):
        return None, Response({"detail": "Duplicate message selector."}, status=400)
    folder = request.query_params.get("folder", "INBOX")
    validity = request.query_params.get("uid_validity")
    if not isinstance(folder, str) or not folder or len(folder) > 255:
        return None, Response({"detail": "Invalid folder."}, status=400)
    try:
        validity = int(validity)
    except (TypeError, ValueError):
        return None, Response({"detail": "A valid UIDVALIDITY is required."}, status=400)
    if not (1 <= uid <= 4294967295 and 1 <= validity <= 4294967295):
        return None, Response({"detail": "Invalid message reference."}, status=400)
    return (folder, validity), None


def _read_fixed_operations_message(folder, validity, uid):
    """Never select an arbitrary account or folder. Read-only IMAP, bounded MIME."""
    with imap.open_mailbox(OPERATIONS_MAILBOX) as connection:
        chosen = next(
            (item for item in connection.list_folders()
             if item.selectable and item.name == folder), None,
        )
        if chosen is None:
            return None, None, None, Response({"detail": "Folder not found."}, status=404)
        selected = connection.select(folder, readonly=True)
        if selected.uid_validity <= 0:
            return None, None, None, Response({"detail": "Mailbox is unavailable."}, status=503)
        if selected.uid_validity != validity:
            return None, None, None, Response({
                "detail": "This folder has changed. Refresh your message list.",
            }, status=409)
        summaries = connection.fetch_summaries([uid])
        if not summaries or summaries[0].uid != uid:
            return None, None, None, Response({"detail": "Message not found."}, status=404)
        if summaries[0].size > MAX_RAW_MESSAGE_BYTES:
            return None, None, None, Response({"detail": "Message exceeds the safe reading limit."}, status=413)
        raw = connection.fetch_raw(uid)
        if not isinstance(raw, bytes) or len(raw) > MAX_RAW_MESSAGE_BYTES:
            return None, None, None, Response({"detail": "Message exceeds the safe reading limit."}, status=413)
    return raw, chosen, selected, None


class PlatformMailCenterMessageDetailView(PlatformMailCenterBase):
    """The fixed Operations message, with sanitised HTML and no remote images."""

    def get(self, request, uid: int):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        reference, error = _validated_reference(request, uid)
        if error is not None:
            return error
        folder, validity = reference
        try:
            raw, chosen, selected, error = _read_fixed_operations_message(folder, validity, uid)
            if error is not None:
                return error
            parsed = mime.parse_message(raw, load_remote_images=False)
            if len(parsed.attachments) > MAX_ATTACHMENTS:
                return Response({"detail": "Too many message attachments."}, status=413)
            if len(parsed.text) > MAX_BODY_CHARS or len(parsed.html) > MAX_BODY_CHARS:
                return Response({"detail": "Message content exceeds the safe reading limit."}, status=413)
        except Exception:
            logger.warning("Platform Mail Center message detail could not be read.")
            return self.unavailable()

        record_platform_action(
            actor=request.user, request=request, action="mail_center.message_read",
            target_label=OPERATIONS_MAILBOX,
            metadata={"folder_role": chosen.role or "custom"},
        )
        return Response({
            "mailbox": OPERATIONS_MAILBOX,
            "folder": folder,
            "uid": uid,
            "uid_validity": selected.uid_validity,
            "subject": parsed.subject,
            "from": {"name": parsed.from_name, "address": parsed.from_address},
            "to": parsed.to, "cc": parsed.cc,
            "reply_to": parsed.reply_to,
            "date": parsed.date,
            "message_id": parsed.message_id,
            "text": parsed.text,
            "html": parsed.html,
            "remote_images_blocked": parsed.has_remote_images,
            "attachments": [
                {
                    "part_id": a.part_id,
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "size": a.size,
                    "inline": a.inline,
                    "downloadable": a.size <= MAX_ATTACHMENT_BYTES,
                }
                for a in parsed.attachments
            ],
        })


class PlatformMailCenterAttachmentView(PlatformMailCenterBase):
    """Bounded attachment download only, never previewed inline on the app origin."""

    def get(self, request, uid: int, part_id: int):
        from django.http import HttpResponse
        from django.utils.http import content_disposition_header

        blocked = self.gate()
        if blocked is not None:
            return blocked
        reference, error = _validated_reference(request, uid)
        if error is not None:
            return error
        if not (0 <= part_id <= 255):
            return Response({"detail": "Invalid attachment reference."}, status=400)
        folder, validity = reference
        try:
            raw, chosen, selected, error = _read_fixed_operations_message(folder, validity, uid)
            if error is not None:
                return error
            parsed = mime.parse_message(raw, load_remote_images=False)
            if len(parsed.attachments) > MAX_ATTACHMENTS:
                return Response({"detail": "Too many message attachments."}, status=413)
            found = next((a for a in parsed.attachments if a.part_id == str(part_id)), None)
            if found is None:
                return Response({"detail": "Attachment not found."}, status=404)
            if found.size > MAX_ATTACHMENT_BYTES:
                return Response({"detail": "Attachment exceeds the download limit."}, status=413)
            filename, _, payload = mime.extract_attachment(raw, str(part_id))
            if len(payload) > MAX_ATTACHMENT_BYTES:
                return Response({"detail": "Attachment exceeds the download limit."}, status=413)
        except Exception:
            logger.warning("Platform Mail Center attachment could not be read.")
            return self.unavailable()
        record_platform_action(
            actor=request.user, request=request, action="mail_center.attachment_download",
            target_label=OPERATIONS_MAILBOX,
            metadata={"folder_role": chosen.role or "custom", "bytes": len(payload)},
        )
        result = HttpResponse(payload, content_type="application/octet-stream")
        result["Content-Disposition"] = content_disposition_header(True, filename)
        result["Content-Length"] = str(len(payload))
        result["Content-Security-Policy"] = "sandbox; default-src 'none'"
        result["X-Content-Type-Options"] = "nosniff"
        result["Referrer-Policy"] = "no-referrer"
        return result
