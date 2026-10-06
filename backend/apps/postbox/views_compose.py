"""
Compose, drafts and scheduled send.

THE SENT COPY IS EVIDENCE
    Submission happens first and the Sent copy second, always. A message in
    Sent means Postfix accepted it — that is what a person is checking when
    they look. Filing first and sending second would put messages in Sent that
    never left, which is the same lie as a backup that reports success without
    writing an archive.

DRAFTS ARE REAL MAIL
    In the Drafts folder, as RFC 5322 messages with the `\\Draft` flag, not
    rows in Postgres and certainly not `localStorage`. They therefore survive
    logout, a closed browser and a different device, and they are visible to
    any other IMAP client the person uses.

    Editing a draft REPLACES it: the new version is appended and the old UID
    expunged, in that order, so a failure leaves the old draft rather than
    nothing. IMAP has no edit-in-place, and an implementation that only
    appended would fill Drafts with every keystroke's autosave.
"""
from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime
from email.utils import formataddr

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response

from apps.security import ratelimit
from apps.security.limits import POSTBOX_SEND_PER_MAILBOX

from . import imap, mime, sending, signatures
from .models import PostBoxPreference, ScheduledMessage
from .views_mail import PostBoxView, _assert_uid_validity

logger = logging.getLogger(__name__)


class AttachmentInputSerializer(serializers.Serializer):
    filename = serializers.CharField(max_length=255)
    content_type = serializers.CharField(max_length=180, required=False, allow_blank=True)
    #: Base64. The upload rides in the JSON body because PostBox's compose is a
    #: single atomic submit — a separate multipart upload endpoint would need
    #: its own lifecycle, its own orphan cleanup and its own authorisation.
    data = serializers.CharField()


class ExistingAttachmentSerializer(serializers.Serializer):
    """
    A reference to an attachment that already exists in THIS mailbox.

    Used by Forward and by reopened/autosaved drafts. The browser never has to
    download an original attachment and upload it back just to keep it attached.
    The server resolves the part from the authenticated mailbox at build time.
    """

    folder = serializers.CharField(max_length=255)
    uid = serializers.IntegerField(min_value=1)
    uid_validity = serializers.IntegerField(min_value=0, required=False, default=0)
    part_id = serializers.CharField(max_length=80)


class ComposeSerializer(serializers.Serializer):
    from_address = serializers.EmailField()
    to = serializers.ListField(child=serializers.EmailField(), allow_empty=True, default=list)
    cc = serializers.ListField(child=serializers.EmailField(), allow_empty=True, default=list)
    bcc = serializers.ListField(child=serializers.EmailField(), allow_empty=True, default=list)
    subject = serializers.CharField(required=False, allow_blank=True, default="", max_length=900)
    text = serializers.CharField(required=False, allow_blank=True, default="")
    html = serializers.CharField(required=False, allow_blank=True, default="")
    # Opt-in quote is separate from editable text; never silently inserted.
    quoted_text = serializers.CharField(required=False, allow_blank=True, default="", max_length=500000)
    in_reply_to = serializers.CharField(required=False, allow_blank=True, default="")
    references = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    attachments = AttachmentInputSerializer(many=True, required=False, default=list)
    existing_attachments = ExistingAttachmentSerializer(
        many=True,
        required=False,
        default=list,
    )
    signature_id = serializers.UUIDField(required=False, allow_null=True)

    #: Present for a scheduled send. Absent means send now.
    send_at = serializers.DateTimeField(required=False, allow_null=True)

    #: The draft this replaces, so composing from a draft does not leave the
    #: old copy behind.
    draft_uid = serializers.IntegerField(required=False, allow_null=True)


def _decode_attachments(items, *, limit_mb: int, total_limit_mb: int):
    """
    Decode and bound the uploads.

    Both limits matter: one oversized file and a thousand small ones are the
    same denial of service, and Postfix will refuse an oversized message anyway
    — better to say so before the person has typed it.
    """
    decoded: list[tuple[str, str, bytes]] = []
    total = 0
    for item in items or []:
        try:
            payload = base64.b64decode(item["data"], validate=True)
        except (binascii.Error, ValueError, KeyError):
            raise serializers.ValidationError(
                {"attachments": "An attachment could not be read."}
            )
        if len(payload) > limit_mb * 1024 * 1024:
            raise serializers.ValidationError(
                {"attachments": f"'{item.get('filename', 'file')}' is larger than {limit_mb} MB."}
            )
        total += len(payload)
        if total > total_limit_mb * 1024 * 1024:
            raise serializers.ValidationError(
                {"attachments": f"The message is larger than {total_limit_mb} MB in total."}
            )
        decoded.append((
            mime.safe_filename(item.get("filename", "attachment")),
            item.get("content_type") or mime.guess_content_type(item.get("filename", "")),
            payload,
        ))
    return decoded


