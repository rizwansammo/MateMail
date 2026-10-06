"""
PostBox settings: preferences, signatures, contacts, rules, vacation,
identities, forwarding, quota and the mailbox password.

SCOPE
    This is a mailbox-user interface, not the organization console. It shows
    what belongs to THIS mailbox — its aliases, its own forwarding, its quota —
    and offers no organization-wide administration. Domains, other people's
    mailboxes and tenant-wide aliases live in the Workspace.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.security import ratelimit
from apps.security.limits import POSTBOX_PASSWORD_CHANGE

from . import imap, sending, sieve
from . import signatures as pb_signatures
from .auth import PostBoxSessionAuthentication, revoke_other_sessions
from .models import (
    Contact,
    MailRule,
    MailSignature,
    PostBoxPreference,
    VacationResponder,
)
from .serializers import (
    ContactSerializer,
    MailRuleSerializer,
    PreferenceSerializer,
    SignatureSerializer,
    VacationSerializer,
)
from .views_mail import PostBoxView

logger = logging.getLogger(__name__)


# ── preferences ─────────────────────────────────────────────────────────────

class PreferenceView(PostBoxView):
    team_box_permission_scope = "identity"

    def get(self, request):
        preference, _ = PostBoxPreference.objects.get_or_create(
            mailbox=request.identity_mailbox
        )
        return Response(PreferenceSerializer(preference).data)

    def patch(self, request):
        preference, _ = PostBoxPreference.objects.get_or_create(
            mailbox=request.identity_mailbox
        )
        serializer = PreferenceSerializer(preference, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        identity = serializer.validated_data.get("default_identity")
        if identity:
            if request.mailbox.pk != request.identity_mailbox.pk:
                return Response(
                    {
                        "default_identity": [
                            "Switch back to your personal mailbox to change the default sender."
                        ]
                    },
                    status=400,
                )
            sending.assert_may_send_as(
                request.identity_mailbox,
                identity,
                actor_mailbox=request.identity_mailbox,
            )

        serializer.save()
        return Response(serializer.data)


# ── a small CRUD base, scoped by mailbox ────────────────────────────────────

class MailboxScopedListView(PostBoxView):
    """
    List and create, always filtered by the session's mailbox.

    The scoping lives here rather than in each view so a new resource cannot
    be added that forgets it.
    """

    model = None
    serializer_class = None

    def queryset(self):
        return self.model.objects.for_mailbox(self.mailbox)

    def get(self, request):
        return Response({
            "results": self.serializer_class(self.queryset(), many=True).data
        })

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            instance = serializer.save(mailbox=self.mailbox)
            self.after_change(instance)
        return Response(self.serializer_class(instance).data, status=201)

    def after_change(self, instance) -> None:
        """Hook for resources that have to be pushed to the mail server."""


class MailboxScopedDetailView(PostBoxView):
    model = None
    serializer_class = None

    def instance(self, pk):
        return self.model.objects.for_mailbox(self.mailbox).filter(pk=pk).first()

    def get(self, request, pk):
        instance = self.instance(pk)
        if instance is None:
            return Response({"detail": "Not found."}, status=404)
        return Response(self.serializer_class(instance).data)

    def patch(self, request, pk):
        instance = self.instance(pk)
        if instance is None:
            return Response({"detail": "Not found."}, status=404)
        serializer = self.serializer_class(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            serializer.save()
            self.after_change(instance)
        return Response(serializer.data)

    def delete(self, request, pk):
        instance = self.instance(pk)
        if instance is None:
            return Response({"detail": "Not found."}, status=404)
        with transaction.atomic():
            instance.delete()
            self.after_change(None)
        return Response(status=204)

    def after_change(self, instance) -> None:
        """Hook for resources that have to be pushed to the mail server."""


# ── signatures ──────────────────────────────────────────────────────────────

class SignatureListView(MailboxScopedListView):
    model = MailSignature
    serializer_class = SignatureSerializer


class SignatureDetailView(MailboxScopedDetailView):
    model = MailSignature
    serializer_class = SignatureSerializer


#: What an image signature may be, by what the bytes actually START with.
#:
#: The declared content type is a client-supplied string and is not
#: evidence of anything — a caller can label an HTML file `image/png`. The
#: stored type is derived from the magic bytes, so what we later serve with
#: `Content-Type: image/png` really is a PNG.
#:
#: SVG is deliberately absent: it is a document that can carry script and
#: external references, no mail client renders it reliably, and 'an image'
#: is not a good enough reason to accept executable markup.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
)


def _sniff_image(payload: bytes) -> tuple[str, str] | None:
    """(content_type, extension) from the bytes themselves, or None."""
    for magic, content_type, extension in _IMAGE_SIGNATURES:
        if payload.startswith(magic):
            return content_type, extension

    # WebP is RIFF....WEBP — a container, so the marker is not at offset 0.
    #
    # Accepted, with a caveat recorded here rather than in a changelog:
    # Gmail and Apple Mail render inline WebP, but Outlook on Windows does
    # not, and shows a broken image instead. The settings UI says so at the
    # point of upload; PNG remains the safe default.
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


class SignatureImageView(PostBoxView):
    """
    POST an image for a signature; GET it back for the preview and the
    composer; DELETE to remove it.

    A separate endpoint rather than a base64 field on the signature JSON,
    because the bytes need checking as bytes — magic number, size, and
    dimensions — and because every signature list response would otherwise
    carry the whole image.
    """

    def _signature(self, pk):
        return MailSignature.objects.for_mailbox(self.mailbox).filter(pk=pk).first()

    def get(self, request, pk):
        """
        Serve the bytes.

        Scoped to the signed-in mailbox, so one mailbox cannot read another's
        logo by guessing a UUID. `nosniff` and an attachment-safe policy
        because this returns caller-supplied bytes on the PostBox origin:
        even having verified the magic number, the browser must not be
        allowed to reconsider the type.
        """
        signature = self._signature(pk)
        if signature is None or not signature.image_data:
            return Response({"detail": "Not found."}, status=404)

        response = HttpResponse(
            bytes(signature.image_data),
            content_type=signature.image_content_type or "application/octet-stream",
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["Cache-Control"] = "private, max-age=300"
        return response

    def post(self, request, pk):
        signature = self._signature(pk)
        if signature is None:
            return Response({"detail": "Not found."}, status=404)

        upload = request.FILES.get("image")
        if upload is None:
            return Response({"image": ["Choose an image to upload."]}, status=400)

        max_bytes, max_px = pb_signatures.image_limits()
        if upload.size > max_bytes:
            return Response(
                {"image": [f"Keep the image under {max_bytes // 1024} KB — it is sent with every message."]},
                status=400,
            )

        payload = upload.read()
        sniffed = _sniff_image(payload)
        if sniffed is None:
            return Response(
                {"image": ["That is not a PNG, JPEG, GIF or WebP image."]},
                status=400,
            )
        content_type, extension = sniffed

        dimensions = _image_size(payload)
        if dimensions and max(dimensions) > max_px:
            return Response(
                {"image": [f"Keep the image within {max_px}px on its longest side."]},
                status=400,
            )

        signature.image_data = payload
        signature.image_content_type = content_type
        signature.image_filename = f"signature.{extension}"
        signature.save(update_fields=[
            "image_data", "image_content_type", "image_filename", "updated_at",
        ])
        logger.info(
            "PostBox signature image set: mailbox=%s signature=%s type=%s bytes=%d",
            self.mailbox.pk, signature.pk, content_type, len(payload),
        )
        return Response(SignatureSerializer(signature).data)

    def delete(self, request, pk):
        signature = self._signature(pk)
        if signature is None:
            return Response({"detail": "Not found."}, status=404)
        signature.image_data = b""
        signature.image_content_type = ""
        signature.image_filename = ""
        signature.save(update_fields=[
            "image_data", "image_content_type", "image_filename", "updated_at",
        ])
        return Response(status=204)


class SignatureHtmlImageView(PostBoxView):
    """
    GET the [index]th https image of this mailbox's own HTML signature, for
    the native app's preview, which never loads remote images itself.

    Only images already in the STORED, sanitised signature can be named -
    the client sends an index, never a URL - and the fetch is SSRF-safe and
    bounded (`signature_images`). Read-only, signatures only; the webmail and
    every other endpoint are unchanged.
    """

    def get(self, request, pk, index: int):
        from . import signature_images
        from .models import SignatureKind

        signature = MailSignature.objects.for_mailbox(self.mailbox).filter(pk=pk).first()
        if signature is None or signature.kind != SignatureKind.HTML:
            return Response({"detail": "Not found."}, status=404)
        sources = signature_images.https_sources(signature.html)
        if index >= len(sources):
            return Response({"detail": "Not found."}, status=404)
        try:
            image = signature_images.cached_fetch(signature, index, sources[index])
        except signature_images.SignatureImageError as exc:
            logger.warning(
                "PostBox signature image not served: mailbox=%s signature=%s "
                "index=%d reason=%s",
                self.mailbox.pk, signature.pk, index, exc.code,
            )
            return Response({"detail": "That image couldn't be loaded."}, status=502)

        response = HttpResponse(image.data, content_type=image.content_type)
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        response["Cache-Control"] = "private, max-age=3600"
        return response


def _image_size(payload: bytes) -> tuple[int, int] | None:
    """
    (width, height) read from the header, or None if it cannot be read.

    Parsed by hand rather than with Pillow: decoding an untrusted image to
    measure it is how decompression bombs get their chance, and MateMail
    does not otherwise depend on an image library. Unknown dimensions are
    allowed through — the byte-size limit is the one that actually protects
    the mail, and refusing an image because a header is unusual would be a
    guess dressed up as a rule.
    """
    try:
        if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
            return (int.from_bytes(payload[16:20], "big"),
                    int.from_bytes(payload[20:24], "big"))

        if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP" and len(payload) >= 30:
            if payload[12:16] == b"VP8 ":
                return (int.from_bytes(payload[26:28], "little") & 0x3FFF,
                        int.from_bytes(payload[28:30], "little") & 0x3FFF)
            return None

        if payload.startswith(b"\xff\xd8"):
            i = 2
            while i + 9 < len(payload):
                if payload[i] != 0xFF:
                    i += 1
                    continue
                marker = payload[i + 1]
                # SOF0..SOF15, excluding the non-frame markers in that range.
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    return (int.from_bytes(payload[i + 7:i + 9], "big"),
                            int.from_bytes(payload[i + 5:i + 7], "big"))
                i += 2 + int.from_bytes(payload[i + 2:i + 4], "big")
    except (IndexError, ValueError):
        return None
    return None


# ── contacts ────────────────────────────────────────────────────────────────

class ContactListView(MailboxScopedListView):
    model = Contact
    serializer_class = ContactSerializer

    def queryset(self):
        rows = super().queryset()
        term = (self.request.query_params.get("q") or "").strip()
        if term:
            from django.db.models import Q

            rows = rows.filter(
                Q(name__icontains=term)
                | Q(email__icontains=term)
                | Q(company__icontains=term)
            )
        return rows[:500]


class ContactDetailView(MailboxScopedDetailView):
    model = Contact
    serializer_class = ContactSerializer


class RecipientSuggestionView(PostBoxView):
    """
    Autocomplete: saved contacts first, then people recently written to.

    Recent recipients are read from the Sent folder and are NOT silently saved
    as contacts. An address book that fills itself with every address ever
    typed becomes useless, and quietly recording who somebody mails is not a
    thing to do without being asked.
    """

    def get(self, request):
        term = (request.query_params.get("q") or "").strip().lower()
        if len(term) < 2:
            return Response({"results": []})

        from django.db.models import Q

        saved = (
            Contact.objects.for_mailbox(self.mailbox)
            .filter(Q(name__icontains=term) | Q(email__icontains=term))[:8]
        )
        results = [
            {"name": c.name, "email": c.email, "source": "contact"} for c in saved
        ]
        known = {r["email"].lower() for r in results}

        try:
            with imap.open_mailbox(self.mailbox.email) as connection:
                roles = {f.role: f.name for f in connection.list_folders() if f.role}
                connection.select(roles.get("sent", "Sent"), readonly=True)
                uids = connection.search_uids(["TO", term])[:25]
                for summary in connection.fetch_summaries(uids):
                    for address in summary.to:
                        key = address.lower()
                        if term in key and key not in known:
                            known.add(key)
                            results.append(
                                {"name": "", "email": address, "source": "recent"}
                            )
        except Exception as exc:  # noqa: BLE001 - suggestions are a convenience
            logger.info("PostBox: recipient suggestions unavailable: %r", exc)

        return Response({"results": results[:12]})


# ── rules and vacation ──────────────────────────────────────────────────────

class RuleListView(MailboxScopedListView):
    model = MailRule
    serializer_class = MailRuleSerializer

    def after_change(self, instance) -> None:
        _sync_sieve(self.mailbox)


class RuleDetailView(MailboxScopedDetailView):
    model = MailRule
    serializer_class = MailRuleSerializer

    def after_change(self, instance) -> None:
        _sync_sieve(self.mailbox)


class VacationView(PostBoxView):
    def get(self, request):
        responder, _ = VacationResponder.objects.get_or_create(mailbox=self.mailbox)
        return Response(VacationSerializer(responder).data)

    def patch(self, request):
        responder, _ = VacationResponder.objects.get_or_create(mailbox=self.mailbox)
        serializer = VacationSerializer(responder, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            serializer.save()
            _sync_sieve(self.mailbox)
        return Response(serializer.data)


def _sync_sieve(mailbox) -> None:
    """
    Push the mailbox's rules to Dovecot.

    Folder names are read live and passed in, so a rule pointing at a folder
    that no longer exists is skipped rather than compiled — `fileinto` into a
    missing mailbox is a runtime error that breaks every rule after it.

    A failure is raised, not swallowed: a filter somebody believes is active
    but which was never installed is worse than an error message.
    """
    valid_folders: set[str] | None = None
    try:
        with imap.open_mailbox(mailbox.email) as connection:
            valid_folders = {f.name for f in connection.list_folders() if f.selectable}
    except Exception as exc:  # noqa: BLE001
        logger.warning("PostBox: could not read folders before Sieve sync: %r", exc)

    sieve.sync_mailbox_rules(mailbox, valid_folders=valid_folders)


# ── identities, forwarding, quota ───────────────────────────────────────────

class IdentityListView(PostBoxView):
    """Every address this active mailbox may send as, from authoritative data."""

    def required_team_box_permission(self, request) -> str:
        return "read_or_send"

    def get(self, request):
        return Response({"results": [
            {
                "address": i.address,
                "name": i.name,
                "is_primary": i.is_primary,
                "kind": i.kind,
                "send_mode": i.send_mode,
            }
            for i in sending.allowed_identities(
                self.mailbox,
                actor_mailbox=request.identity_mailbox,
            )
        ]})


class ForwardingView(PostBoxView):
    """
    This mailbox's own forwarding, read from the authoritative model.

    Read-only in PostBox. Forwarding to an external destination is the highest
    risk configuration on the platform — it is how a compromised mailbox
    exfiltrates mail — so it stays an organization-administered setting rather
    than something a mailbox user can switch on alone. The UI says so instead
    of offering a control that would be refused.
    """

    def get(self, request):
        from apps.forwarding.models import ForwardingRule

        rules = ForwardingRule.objects.filter(
            tenant=self.mailbox.tenant, source_mailbox=self.mailbox
        )
        return Response({
            "manageable_here": False,
            "detail": (
                "Forwarding is managed by your organization's MateMail "
                "administrator."
            ),
            "results": [
                {
                    "destination": rule.destination_email,
                    "keep_copy": rule.keep_copy,
                    "status": rule.status,
                    "created_at": rule.created_at.isoformat() if rule.created_at else None,
                }
                for rule in rules
            ],
        })


class MailboxAccountView(PostBoxView):
    team_box_permission_scope = "identity"
    """
    Mailbox and account: quota, identities and the real connection settings.

    Connection details come from MateMail's own configuration — the same
    `MAIL_HOSTNAME` the rest of the platform uses — and only the protocols
    production actually offers are listed. The prototype advertised a
    `mail.matemail.online` that does not serve clients and a POP3 service that
    does not exist; printing settings that do not work is worse than printing
    none.
    """

    def get(self, request):
        mailbox = request.identity_mailbox
        usage_mb = None
        quota_mb = mailbox.quota_mb

        # Authoritative usage from the engine, not the stored column, which is
        # only as fresh as the last time something wrote it.
        try:
            from apps.mail_engine.factory import get_adapter

            usage = get_adapter().get_mailbox_usage(mailbox.email)
            if usage is not None:
                usage_mb = getattr(usage, "used_mb", None)
                quota_mb = getattr(usage, "quota_mb", None) or quota_mb
        except Exception as exc:  # noqa: BLE001
            logger.info("PostBox: live quota unavailable for %s: %r", mailbox.pk, exc)

        host = getattr(settings, "MAIL_HOSTNAME", "")

        return Response({
            "email": mailbox.email,
            "full_name": mailbox.full_name,
            "organization": mailbox.tenant.name if mailbox.tenant else "",
            "storage": {
                # None, not 0. "Unknown" and "empty" are different facts, and a
                # bar showing 0% for an unknown value is a fabrication.
                "used_mb": usage_mb,
                "quota_mb": quota_mb,
                "percent": (
                    round((usage_mb / quota_mb) * 100, 1)
                    if usage_mb is not None and quota_mb else None
                ),
                "available": usage_mb is not None,
            },
            "identities": [
                {"address": i.address, "is_primary": i.is_primary, "kind": i.kind}
                for i in sending.allowed_identities(
                    mailbox,
                    actor_mailbox=mailbox,
                )
            ],
            "connection": {
                "imap": {"host": host, "port": 993, "security": "SSL/TLS"},
                "smtp": {"host": host, "port": 587, "security": "STARTTLS"},
                "username": mailbox.email,
                "note": "Your username is always your full email address.",
            },
        })


# ── password ────────────────────────────────────────────────────────────────

class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(trim_whitespace=False)


class PasswordChangeView(PostBoxView):
    """
    Change the mailbox password.

    The current password is verified against Dovecot, the new one is applied
    through the Mail Engine adapter's `set_mailbox_password`, and neither is
    stored or logged. The mailbox is NOT reprovisioned: deleting and recreating
    it to change a password would destroy the mail.

    Other sessions are revoked afterwards. Changing a password is what somebody
    does when they think it is known, and leaving other sessions alive would
    make the act pointless.
    """

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        decision = ratelimit.hit(
            POSTBOX_PASSWORD_CHANGE.bucket, str(self.mailbox.pk),
            limit=POSTBOX_PASSWORD_CHANGE.limit, window=POSTBOX_PASSWORD_CHANGE.window,
        )
        if not decision.allowed:
            from rest_framework.exceptions import Throttled

            raise Throttled(wait=decision.retry_after, detail="Too many attempts.")

        if not imap.authenticate(self.mailbox.email, data["current_password"]):
            return Response(
                {"current_password": ["That is not your current password."]}, status=400
            )

        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_password(data["new_password"])
        except DjangoValidationError as exc:
            return Response({"new_password": list(exc.messages)}, status=400)

        from apps.mail_engine.errors import MailEngineError
        from apps.mail_engine.factory import get_adapter

        try:
            get_adapter().set_mailbox_password(self.mailbox.email, data["new_password"])
        except MailEngineError as exc:
            logger.error(
                "PostBox password change failed for %s: %s", self.mailbox.pk, exc.log_message
            )
            return Response({"detail": exc.customer_message}, status=502)

        with transaction.atomic():
            revoked = revoke_other_sessions(self.mailbox, keep=request.postbox_session)

        logger.info(
            "PostBox password changed for mailbox %s; %d other session(s) revoked",
            self.mailbox.pk, revoked,
        )
        return Response({
            "detail": "Your password has been changed.",
            "other_sessions_revoked": revoked,
        })
