"""
PostBox mail endpoints: folders, messages, flags, moves, compose and drafts.

EVERY VIEW IS SCOPED BY THE SESSION
    `request.mailbox` comes from the authenticated session and nowhere else.
    No endpoint here accepts a mailbox identifier, so there is no request that
    can be aimed at somebody else's mail — the same construction used for
    Primary Owner recovery, for the same reason.

A MESSAGE REFERENCE IS (folder, uidvalidity, uid)
    Never a sequence number, which changes when anything is expunged, and
    never a bare UID, which is meaningless if the folder was recreated.
    UIDVALIDITY is carried and checked so a stale browser tab acting on an old
    identifier is refused rather than acting on whatever now holds that UID.
"""
from __future__ import annotations

import logging

from django.conf import settings
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.security import ratelimit
from apps.security.limits import POSTBOX_SEARCH_PER_MAILBOX, POSTBOX_SEND_PER_MAILBOX

from . import imap, mime, sending
from .auth import PostBoxSessionAuthentication
from .models import PostBoxPreference

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100


class PostBoxView(APIView):
    """Shared base: PostBox session required, errors rendered as MateMail text."""

    permission_classes = [IsAuthenticated]
    authentication_classes = [PostBoxSessionAuthentication]

    def handle_exception(self, exc):
        """
        Mail-layer failures become MateMail-authored messages.

        Raw IMAP and SMTP errors name hosts, ports, internal addresses and
        Dovecot internals. The operator gets those in the log; the browser gets
        a sentence written for a person.
        """
        if isinstance(exc, imap.MailAccessError):
            logger.warning("PostBox mail access: %s", exc.log_message)
            return Response({"detail": exc.customer_message}, status=502)
        if isinstance(exc, sending.SendFailed):
            logger.warning("PostBox send: %s", exc.log_message)
            return Response({"detail": exc.customer_message}, status=400)
        return super().handle_exception(exc)

    @property
    def mailbox(self):
        return self.request.mailbox

    def preferences(self) -> PostBoxPreference:
        preference, _ = PostBoxPreference.objects.get_or_create(mailbox=self.mailbox)
        return preference


# ── folders ─────────────────────────────────────────────────────────────────

class FolderListView(PostBoxView):
    """Every folder, with counts. The sidebar's single call."""

    def get(self, request):
        with imap.open_mailbox(self.mailbox.email) as connection:
            folders = connection.list_folders()
            payload = []
            for folder in folders:
                if not folder.selectable:
                    continue
                total, unseen = connection.folder_counts(folder.name)
                payload.append({
                    "name": folder.name,
                    "role": folder.role,
                    "messages": total,
                    "unseen": unseen,
                })
        return Response({"results": payload})

    def post(self, request):
        name = (request.data.get("name") or "").strip()
        with imap.open_mailbox(self.mailbox.email) as connection:
            connection.create_folder(name)
        return Response({"name": name}, status=201)


class FolderDetailView(PostBoxView):
    def patch(self, request, name: str):
        new_name = (request.data.get("name") or "").strip()
        with imap.open_mailbox(self.mailbox.email) as connection:
            connection.rename_folder(name, new_name)
        return Response({"name": new_name})

    def delete(self, request, name: str):
        with imap.open_mailbox(self.mailbox.email) as connection:
            connection.delete_folder(name)
        return Response(status=204)


# ── listing ─────────────────────────────────────────────────────────────────

def _summary_payload(summary: imap.MessageSummary) -> dict:
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


class MessageListView(PostBoxView):
    """
    A page of messages.

    Paged over the UID list rather than with an IMAP cursor: the search runs
    server-side and returns UIDs, which are sliced here and fetched in ONE
    batched FETCH. That keeps the expensive part (the search) on Dovecot and
    the cheap part (paging) local, and never loads a mailbox into memory.
    """

    def get(self, request):
        folder = request.query_params.get("folder") or "INBOX"
        preference = self.preferences()

        try:
            page = max(int(request.query_params.get("page", 1)), 1)
        except (TypeError, ValueError):
            page = 1
        try:
            size = int(request.query_params.get("page_size", preference.messages_per_page))
        except (TypeError, ValueError):
            size = preference.messages_per_page
        size = max(1, min(size, MAX_PAGE_SIZE))

        criteria = _search_criteria(request.query_params)
        if criteria != ["ALL"]:
            decision = ratelimit.hit(
                POSTBOX_SEARCH_PER_MAILBOX.bucket, str(self.mailbox.pk),
                limit=POSTBOX_SEARCH_PER_MAILBOX.limit,
                window=POSTBOX_SEARCH_PER_MAILBOX.window,
            )
            if not decision.allowed:
                from rest_framework.exceptions import Throttled

                raise Throttled(
                    wait=decision.retry_after,
                    detail="Too many searches. Please wait a moment.",
                )

        with imap.open_mailbox(self.mailbox.email) as connection:
            info = connection.select(folder, readonly=True)
            uids = connection.search_uids(criteria)
            start = (page - 1) * size
            window = uids[start:start + size]
            summaries = connection.fetch_summaries(window)

        return Response({
            "folder": folder,
            "uid_validity": info.uid_validity,
            "page": page,
            "page_size": size,
            "total": len(uids),
            "has_next": start + size < len(uids),
            "results": [_summary_payload(s) for s in summaries],
        })