def _resolve_existing_attachments(
    mailbox,
    items,
    *,
    limit_mb: int,
    total_limit_mb: int,
):
    """
    Resolve attachment refs inside the authenticated mailbox.

    Fetches each source message once even when several parts are forwarded.
    UIDVALIDITY is checked when the caller supplied it so a stale draft/forward
    cannot silently attach a different message after a mailbox reset.
    """
    if not items:
        return []

    grouped: dict[tuple[str, int, int], list[str]] = {}
    for item in items:
        key = (
            item["folder"],
            int(item["uid"]),
            int(item.get("uid_validity") or 0),
        )
        grouped.setdefault(key, []).append(item["part_id"])

    decoded: list[tuple[str, str, bytes]] = []
    total = 0
    with imap.open_mailbox(mailbox.email) as connection:
        for (folder, uid, expected_validity), part_ids in grouped.items():
            info = connection.select(folder, readonly=True)
            if expected_validity and info.uid_validity != expected_validity:
                raise serializers.ValidationError({
                    "existing_attachments": (
                        "An original attachment changed before it could be "
                        "copied. Reopen the message and try again."
                    )
                })
            raw = connection.fetch_raw(uid)
            for part_id in part_ids:
                try:
                    filename, content_type, payload = mime.extract_attachment(
                        raw,
                        part_id,
                    )
                except KeyError:
                    raise serializers.ValidationError({
                        "existing_attachments": (
                            "An original attachment could not be found. "
                            "Reopen the message and try again."
                        )
                    })
                if len(payload) > limit_mb * 1024 * 1024:
                    raise serializers.ValidationError({
                        "existing_attachments": (
                            f"'{filename}' is larger than {limit_mb} MB."
                        )
                    })
                total += len(payload)
                if total > total_limit_mb * 1024 * 1024:
                    raise serializers.ValidationError({
                        "existing_attachments": (
                            f"The message is larger than {total_limit_mb} MB "
                            "in total."
                        )
                    })
                decoded.append((filename, content_type, payload))
    return decoded


def _attachment_refs_for_saved_message(
    message,
    *,
    folder: str,
    uid: int,
    uid_validity: int,
):
    """Return refs for every attachment in a newly saved draft."""
    parsed = mime.parse_message(message.as_bytes(), load_remote_images=False)
    return [
        {
            "folder": folder,
            "uid": uid,
            "uid_validity": uid_validity,
            "part_id": item.part_id,
            "filename": item.filename,
            "content_type": item.content_type,
            "size": item.size,
        }
        for item in parsed.attachments
    ]


