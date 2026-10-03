"""
Read-only, mailbox-scoped conversation endpoints for Phase 2.

Phase 3's premium thread UI can consume these endpoints without any new
mail-store index. Both endpoints scan headers only, never raw bodies, and always
open the mailbox from the authenticated PostBox session.
"""
from __future__ import annotations

from rest_framework.exceptions import Throttled
from rest_framework.response import Response

from apps.security import ratelimit
from apps.security.limits import POSTBOX_SEARCH_PER_MAILBOX

from . import imap, threading
from .views_mail import PostBoxView, _summary_payload, _fetch_summaries_chunked

MAX_CONVERSATION_MESSAGES = 5000
MAX_CONVERSATION_FOLDERS = 40
MAX_CONVERSATION_PAGE = 100
EXCLUDED_ROLES = frozenset({"drafts", "scheduled", "junk", "trash"})


def _role(folder: imap.FolderInfo) -> str:
    # list_folders honours special-use flags before canonical name fallback.
    return folder.role or imap.CANONICAL_ROLE_NAMES.get(folder.name.upper(), "")


def _member_payload(member: threading.ConversationMessage) -> dict:
    summary = _summary_payload(member.primary)
    summary["seen"] = not member.unread
    summary["flagged"] = member.flagged
    return {
        **summary,
        "copies": [
            {
                "folder": copy.folder,
                "uid": copy.uid,
                "uid_validity": copy.uid_validity,
                "seen": copy.seen,
                "flagged": copy.flagged,
            }
            for copy in member.copies
        ],
    }


def _conversation_payload(conversation: threading.Conversation) -> dict:
    latest = conversation.latest
    return {
        "id": conversation.id,
        "subject": latest.primary.subject,
        "latest": _member_payload(latest),
        "latest_date": latest.primary.date,
        "message_count": len(conversation.messages),
        "unread_count": conversation.unread_count,
        "flagged": conversation.flagged,
        "participants": conversation.participants,
        "has_inbox": conversation.has_inbox,
    }


class ConversationMixin(PostBoxView):
    """Shared bounded header scan; deliberately no cross-user cache."""

    def _rate_limit(self) -> None:
        decision = ratelimit.hit(
            POSTBOX_SEARCH_PER_MAILBOX.bucket,
            str(self.mailbox.pk),
            limit=POSTBOX_SEARCH_PER_MAILBOX.limit,
            window=POSTBOX_SEARCH_PER_MAILBOX.window,
        )
        if not decision.allowed:
            raise Throttled(
                wait=decision.retry_after,
                detail="Too many conversation requests. Please wait a moment.",
            )

    def _conversations(self, anchor: tuple[str, int, int] | None = None):
        with imap.open_mailbox(self.mailbox.email) as connection:
            folders = [
                item for item in connection.list_folders()
                if item.selectable and _role(item) not in EXCLUDED_ROLES
            ]
            if len(folders) > MAX_CONVERSATION_FOLDERS:
                return None, Response(
                    {
                        "detail": (
                            "This mailbox has too many folders for conversation "
                            "view. Use the regular folder view for now."
                        )
                    },
                    status=400,
                )

            role_by_folder = {item.name: _role(item) for item in folders}
            if anchor and anchor[0] not in role_by_folder:
                return None, Response({"detail": "That message was not found."}, status=404)

            collected: list[imap.MessageSummary] = []
            total = 0
            anchor_found = not anchor
            for folder in folders:
                info = connection.select(folder.name, readonly=True)
                if anchor and anchor[0] == folder.name:
                    if not info.uid_validity or info.uid_validity != anchor[1]:
                        return None, Response(
                            {"detail": "The folder has changed. Refresh and try again."},
                            status=409,
                        )
                uids = connection.search_uids(["ALL"])
                total += len(uids)
                if total > MAX_CONVERSATION_MESSAGES:
                    return None, Response(
                        {
                            "detail": (
                                "There are too many messages to group safely. "
                                "Use the regular folder view for now."
                            )
                        },
                        status=400,
                    )
                summaries = _fetch_summaries_chunked(connection, uids, chunk_size=250)
                if anchor and anchor[0] == folder.name:
                    # An absent or expunged UID must not resolve to another
                    # thread, even if a client guessed its message headers.
                    anchor_found = any(
                        summary.uid == anchor[2] and summary.uid_validity == anchor[1]
                        for summary in summaries
                    )
                collected.extend(summaries)

        if not anchor_found:
            return None, Response({"detail": "That message was not found."}, status=404)
        return threading.build_conversations(collected, role_by_folder), None


class ConversationListView(ConversationMixin):
    """GET /api/postbox/conversations/?scope=inbox|all&page=1&page_size=25."""

    def get(self, request):
        scope = (request.query_params.get("scope") or "inbox").strip().lower()
        if scope not in {"inbox", "all"}:
            return Response({"detail": "Scope must be inbox or all."}, status=400)

        try:
            page = int(request.query_params.get("page", "1"))
            page_size = int(request.query_params.get("page_size", "25"))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid conversation pagination."}, status=400)
        if page < 1 or page_size < 1 or page_size > MAX_CONVERSATION_PAGE:
            return Response({"detail": "Invalid conversation pagination."}, status=400)

        self._rate_limit()
        conversations, error = self._conversations()
        if error is not None:
            return error

        if scope == "inbox":
            conversations = [item for item in conversations if item.has_inbox]
        offset = (page - 1) * page_size
        return Response({
            "scope": scope,
            "page": page,
            "page_size": page_size,
            "total": len(conversations),
            "has_next": offset + page_size < len(conversations),
            "results": [
                _conversation_payload(item)
                for item in conversations[offset:offset + page_size]
            ],
        })


class ConversationForMessageView(ConversationMixin):
    """GET /api/postbox/conversations/for-message/?folder=...&uid=...&uid_validity=...

    Message reference is mandatory; a guessed message-id or conversation ID
    is never accepted as an authorization or lookup shortcut.
    """

    def get(self, request):
        folder = request.query_params.get("folder") or ""
        if not folder or len(folder) > 255:
            return Response({"detail": "Specify a message folder."}, status=400)
        try:
            uid = int(request.query_params["uid"])
            uid_validity = int(request.query_params["uid_validity"])
        except (KeyError, TypeError, ValueError):
            return Response({"detail": "Specify a valid message reference."}, status=400)
        if uid < 1 or uid_validity < 1:
            return Response({"detail": "Specify a valid message reference."}, status=400)

        self._rate_limit()
        conversations, error = self._conversations(anchor=(folder, uid_validity, uid))
        if error is not None:
            return error

        for item in conversations:
            if any(
                copy.folder == folder and copy.uid == uid
                and copy.uid_validity == uid_validity
                for member in item.messages for copy in member.copies
            ):
                return Response({
                    **_conversation_payload(item),
                    "messages": [_member_payload(member) for member in item.messages],
                })
        return Response({"detail": "That message was not found."}, status=404)