def _search_criteria(params) -> list[str]:
    """
    Translate the UI's filters into IMAP SEARCH.

    Every value is passed as its own argument rather than interpolated into a
    command string — imaplib quotes each one, which is what stops a search term
    containing a quote or a space from being read as further IMAP syntax.

    Deliberately IMAP SEARCH rather than a search index: Dovecot has no
    full-text index configured, and for Private Beta mailbox sizes a
    server-side scan is both sufficient and far less machinery than adding one.
    """
    criteria: list[str] = []

    text = (params.get("q") or "").strip()
    if text:
        criteria += ["TEXT", text]
    for key, keyword in (("from", "FROM"), ("to", "TO"), ("subject", "SUBJECT")):
        value = (params.get(key) or "").strip()
        if value:
            criteria += [keyword, value]

    if params.get("unread") == "true":
        criteria.append("UNSEEN")
    elif params.get("unread") == "false":
        criteria.append("SEEN")
    if params.get("starred") == "true":
        criteria.append("FLAGGED")

    since = (params.get("since") or "").strip()
    if since:
        criteria += ["SINCE", since]
    before = (params.get("before") or "").strip()
    if before:
        criteria += ["BEFORE", before]

    return criteria or ["ALL"]


# ── reading ─────────────────────────────────────────────────────────────────

class MessageDetailView(PostBoxView):
    """
    One message, parsed and sanitised.

    Reading does NOT mark as read. That is a separate, explicit call, because
    a preview pane that marks everything seen as the list scrolls past is how
    people lose track of their own mail.
    """

    def get(self, request, folder: str, uid: int):
        preference = self.preferences()
        show_remote = (
            request.query_params.get("remote_images") == "true"
            or preference.load_remote_images
        )

        with imap.open_mailbox(self.mailbox.email) as connection:
            info = connection.select(folder, readonly=True)
            _assert_uid_validity(request, info.uid_validity)
            raw = connection.fetch_raw(uid)
            parsed = mime.parse_message(raw, load_remote_images=show_remote)
            # Bcc only for this mailbox's own drafts, which store it so they
            # can be reopened whole. A Bcc header on any other message is not
            # reported, and nothing is inferred from the envelope.
            bcc = parsed.bcc if parsed.bcc and _is_drafts(connection, folder) else []

        return Response({
            "uid": uid,
            "uid_validity": info.uid_validity,
            "folder": folder,
            "subject": parsed.subject,
            "from": {"name": parsed.from_name, "address": parsed.from_address},
            "to": parsed.to,
            "cc": parsed.cc,
            "bcc": bcc,
            "reply_to": parsed.reply_to,
            "date": parsed.date,
            "message_id": parsed.message_id,
            "in_reply_to": parsed.in_reply_to,
            "references": parsed.references,
            "text": parsed.text,
            "html": parsed.html,
            "remote_images_blocked": parsed.has_remote_images and not show_remote,
            "attachments": [
                {
                    "part_id": a.part_id,
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "size": a.size,
                    "inline": a.inline,
                    "content_id": a.content_id,
                }
                for a in parsed.attachments
            ],
        })


class MessageRawView(PostBoxView):
    """The original message source, for "show original"."""

    def get(self, request, folder: str, uid: int):
        from django.http import HttpResponse

        with imap.open_mailbox(self.mailbox.email) as connection:
            connection.select(folder, readonly=True)
            raw = connection.fetch_raw(uid)

        # text/plain, not message/rfc822: the browser should display it, not
        # offer to open it in a mail client.
        response = HttpResponse(raw, content_type="text/plain; charset=utf-8")
        response["X-Content-Type-Options"] = "nosniff"
        return response