class ComposeMixin:
    """Shared building of a message from a compose payload."""

    def build(self, data, *, mailbox, actor_mailbox=None, draft=False):
        actor = actor_mailbox or mailbox
        identity = sending.assert_may_send_as(
            mailbox,
            data["from_address"],
            actor_mailbox=actor,
        )

        html = data.get("html") or ""
        text = data.get("text") or ""
        quoted_text = data.get("quoted_text") or ""

        # An explicitly chosen signature that this mailbox no longer has —
        # deleted elsewhere, or never its own — is refused. Sending without
        # it would silently change the message somebody chose to send.
        signature_id = data.get("signature_id")
        signature = signatures.for_mailbox(mailbox, signature_id)
        if signature_id and signature is None:
            raise serializers.ValidationError({
                "signature_id": [
                    "That signature is no longer available. Choose another "
                    "signature, or none."
                ],
            })

        if draft:
            # A draft keeps the body as written and the chosen signature
            # as metadata, so it can be reopened and edited with the same
            # choice. The signature is applied only when it is sent.
            related = []
            if quoted_text:
                # Ordinary text/plain draft remains usable in other IMAP clients.
                # Draft-only digest metadata lets PostBox recover a clean editor.
                text, _ = mime.append_reply_quote(text, "", quoted_text)
        else:
            # The signature is applied HERE and nowhere else — see
            # apps/postbox/signatures.py. The composer renders a preview but
            # never puts the signature in the body it submits, so there is
            # nothing to double up.
            #
            # The previous code did `html = signature.html if not html`, and
            # PostBox's composer only ever sends plain text — so the HTML
            # alternative became the signature with no message above it.
            text, html, related = signatures.apply(
                text=text, html=html, signature=signature
            )
            # Keep signature above the original, never after the quoted thread.
            text, html = mime.append_reply_quote(text, html, quoted_text)

        uploaded_attachments = _decode_attachments(
            data.get("attachments"),
            limit_mb=settings.POSTBOX_MAX_ATTACHMENT_MB,
            total_limit_mb=settings.POSTBOX_MAX_MESSAGE_MB,
        )
        existing_attachments = _resolve_existing_attachments(
            mailbox,
            data.get("existing_attachments"),
            limit_mb=settings.POSTBOX_MAX_ATTACHMENT_MB,
            total_limit_mb=settings.POSTBOX_MAX_MESSAGE_MB,
        )
        attachments = [*existing_attachments, *uploaded_attachments]
        total_attachment_bytes = sum(len(payload) for _, _, payload in attachments)
        if total_attachment_bytes > settings.POSTBOX_MAX_MESSAGE_MB * 1024 * 1024:
            raise serializers.ValidationError({
                "attachments": (
                    f"The message is larger than "
                    f"{settings.POSTBOX_MAX_MESSAGE_MB} MB in total."
                )
            })

        message = mime.build_message(
            from_address=identity.address,
            from_name=identity.name,
            to=data.get("to") or [],
            cc=data.get("cc") or [],
            bcc=data.get("bcc") or [],
            subject=data.get("subject") or "",
            text=text,
            html=html,
            in_reply_to=data.get("in_reply_to") or "",
            references=data.get("references") or [],
            attachments=attachments,
            related=related,
            keep_bcc=draft,
            draft_signature_id=str(signature.id) if draft and signature else "",
            draft_quoted_text=quoted_text if draft else "",
        )
        if (
            identity.send_mode == "on_behalf"
            and actor.pk != mailbox.pk
            and not draft
        ):
            message["Sender"] = formataddr((
                actor.full_name or "",
                actor.email,
            ))
        return message, identity


class SendView(PostBoxView, ComposeMixin):
    def required_team_box_permission(self, request) -> str:
        return "send"
    """
    Send now, or schedule.

    Both paths share validation and message building so a scheduled message is
    byte-for-byte what an immediate one would have been — including its
    Message-ID, which is generated once here and preserved through submission
    and the Sent copy.
    """

    def post(self, request):
        serializer = ComposeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        mailbox = self.mailbox
        actor = request.identity_mailbox

        recipients = sending.envelope_recipients(
            data.get("to") or [], data.get("cc") or [], data.get("bcc") or []
        )
        if not recipients:
            return Response({"detail": "Add at least one recipient."}, status=400)

        sending.assert_organization_may_send(mailbox)

        decision = ratelimit.hit(
            POSTBOX_SEND_PER_MAILBOX.bucket, str(mailbox.pk),
            limit=POSTBOX_SEND_PER_MAILBOX.limit, window=POSTBOX_SEND_PER_MAILBOX.window,
        )
        if not decision.allowed:
            from rest_framework.exceptions import Throttled

            raise Throttled(
                wait=decision.retry_after,
                detail="You have sent a lot of mail recently. Please wait a little.",
            )

        send_at = data.get("send_at")
        if send_at:
            # Store an EDITABLE source message in Scheduled: Bcc and the
            # selected signature remain draft metadata, while the signature
            # itself is not applied yet. The worker finalises it at send time.
            scheduled_source, _ = self.build(
                data,
                mailbox=mailbox,
                actor_mailbox=actor,
                draft=True,
            )
            return self._schedule(
                scheduled_source,
                data,
                send_at,
                recipients,
                mailbox,
                actor,
            )

        message, identity = self.build(
            data,
            mailbox=mailbox,
            actor_mailbox=actor,
        )
        sending.submit(
            message,
            mailbox=actor,
            envelope_from=identity.address,
            recipients=recipients,
        )

        # Only now. Submission succeeded, so this copy is true.
        appended = self._file_in_sent(mailbox, message)
        self._discard_draft(mailbox, data.get("draft_uid"))

        logger.info(
            "PostBox sent: mailbox=%s recipients=%d filed=%s",
            mailbox.pk, len(recipients), appended,
        )
        return Response({
            "sent": True,
            "message_id": message["Message-ID"],
            "filed_in_sent": appended,
        })

    def _schedule(
        self,
        message,
        data,
        send_at,
        recipients,
        mailbox,
        submission_mailbox,
    ):
        if send_at <= timezone.now():
            return Response({"detail": "Choose a time in the future."}, status=400)

        raw = message.as_bytes()
        with imap.open_mailbox(mailbox.email) as connection:
            connection.ensure_standard_folders()
            uid_validity, uid = connection.append(
                "Scheduled", raw, flags="\\Draft"
            )

        if not uid:
            # Without UIDPLUS there is no reliable way to address the message
            # later, and a scheduled send that cannot find its own message is
            # worse than a refusal.
            return Response(
                {"detail": "Scheduled send is unavailable on this server."}, status=503
            )

        scheduled = ScheduledMessage.objects.create(
            mailbox=mailbox,
            submission_mailbox=submission_mailbox,
            folder="Scheduled",
            uid_validity=uid_validity,
            uid=uid,
            subject=data.get("subject") or "",
            recipients=", ".join(recipients),
            scheduled_at=send_at,
        )
        self._discard_draft(mailbox, data.get("draft_uid"))

        logger.info(
            "PostBox scheduled %s for mailbox=%s at %s", scheduled.id, mailbox.pk, send_at
        )
        return Response({
            "scheduled": True,
            "id": str(scheduled.id),
            "scheduled_at": send_at.isoformat(),
        }, status=201)

    @staticmethod
    def _file_in_sent(mailbox, message) -> bool:
        """
        Append to Sent. A failure here is logged, not raised.

        The mail has already gone. Turning a filing problem into a send error
        would tell the person their message failed when it did not, and they
        would send it again.
        """
        try:
            with imap.open_mailbox(mailbox.email) as connection:
                roles = {f.role: f.name for f in connection.list_folders() if f.role}
                connection.append(
                    roles.get("sent", "Sent"), message.as_bytes(), flags="\\Seen"
                )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "PostBox: message sent for mailbox %s but not filed in Sent: %r",
                mailbox.pk, exc,
            )
            return False

    @staticmethod
    def _discard_draft(mailbox, draft_uid) -> None:
        if not draft_uid:
            return
        try:
            with imap.open_mailbox(mailbox.email) as connection:
                roles = {f.role: f.name for f in connection.list_folders() if f.role}
                connection.select(roles.get("drafts", "Drafts"))
                connection.delete_permanently([int(draft_uid)])
        except Exception as exc:  # noqa: BLE001
            logger.warning("PostBox: draft %s could not be removed: %r", draft_uid, exc)


