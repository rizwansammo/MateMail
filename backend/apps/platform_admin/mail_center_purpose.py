"""Phase 6D: explicit, OTP-bound purpose mailbox authorization.

No user-selected mailbox email, no implicit platform admin grant, no send
capability, no read flags, and no customer mailbox session delegation.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header
from rest_framework import serializers
from rest_framework.response import Response

from apps.postbox import imap, mime

from .audit import record_platform_action
from .mail_center_views import (
    PlatformMailCenterBase, MAX_FOLDERS, MAX_PAGE, MAX_PAGE_SIZE,
    MAX_RAW_MESSAGE_BYTES, MAX_BODY_CHARS, MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS, _validated_reference,
)
from .models import PlatformSystemMailboxGrant

log = logging.getLogger(__name__)

PURPOSE_ADDRESSES = {
    "security": "security@system.matemail.pro",
    "noreply": "noreply@system.matemail.pro",
}
OTP_PROOF_SECONDS = 15 * 60


def recent_platform_otp(request):
    """Only tokens minted after the Platform Console email OTP verification."""
    token = getattr(request, "auth", None)
    if not hasattr(token, "get"):
        return False
    proof = token.get("platform_otp_verified_at")
    if type(proof) is not int:
        return False
    elapsed = int(time.time()) - proof
    return -30 <= elapsed <= OTP_PROOF_SECONDS


def _audit(request, action, role, *, target_user=None, **metadata):
    return record_platform_action(
        request=request, actor=request.user, action=action,
        target_label=PURPOSE_ADDRESSES[role],
        target_user=target_user,
        metadata={"role": role, **metadata},
    )


class StrictGrantInput(serializers.Serializer):
    grantee_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=("security", "noreply"))
    duration_hours = serializers.IntegerField(min_value=1, max_value=168)

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("A JSON object is required.")
        extra = set(data) - set(self.fields)
        if extra:
            raise serializers.ValidationError({
                key: "Unsupported field." for key in extra
            })
        return super().to_internal_value(data)


class PurposeBase(PlatformMailCenterBase):
    """Every request needs a recent platform OTP, a grant and both feature flags."""

    def gate_sensitive(self, request, role):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        if not getattr(settings, "PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED", False):
            return Response({"detail": "Purpose mailbox access is not enabled."}, status=503)
        if role not in PURPOSE_ADDRESSES:
            return Response({"detail": "Unknown mailbox role."}, status=404)
        if not recent_platform_otp(request):
            return Response({"detail": "A recent Platform Console verification is required."}, status=403)
        now = timezone.now()
        if not PlatformSystemMailboxGrant.objects.filter(
            grantee=request.user, role=role, expires_at__gt=now,
            revoked_at__isnull=True,
        ).exists():
            return Response({"detail": "You do not have access to this system mailbox."}, status=403)
        return None


class PlatformPurposeGrantListView(PurposeBase):
    """Show a platform administrator their own unexpired purpose read grants."""

    def get(self, request):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        if not getattr(settings, "PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED", False):
            return Response({"detail": "Purpose mailbox access is not enabled."}, status=503)
        if not recent_platform_otp(request):
            return Response({"detail": "A recent Platform Console verification is required."}, status=403)
        if request.query_params:
            return Response({"detail": "Unsupported grant filter."}, status=400)
        rows = list(
            PlatformSystemMailboxGrant.objects.filter(
                grantee=request.user, revoked_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).values("role", "expires_at").order_by("role")
        )
        record_platform_action(
            actor=request.user, request=request, action="mail_center.grants_read",
            metadata={"count": len(rows)},
        )
        return Response({"permissions": [
            {"role": row["role"], "access": "read", "expires_at": row["expires_at"]}
            for row in rows
        ]})


class PlatformPurposeGrantCreateView(PurposeBase):
    http_method_names = ["post", "options"]

    def post(self, request):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        if not getattr(settings, "PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED", False):
            return Response({"detail": "Purpose mailbox access is not enabled."}, status=503)
        if not request.user.is_superuser or not recent_platform_otp(request):
            return Response({"detail": "A verified platform superuser is required."}, status=403)
        payload = StrictGrantInput(data=request.data)
        payload.is_valid(raise_exception=True)
        info = payload.validated_data
        user = get_user_model().objects.filter(
            pk=info["grantee_id"], is_active=True,
            is_platform_admin=True,
        ).first()
        if user is None or user.pk == request.user.pk:
            return Response({"detail": "An eligible second administrator is required."}, status=403)
        role = info["role"]
        now = timezone.now()
        with transaction.atomic():
            current = PlatformSystemMailboxGrant.objects.select_for_update().filter(
                grantee=user, role=role,
            ).first()
            if current and current.revoked_at is None and current.expires_at > now:
                return Response({"detail": "An active grant already exists."}, status=409)
            # Audit is in the same DB transaction and must not silently fail.
            _audit(request, "mail_center.grant_approved", role,
                   target_user=user, duration_hours=info["duration_hours"])
            expires = now + timedelta(hours=info["duration_hours"])
            if current:
                current.expires_at = expires
                current.revoked_at = None
                current.granted_by = request.user
                current.save(update_fields=("expires_at", "revoked_at", "granted_by"))
            else:
                current = PlatformSystemMailboxGrant.objects.create(
                    grantee=user, role=role, expires_at=expires,
                    granted_by=request.user,
                )
        return Response({
            "grant_id": current.pk, "role": role,
            "grantee_id": user.pk, "expires_at": current.expires_at,
            "access": "read",
        }, status=201)


class PlatformPurposeGrantRevokeView(PurposeBase):
    http_method_names = ["delete", "options"]

    def delete(self, request, grant_id):
        blocked = self.gate()
        if blocked is not None:
            return blocked
        if not getattr(settings, "PLATFORM_MAIL_CENTER_PURPOSE_READ_ENABLED", False):
            return Response({"detail": "Purpose mailbox access is not enabled."}, status=503)
        if not request.user.is_superuser or not recent_platform_otp(request):
            return Response({"detail": "A verified platform superuser is required."}, status=403)
        if request.query_params:
            return Response({"detail": "Unsupported grant filter."}, status=400)
        with transaction.atomic():
            current = PlatformSystemMailboxGrant.objects.select_for_update().filter(
                pk=grant_id, revoked_at__isnull=True,
            ).first()
            if current is None:
                return Response({"detail": "Active grant not found."}, status=404)
            _audit(request, "mail_center.grant_revoked", current.role,
                   target_user=current.grantee)
            current.revoked_at = timezone.now()
            current.save(update_fields=("revoked_at",))
        return Response(status=204)


def _read_purpose_message(address, folder, validity, uid):
    """Read a single bounded RFC822 payload from a literal server role address."""
    with imap.open_mailbox(address) as connection:
        chosen = next((
            f for f in connection.list_folders()
            if f.selectable and f.name == folder
        ), None)
        if chosen is None:
            return None, None, None, Response({"detail": "Folder not found."}, status=404)
        selected = connection.select(folder, readonly=True)
        if selected.uid_validity <= 0:
            return None, None, None, Response({"detail": "Mailbox is unavailable."}, status=503)
        if selected.uid_validity != validity:
            return None, None, None, Response({
                "detail": "This folder has changed. Refresh your message list."
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


class PlatformPurposeFoldersView(PurposeBase):
    def get(self, request, role):
        denied = self.gate_sensitive(request, role)
        if denied is not None:
            return denied
        if request.query_params:
            return Response({"detail": "No folder filters are supported."}, status=400)
        address = PURPOSE_ADDRESSES[role]
        try:
            with imap.open_mailbox(address) as connection:
                available = [f for f in connection.list_folders() if f.selectable]
                rows = []
                for folder in available[:MAX_FOLDERS]:
                    total, unseen = connection.folder_counts(folder.name)
                    rows.append({
                        "name": folder.name, "role": folder.role,
                        "messages": total, "unseen": unseen,
                    })
        except Exception:
            log.warning("Purpose mailbox folders could not be read.")
            return self.unavailable()
        _audit(request, "mail_center.purpose_folders_read", role,
               folder_count=len(rows))
        return Response({
            "mailbox_role": role, "access": "read",
            "results": rows, "has_more": len(available) > MAX_FOLDERS,
        })


class PlatformPurposeMessagesView(PurposeBase):
    def get(self, request, role):
        denied = self.gate_sensitive(request, role)
        if denied is not None:
            return denied
        if set(request.query_params) - {"folder", "page", "page_size"}:
            return Response({"detail": "Unsupported message filter."}, status=400)
        if any(len(request.query_params.getlist(k)) != 1 for k in request.query_params):
            return Response({"detail": "Duplicate message selector."}, status=400)
        folder = request.query_params.get("folder", "INBOX")
        if not folder or len(folder) > 255:
            return Response({"detail": "Invalid folder."}, status=400)
        try:
            page = int(request.query_params.get("page", 1))
            size = int(request.query_params.get("page_size", 25))
        except (TypeError, ValueError):
            return Response({"detail": "Invalid pagination."}, status=400)
        if not (1 <= page <= MAX_PAGE and 1 <= size <= MAX_PAGE_SIZE):
            return Response({"detail": "Pagination is out of range."}, status=400)
        try:
            with imap.open_mailbox(PURPOSE_ADDRESSES[role]) as connection:
                chosen = next((
                    f for f in connection.list_folders()
                    if f.selectable and f.name == folder
                ), None)
                if chosen is None:
                    return Response({"detail": "Folder not found."}, status=404)
                selected = connection.select(folder, readonly=True)
                if selected.uid_validity <= 0:
                    return self.unavailable()
                uids = connection.search_uids(["ALL"], newest=True)
                start = (page - 1) * size
                summaries = connection.fetch_summaries(uids[start:start + size])
                rows = [{
                    "uid": m.uid, "subject": m.subject,
                    "from_name": m.from_name, "from_address": m.from_address,
                    "date": m.date, "seen": m.seen,
                    "has_attachments": m.has_attachments, "size": m.size,
                } for m in summaries]
        except Exception:
            log.warning("Purpose mailbox headers could not be read.")
            return self.unavailable()
        _audit(request, "mail_center.purpose_messages_read", role,
               folder_role=chosen.role or "custom", count=len(rows))
        return Response({
            "mailbox_role": role, "folder": folder,
            "uid_validity": selected.uid_validity,
            "page": page, "page_size": size,
            "total": len(uids), "has_next": start + size < len(uids),
            "results": rows,
        })


class PlatformPurposeMessageDetailView(PurposeBase):
    def get(self, request, role, uid):
        denied = self.gate_sensitive(request, role)
        if denied is not None:
            return denied
        ref, error = _validated_reference(request, uid)
        if error is not None:
            return error
        try:
            raw, chosen, selected, error = _read_purpose_message(
                PURPOSE_ADDRESSES[role], ref[0], ref[1], uid,
            )
            if error is not None:
                return error
            parsed = mime.parse_message(raw, load_remote_images=False)
            if len(parsed.attachments) > MAX_ATTACHMENTS:
                return Response({"detail": "Too many message attachments."}, status=413)
            if len(parsed.text) > MAX_BODY_CHARS or len(parsed.html) > MAX_BODY_CHARS:
                return Response({"detail": "Message content exceeds the safe reading limit."}, status=413)
        except Exception:
            log.warning("Purpose mailbox message could not be read.")
            return self.unavailable()
        _audit(request, "mail_center.purpose_message_read", role,
               folder_role=chosen.role or "custom")
        return Response({
            "mailbox_role": role, "folder": ref[0],
            "uid": uid, "uid_validity": selected.uid_validity,
            "subject": parsed.subject,
            "from": {"name": parsed.from_name, "address": parsed.from_address},
            "to": parsed.to, "cc": parsed.cc,
            "reply_to": parsed.reply_to, "date": parsed.date,
            "message_id": parsed.message_id,
            "text": parsed.text, "html": parsed.html,
            "remote_images_blocked": parsed.has_remote_images,
            "attachments": [{
                "part_id": a.part_id, "filename": a.filename,
                "content_type": a.content_type, "size": a.size,
                "inline": a.inline,
                "downloadable": a.size <= MAX_ATTACHMENT_BYTES,
            } for a in parsed.attachments],
        })


class PlatformPurposeAttachmentView(PurposeBase):
    def get(self, request, role, uid, part_id):
        denied = self.gate_sensitive(request, role)
        if denied is not None:
            return denied
        ref, error = _validated_reference(request, uid)
        if error is not None:
            return error
        if part_id > 255:
            return Response({"detail": "Invalid attachment reference."}, status=400)
        try:
            raw, chosen, _, error = _read_purpose_message(
                PURPOSE_ADDRESSES[role], ref[0], ref[1], uid,
            )
            if error is not None:
                return error
            parsed = mime.parse_message(raw, load_remote_images=False)
            if len(parsed.attachments) > MAX_ATTACHMENTS:
                return Response({"detail": "Too many message attachments."}, status=413)
            attachment = next((
                a for a in parsed.attachments if a.part_id == str(part_id)
            ), None)
            if attachment is None:
                return Response({"detail": "Attachment not found."}, status=404)
            if attachment.size > MAX_ATTACHMENT_BYTES:
                return Response({"detail": "Attachment exceeds the download limit."}, status=413)
            filename, _, payload = mime.extract_attachment(raw, str(part_id))
            if len(payload) > MAX_ATTACHMENT_BYTES:
                return Response({"detail": "Attachment exceeds the download limit."}, status=413)
        except Exception:
            log.warning("Purpose mailbox attachment could not be read.")
            return self.unavailable()
        _audit(request, "mail_center.purpose_attachment", role,
               folder_role=chosen.role or "custom", bytes=len(payload))
        result = HttpResponse(payload, content_type="application/octet-stream")
        result["Content-Disposition"] = content_disposition_header(True, filename)
        result["Content-Length"] = str(len(payload))
        result["Content-Security-Policy"] = "sandbox; default-src 'none'"
        result["X-Content-Type-Options"] = "nosniff"
        result["Referrer-Policy"] = "no-referrer"
        return result
