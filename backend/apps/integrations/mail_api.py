import logging

from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.postbox import imap, mime

from .authentication import IntegrationAccessAuthentication

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100


class ConnectedMailView(APIView):
    authentication_classes = [IntegrationAccessAuthentication]
    permission_classes = []
    required_scope = ""

    def handle_exception(self, exc):
        if isinstance(exc, imap.MailAccessError):
            logger.warning("Connected-app mail access: %s", exc.log_message)
            return Response({"detail": exc.customer_message}, status=502)
        return super().handle_exception(exc)

    def scope_denied(self):
        if self.required_scope and not self.request.integration.has_permission(
            self.required_scope
        ):
            return Response(
                {"detail": "This connected app does not have the required mailbox access."},
                status=403,
            )
        return None

    @property
    def mailbox(self):
        return self.request.integration.mailbox


def _summary_payload(summary):
    return {
        "uid": summary.uid,
        "uid_validity": summary.uid_validity,
        "folder": summary.folder,
        "message_id": summary.message_id,
        "subject": summary.subject,
        "from": {"name": summary.from_name, "address": summary.from_address},
        "to": summary.to,
        "cc": summary.cc,
        "date": summary.date,
        "size": summary.size,
        "seen": summary.seen,
        "flagged": summary.flagged,
        "answered": summary.answered,
        "draft": summary.draft,
        "has_attachments": summary.has_attachments,
        "in_reply_to": summary.in_reply_to,
        "references": summary.thread_references,
    }


def _uid_validity_matches(claimed, actual):
    if claimed in (None, ""):
        return True
    try:
        return int(claimed) == int(actual)
    except (TypeError, ValueError):
        return False


class ConnectedFolderListView(ConnectedMailView):
    required_scope = "mail.read"

    def get(self, request):
        denied = self.scope_denied()
        if denied:
            return denied
        with imap.open_mailbox(self.mailbox.email) as connection:
            folders = []
            for folder in connection.list_folders():
                if not folder.selectable:
                    continue
                total, unseen = connection.folder_counts(folder.name)
                folders.append(
                    {
                        "name": folder.name,
                        "role": folder.role,
                        "messages": total,
                        "unseen": unseen,
                    }
                )
        return Response({"results": folders})


class ConnectedMessageListView(ConnectedMailView):
    required_scope = "mail.read"

    def get(self, request):
        denied = self.scope_denied()
        if denied:
            return denied

        folder = (request.query_params.get("folder") or "INBOX").strip()
        unread = request.query_params.get("unread")
        try:
            page = max(int(request.query_params.get("page", 1)), 1)
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.query_params.get("page_size", 50))
        except (TypeError, ValueError):
            page_size = 50
        page_size = max(1, min(page_size, MAX_PAGE_SIZE))

        criteria = ["ALL"]
        if unread == "true":
            criteria = ["UNSEEN"]
        elif unread == "false":
            criteria = ["SEEN"]

        with imap.open_mailbox(self.mailbox.email) as connection:
            info = connection.select(folder, readonly=True)
            uids = connection.search_uids(criteria)
            start = (page - 1) * page_size
            window = uids[start : start + page_size]
            summaries = connection.fetch_summaries(window)

        return Response(
            {
                "folder": folder,
                "uid_validity": info.uid_validity,
                "page": page,
                "page_size": page_size,
                "total": len(uids),
                "has_next": start + page_size < len(uids),
                "results": [_summary_payload(item) for item in summaries],
            }
        )


class ConnectedMessageDetailView(ConnectedMailView):
    required_scope = "mail.read"

    def get(self, request):
        denied = self.scope_denied()
        if denied:
            return denied

        folder = (request.query_params.get("folder") or "INBOX").strip()
        try:
            uid = int(request.query_params.get("uid"))
        except (TypeError, ValueError):
            return Response({"detail": "A valid uid is required."}, status=400)

        with imap.open_mailbox(self.mailbox.email) as connection:
            info = connection.select(folder, readonly=True)
            if not _uid_validity_matches(
                request.query_params.get("uid_validity"), info.uid_validity
            ):
                return Response(
                    {"detail": "This folder has changed. Refresh and try again."},
                    status=409,
                )
            raw = connection.fetch_raw(uid)

        parsed = mime.parse_message(raw, load_remote_images=False)
        return Response(
            {
                "uid": uid,
                "uid_validity": info.uid_validity,
                "folder": folder,
                "subject": parsed.subject,
                "from": {"name": parsed.from_name, "address": parsed.from_address},
                "to": parsed.to,
                "cc": parsed.cc,
                "reply_to": parsed.reply_to,
                "date": parsed.date,
                "message_id": parsed.message_id,
                "in_reply_to": parsed.in_reply_to,
                "references": parsed.references,
                "text": parsed.text,
                "html": parsed.html,
                "attachments": [
                    {
                        "part_id": item.part_id,
                        "filename": item.filename,
                        "content_type": item.content_type,
                        "size": item.size,
                        "inline": item.inline,
                        "content_id": item.content_id,
                    }
                    for item in parsed.attachments
                ],
            }
        )


class ConnectedMessageRawView(ConnectedMailView):
    required_scope = "mail.read"

    def get(self, request):
        denied = self.scope_denied()
        if denied:
            return denied

        folder = (request.query_params.get("folder") or "INBOX").strip()
        try:
            uid = int(request.query_params.get("uid"))
        except (TypeError, ValueError):
            return Response({"detail": "A valid uid is required."}, status=400)

        with imap.open_mailbox(self.mailbox.email) as connection:
            info = connection.select(folder, readonly=True)
            if not _uid_validity_matches(
                request.query_params.get("uid_validity"), info.uid_validity
            ):
                return Response(
                    {"detail": "This folder has changed. Refresh and try again."},
                    status=409,
                )
            raw = connection.fetch_raw(uid)

        response = HttpResponse(raw, content_type="message/rfc822")
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Length"] = str(len(raw))
        return response


class ConnectedMessageSeenView(ConnectedMailView):
    required_scope = "mail.modify"

    def post(self, request):
        denied = self.scope_denied()
        if denied:
            return denied

        folder = str(request.data.get("folder") or "INBOX").strip()
        raw_uids = request.data.get("uids")
        if raw_uids is None:
            raw_uids = [request.data.get("uid")]
        if not isinstance(raw_uids, list):
            return Response({"detail": "uids must be a list."}, status=400)
        try:
            uids = [int(value) for value in raw_uids if value not in (None, "")]
        except (TypeError, ValueError):
            return Response({"detail": "All UIDs must be integers."}, status=400)
        if not uids:
            return Response({"detail": "At least one UID is required."}, status=400)

        seen = bool(request.data.get("seen", True))
        with imap.open_mailbox(self.mailbox.email) as connection:
            info = connection.select(folder, readonly=False)
            if not _uid_validity_matches(
                request.data.get("uid_validity"), info.uid_validity
            ):
                return Response(
                    {"detail": "This folder has changed. Refresh and try again."},
                    status=409,
                )
            connection.mark_seen(uids, seen)

        return Response(
            {
                "updated": True,
                "folder": folder,
                "uid_validity": info.uid_validity,
                "uids": uids,
                "seen": seen,
            }
        )