class DraftView(PostBoxView, ComposeMixin):
    def required_team_box_permission(self, request) -> str:
        return "send" if request.method == "POST" else "manage"
    """
    Save a draft, replacing a previous version.

    APPEND then EXPUNGE, in that order. The reverse would delete the only copy
    before the replacement exists, so an interrupted autosave would lose the
    message being written.
    """

    def post(self, request):
        serializer = ComposeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The draft keeps its Bcc and its chosen signature as headers so it
        # reopens whole; it is never submitted as-is (see mime.build_message).
        message, _ = self.build(
            data,
            mailbox=self.mailbox,
            actor_mailbox=request.identity_mailbox,
            draft=True,
        )
        previous = data.get("draft_uid")

        with imap.open_mailbox(self.mailbox.email) as connection:
            roles = {f.role: f.name for f in connection.list_folders() if f.role}
            drafts = roles.get("drafts", "Drafts")

            uid_validity, uid = connection.append(
                drafts, message.as_bytes(), flags="\\Draft \\Seen"
            )

            if previous:
                connection.select(drafts)
                connection.delete_permanently([int(previous)])

        return Response({
            "folder": drafts,
            "uid": uid,
            "uid_validity": uid_validity,
            "saved_at": timezone.now().isoformat(),
            "attachments": _attachment_refs_for_saved_message(
                message,
                folder=drafts,
                uid=uid,
                uid_validity=uid_validity,
            ),
        })

    def delete(self, request, uid: int):
        with imap.open_mailbox(self.mailbox.email) as connection:
            roles = {f.role: f.name for f in connection.list_folders() if f.role}
            connection.select(roles.get("drafts", "Drafts"))
            connection.delete_permanently([int(uid)])
        return Response(status=204)