class AttachmentView(PostBoxView):
    """
    Download one attachment.

    `Content-Disposition: attachment` unconditionally, with a sanitised
    filename, and `nosniff`. An HTML attachment rendered inline would run in
    PostBox's own origin with the session cookie attached — which is why
    nothing here is ever served inline, whatever its type claims to be.
    """

    def get(self, request, folder: str, uid: int, part_id: str):
        from django.http import HttpResponse
        from urllib.parse import quote

        with imap.open_mailbox(self.mailbox.email) as connection:
            connection.select(folder, readonly=True)
            raw = connection.fetch_raw(uid)

        try:
            filename, content_type, payload = mime.extract_attachment(raw, part_id)
        except KeyError:
            return Response({"detail": "That attachment could not be found."}, status=404)

        response = HttpResponse(payload, content_type="application/octet-stream")
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Length"] = str(len(payload))
        # RFC 5987 for the non-ASCII form, plus a plain fallback.
        response["Content-Disposition"] = (
            f"attachment; filename=\"{filename}\"; "
            f"filename*=UTF-8''{quote(filename)}"
        )
        logger.info(
            "PostBox attachment served: mailbox=%s type=%s bytes=%d",
            self.mailbox.pk, content_type, len(payload),
        )
        return response


def _is_drafts(connection, folder: str) -> bool:
    """Whether `folder` is the mailbox's Drafts, resolved by role as DraftView does."""
    roles = {f.role: f.name for f in connection.list_folders() if f.role}
    return folder == roles.get("drafts", "Drafts")


def _assert_uid_validity(request, actual: int) -> None:
    """
    Refuse an action carrying a stale UIDVALIDITY.

    A browser tab open since before a folder was recreated holds UIDs that now
    refer to different messages. Acting on them would move or delete the wrong
    mail, silently.
    """
    claimed = request.query_params.get("uid_validity") or request.data.get("uid_validity")
    if claimed in (None, ""):
        return
    try:
        if int(claimed) != int(actual):
            raise imap.MailAccessError(
                "This folder has changed. Please refresh and try again.",
                f"UIDVALIDITY mismatch: claimed={claimed} actual={actual}",
            )
    except (TypeError, ValueError):
        return


# ── flags and moves ─────────────────────────────────────────────────────────

class BulkActionSerializer(serializers.Serializer):
    folder = serializers.CharField()
    uids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    uid_validity = serializers.IntegerField(required=False)


class MessageActionView(PostBoxView):
    """
    Every state change, in one endpoint keyed by `action`.

    One view because they are the same operation with a different verb, and
    because the alternative — a dozen near-identical views — is a dozen places
    for the mailbox scoping to be forgotten.
    """

    ACTIONS = {
        "read", "unread", "star", "unstar", "archive", "trash",
        "spam", "not-spam", "move", "restore", "delete",
    }

    def post(self, request, action: str):
        if action not in self.ACTIONS:
            return Response({"detail": "Unknown action."}, status=400)

        serializer = BulkActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        uids = data["uids"]

        if len(uids) > 500:
            return Response(
                {"detail": "Too many messages selected at once."}, status=400
            )

        with imap.open_mailbox(self.mailbox.email) as connection:
            info = connection.select(data["folder"])
            _assert_uid_validity(request, info.uid_validity)
            roles = {f.role: f.name for f in connection.list_folders() if f.role}

            if action == "read":
                connection.mark_seen(uids, True)
            elif action == "unread":
                connection.mark_seen(uids, False)
            elif action == "star":
                connection.mark_flagged(uids, True)
            elif action == "unstar":
                connection.mark_flagged(uids, False)
            elif action == "archive":
                connection.move(uids, roles.get("archive", "Archive"))
            elif action == "trash":
                connection.move(uids, roles.get("trash", "Trash"))
            elif action == "spam":
                connection.move(uids, roles.get("junk", "Junk"))
            elif action == "not-spam":
                connection.move(uids, "INBOX")
            elif action == "restore":
                # Restore returns mail to the Inbox. Putting it back where it
                # came from would need provenance IMAP does not record.
                connection.move(uids, "INBOX")
            elif action == "move":
                destination = (request.data.get("destination") or "").strip()
                if not destination:
                    return Response({"detail": "Choose a destination folder."}, status=400)
                connection.move(uids, destination)
            elif action == "delete":
                # Permanent, and only from Trash or Junk. Erasing from an
                # arbitrary folder would make a mis-click unrecoverable.
                if data["folder"] not in (roles.get("trash", "Trash"), roles.get("junk", "Junk")):
                    return Response(
                        {"detail": "Messages can only be deleted permanently from Trash or Spam."},
                        status=400,
                    )
                connection.delete_permanently(uids)

        logger.info(
            "PostBox %s: mailbox=%s folder=%s count=%d",
            action, self.mailbox.pk, data["folder"], len(uids),
        )
        return Response({"action": action, "count": len(uids)})
