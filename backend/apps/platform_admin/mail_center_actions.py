"""Release 4 Phase 6C: gated Operations sending and IMAP drafts.

Fixed platform system identity only. Never use a customer mailbox as a
sender, and never authenticate through tenant PostBox sessions.
"""
from __future__ import annotations

import logging
import re
from types import SimpleNamespace

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers
from rest_framework.response import Response

from apps.postbox import imap, mime, sending
from apps.security import ratelimit
from .audit import record_platform_action
from .mail_center_views import (
    OPERATIONS_MAILBOX, PlatformMailCenterBase,
    _read_fixed_operations_message,
)

log = logging.getLogger(__name__)
MAX_RECIPIENTS = 10
KEY_FORMAT = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("A JSON object is required.")
        unexpected = set(value) - set(self.fields)
        if unexpected:
            raise serializers.ValidationError({
                key: "Unsupported field." for key in unexpected
            })
        return super().to_internal_value(value)


class DraftReference(StrictSerializer):
    uid = serializers.IntegerField(min_value=1, max_value=4294967295)
    uid_validity = serializers.IntegerField(min_value=1, max_value=4294967295)


class ReplyReference(DraftReference):
    folder = serializers.CharField(max_length=255)
    mode = serializers.ChoiceField(choices=("reply", "reply_all"))


class ComposePayload(StrictSerializer):
    to = serializers.ListField(child=serializers.EmailField(), default=list)
    cc = serializers.ListField(child=serializers.EmailField(), default=list)
    bcc = serializers.ListField(child=serializers.EmailField(), default=list)
    subject = serializers.CharField(max_length=300, allow_blank=True, default="", trim_whitespace=False)
    text = serializers.CharField(max_length=50000, allow_blank=True, default="", trim_whitespace=False)
    draft = DraftReference(required=False)
    reply = ReplyReference(required=False)

    def validate(self, data):
        addresses = data["to"] + data["cc"] + data["bcc"]
        if len(addresses) > MAX_RECIPIENTS:
            raise serializers.ValidationError({"to": "Too many recipients."})
        if len({value.lower() for value in addresses}) != len(addresses):
            raise serializers.ValidationError({"to": "Duplicate recipients."})
        if data.get("reply") and (addresses or data.get("draft")):
            raise serializers.ValidationError({
                "reply": "Reply recipients must come from the original message."
            })
        return data


class SendPayload(ComposePayload):
    idempotency_key = serializers.CharField(min_length=16, max_length=80)

    def validate_idempotency_key(self, value):
        if not KEY_FORMAT.fullmatch(value):
            raise serializers.ValidationError("Invalid idempotency key.")
        return value


def outbound_gate():
    if not getattr(settings, "PLATFORM_MAIL_CENTER_SEND_ENABLED", False):
        return Response({"detail": "Platform Mail Center sending is not enabled."}, status=503)
    if not getattr(settings, "EMAIL_HOST", "") or not getattr(settings, "POSTBOX_MASTER_PASSWORD", ""):
        return Response({"detail": "Platform Mail Center sending is unavailable."}, status=503)
    return None


def audit(request, action, **metadata):
    record_platform_action(
        actor=request.user, request=request, action=action,
        target_label=OPERATIONS_MAILBOX, metadata=metadata,
    )


def draft_folder(connection):
    return next(
        (row.name for row in connection.list_folders()
         if row.selectable and row.role == "drafts"), None,
    )


def validate_draft(connection, reference):
    folder = draft_folder(connection)
    if folder is None:
        return None, Response({"detail": "Drafts folder unavailable."}, status=409)
    selected = connection.select(folder, readonly=True)
    if selected.uid_validity <= 0 or selected.uid_validity != reference["uid_validity"]:
        return None, Response({"detail": "Drafts folder changed. Refresh first."}, status=409)
    found = connection.fetch_summaries([reference["uid"]])
    if not found or found[0].uid != reference["uid"]:
        return None, Response({"detail": "Draft not found."}, status=404)
    if not found[0].draft:
        return None, Response({"detail": "Message is not a draft."}, status=409)
    return folder, None