class ReplyContextView(PostBoxView):
    """
    Everything the composer needs to open a Reply, Reply All or Forward.

    Computed server-side because the rules are not cosmetic: Reply-To takes
    precedence over From, Reply All must remove the mailbox's own identities or
    every reply adds the sender to their own Cc, and the quoted body has to be
    sanitised before it is put in an editor.
    """

    def get(self, request, folder: str, uid: int):
        mode = request.query_params.get("mode", "reply")
        if mode not in ("reply", "reply-all", "forward"):
            return Response({"detail": "Unknown reply mode."}, status=400)

        with imap.open_mailbox(self.mailbox.email) as connection:
            info = connection.select(folder, readonly=True)
            _assert_uid_validity(request, info.uid_validity)
            raw = connection.fetch_raw(uid)

        parsed = mime.parse_message(raw, load_remote_images=False)
        identities = {i.address.lower() for i in sending.allowed_identities(self.mailbox)}

        quoted_text = ""
        if mode == "forward":
            text, html = mime.forward_body(parsed)
            subject = parsed.subject
            if not subject.lower().startswith("fwd:"):
                subject = f"Fwd: {subject}"
            to, cc = [], []
        else:
            quoted_text, _ = mime.quote_for_reply(parsed)
            text, html = "", ""  # a reply must start with a clean editor
            subject = parsed.subject
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            to, cc = mime.reply_recipients(
                parsed, identities, reply_all=(mode == "reply-all")
            )

        preference, _ = PostBoxPreference.objects.get_or_create(mailbox=self.mailbox)
        default_identity = preference.default_identity or self.mailbox.email

        return Response({
            "mode": mode,
            "subject": subject,
            "to": to,
            "cc": cc,
            "from_address": default_identity,
            "text": text,
            "html": html,
            "quoted_text": quoted_text,
            "in_reply_to": parsed.message_id,
            "references": [*parsed.references, parsed.message_id] if parsed.message_id
                          else parsed.references,
            "attachments": [
                {
                    "folder": folder,
                    "uid": uid,
                    "uid_validity": info.uid_validity,
                    "part_id": a.part_id,
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "size": a.size,
                }
                for a in parsed.attachments
            ] if mode == "forward" else [],
        })


class ScheduledListView(PostBoxView):
    """Scheduled messages waiting to go, and recent outcomes."""

    def get(self, request):
        rows = ScheduledMessage.objects.for_mailbox(self.mailbox).order_by("scheduled_at")[:100]
        return Response({"results": [
            {
                "id": str(row.id),
                "subject": row.subject,
                "recipients": row.recipients,
                "folder": row.folder,
                "uid": row.uid,
                "uid_validity": row.uid_validity,
                "scheduled_at": row.scheduled_at.isoformat(),
                "state": row.state,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            }
            for row in rows
        ]})


class ScheduledDetailView(PostBoxView):
    """Cancel or reschedule. Only while still pending."""

    def patch(self, request, scheduled_id):
        row = ScheduledMessage.objects.for_mailbox(self.mailbox).filter(pk=scheduled_id).first()
        if row is None:
            return Response({"detail": "Not found."}, status=404)
        if row.state != ScheduledMessage.State.PENDING:
            return Response(
                {"detail": "That message is no longer waiting to be sent."}, status=409
            )

        when = request.data.get("scheduled_at")
        if not when:
            return Response({"detail": "Provide a new time."}, status=400)
        parsed = serializers.DateTimeField().to_internal_value(when)
        if parsed <= timezone.now():
            return Response({"detail": "Choose a time in the future."}, status=400)

        row.scheduled_at = parsed
        row.save(update_fields=["scheduled_at", "updated_at"])
        return Response({"id": str(row.id), "scheduled_at": parsed.isoformat()})

    def delete(self, request, scheduled_id):
        row = ScheduledMessage.objects.for_mailbox(self.mailbox).filter(pk=scheduled_id).first()
        if row is None:
            return Response({"detail": "Not found."}, status=404)
        if row.state not in (ScheduledMessage.State.PENDING, ScheduledMessage.State.FAILED):
            return Response(
                {"detail": "That message can no longer be cancelled."}, status=409
            )

        # Move FIRST, then mark the database row cancelled. If IMAP fails,
        # the scheduled row remains pending/failed and can still be retried or
        # cancelled again; reporting success while the message is stranded in
        # Scheduled would lose the user's only editable copy.
        try:
            with imap.open_mailbox(self.mailbox.email) as connection:
                roles = {f.role: f.name for f in connection.list_folders() if f.role}
                connection.select(row.folder)
                connection.move([row.uid], roles.get("drafts", "Drafts"))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PostBox: could not cancel scheduled %s because the message "
                "could not be returned to Drafts: %r",
                row.id,
                exc,
            )
            return Response(
                {
                    "detail": (
                        "That scheduled message could not be moved back to "
                        "Drafts, so it was not cancelled."
                    )
                },
                status=502,
            )

        row.state = ScheduledMessage.State.CANCELLED
        row.save(update_fields=["state", "updated_at"])
        return Response({"id": str(row.id), "state": row.state})