def submit_operations(request, data, parsed=None):
    recipients = sending.envelope_recipients(data["to"], data["cc"], data["bcc"])
    if not recipients or len(recipients) > MAX_RECIPIENTS:
        return Response({"detail": "Choose between 1 and 10 valid recipients."}, status=400)
    for bucket, identity, limit in (
        ("platform:operations:send:admin", request.user.pk, 10),
        ("platform:operations:send:identity", OPERATIONS_MAILBOX, 30),
    ):
        result = ratelimit.hit(bucket, identity, limit=limit, window=3600)
        if not result.allowed:
            return Response({"detail": "Operations send rate limit reached."}, status=429)
    if not ratelimit.claim_once(
        "platform:operations:send",
        str(request.user.pk) + ":" + data["idempotency_key"],
        ttl=86400,
    ):
        return Response({
            "detail": "This send request was already attempted. Check Sent before retrying."
        }, status=409)

    # An audit outage must stop any new SMTP side effect.
    audit(request, "mail_center.send_attempt", recipient_count=len(recipients))
    message = mime.build_message(
        from_address=OPERATIONS_MAILBOX,
        to=data["to"], cc=data["cc"], bcc=data["bcc"],
        subject=data["subject"], text=data["text"],
        in_reply_to=parsed.message_id if parsed else "",
        references=parsed.references if parsed else [],
    )
    try:
        sending.submit(
            message,
            mailbox=SimpleNamespace(email=OPERATIONS_MAILBOX),
            envelope_from=OPERATIONS_MAILBOX,
            recipients=recipients,
        )
    except sending.SendFailed:
        log.warning("Platform Operations SMTP submission was not confirmed.")
        return Response({
            "detail": "Message not confirmed sent. Check status before trying a new key."
        }, status=503)

    # SMTP success must be reported as success even if filing or audit fails.
    filed = False
    try:
        with imap.open_mailbox(OPERATIONS_MAILBOX) as connection:
            folder = next(
                (f.name for f in connection.list_folders()
                 if f.selectable and f.role == "sent"), None,
            )
            if folder:
                connection.append(folder, message.as_bytes(), flags="\\Seen")
                filed = True
    except Exception:
        log.warning("Operations SMTP accepted message but Sent copy failed.")
    try:
        audit(request, "mail_center.sent", recipient_count=len(recipients),
              filed_in_sent=filed)
    except Exception:
        log.error("Operations message sent but completion audit failed.")
    return Response({
        "sent": True, "from_address": OPERATIONS_MAILBOX,
        "message_id": message["Message-ID"], "filed_in_sent": filed,
    })


class PlatformOperationsSendView(PlatformMailCenterBase):
    http_method_names = ["post", "options"]

    def post(self, request):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        blocked = outbound_gate()
        if blocked is not None:
            return blocked
        serializer = SendPayload(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        source = data.get("reply")
        original = None
        if source:
            try:
                raw, chosen, _, error = _read_fixed_operations_message(
                    source["folder"], source["uid_validity"], source["uid"],
                )
                if error is not None:
                    return error
                if chosen.role not in ("inbox", "sent"):
                    return Response({"detail": "Reply source must be Inbox or Sent."}, status=400)
                original = mime.parse_message(raw, load_remote_images=False)
            except Exception:
                log.warning("Operations reply source unavailable.")
                return self.unavailable()
            to, cc = mime.reply_recipients(
                original, {OPERATIONS_MAILBOX},
                reply_all=source["mode"] == "reply_all",
            )
            if not to or len(to) + len(cc) > MAX_RECIPIENTS:
                return Response({"detail": "Original message has no valid reply recipients."}, status=400)
            try:
                for address in (*to, *cc):
                    serializers.EmailField().run_validation(address)
            except serializers.ValidationError:
                return Response({"detail": "Original message has invalid reply recipients."}, status=400)
            data["to"], data["cc"], data["bcc"] = to, cc, []
            if not data["subject"]:
                subject = original.subject
                data["subject"] = (
                    subject if subject.lower().startswith("re:")
                    else "Re: " + subject
                )[:300]

        prior = data.get("draft")
        if prior:
            try:
                with imap.open_mailbox(OPERATIONS_MAILBOX) as connection:
                    _, error = validate_draft(connection, prior)
                if error is not None:
                    return error
            except Exception:
                log.warning("Operations send draft reference validation failed.")
                return self.unavailable()
        result = submit_operations(request, data, parsed=original)
        if result.status_code == 200 and prior:
            try:
                with imap.open_mailbox(OPERATIONS_MAILBOX) as connection:
                    folder, error = validate_draft(connection, prior)
                    if error is None:
                        selected = connection.select(folder, readonly=False)
                        if selected.uid_validity == prior["uid_validity"]:
                            connection.delete_permanently([prior["uid"]])
            except Exception:
                log.warning("Operations message sent but original draft was retained.")
        return result


class PlatformOperationsDraftSaveView(PlatformMailCenterBase):
    http_method_names = ["post", "options"]

    def post(self, request):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        blocked = outbound_gate()
        if blocked is not None:
            return blocked
        serializer = ComposePayload(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get("reply"):
            return Response({"detail": "Reply source cannot be saved as a draft."}, status=400)
        raw = mime.build_message(
            from_address=OPERATIONS_MAILBOX,
            to=data["to"], cc=data["cc"], bcc=data["bcc"],
            subject=data["subject"], text=data["text"], keep_bcc=True,
        ).as_bytes()
        if len(raw) > 256 * 1024:
            return Response({"detail": "Draft exceeds size limit."}, status=413)
        previous = data.get("draft")
        audit(request, "mail_center.draft_save_attempt", replacement=bool(previous))
        try:
            with imap.open_mailbox(OPERATIONS_MAILBOX) as connection:
                if previous:
                    folder, error = validate_draft(connection, previous)
                    if error is not None:
                        return error
                else:
                    folder = draft_folder(connection)
                    if folder is None:
                        return Response({"detail": "Drafts folder unavailable."}, status=409)
                uid_validity, uid = connection.append(
                    folder, raw, flags="\\Draft \\Seen",
                )
                removed = False
                if previous:
                    selected = connection.select(folder, readonly=False)
                    if selected.uid_validity == previous["uid_validity"]:
                        connection.delete_permanently([previous["uid"]])
                        removed = True
        except Exception:
            log.warning("Operations draft could not be saved.")
            return self.unavailable()
        try:
            audit(request, "mail_center.draft_saved", replacement=bool(previous))
        except Exception:
            log.warning("Operations draft saved but completion audit failed.")
        return Response({
            "saved": True, "folder": folder,
            "uid": uid, "uid_validity": uid_validity,
            "previous_removed": removed,
            "saved_at": timezone.now().isoformat(),
        }, status=200 if previous else 201)


class PlatformOperationsDraftDeleteView(PlatformMailCenterBase):
    http_method_names = ["delete", "options"]

    def delete(self, request, uid: int):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        blocked = outbound_gate()
        if blocked is not None:
            return blocked
        if set(request.query_params) != {"uid_validity"} or len(request.query_params.getlist("uid_validity")) != 1:
            return Response({"detail": "UIDVALIDITY is required."}, status=400)
        serializer = DraftReference(data={
            "uid": uid, "uid_validity": request.query_params.get("uid_validity"),
        })
        serializer.is_valid(raise_exception=True)
        reference = serializer.validated_data
        audit(request, "mail_center.draft_delete_attempt")
        try:
            with imap.open_mailbox(OPERATIONS_MAILBOX) as connection:
                folder, error = validate_draft(connection, reference)
                if error is not None:
                    return error
                selected = connection.select(folder, readonly=False)
                if selected.uid_validity != reference["uid_validity"]:
                    return Response({"detail": "Drafts folder changed. Refresh first."}, status=409)
                connection.delete_permanently([uid])
        except Exception:
            log.warning("Operations draft deletion failed.")
            return self.unavailable()
        try:
            audit(request, "mail_center.draft_deleted")
        except Exception:
            log.warning("Operations draft deleted but completion audit failed.")
        return Response(status=204)
