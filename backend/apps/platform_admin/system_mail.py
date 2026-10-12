"""Platform system mail proposals, independent of tenant mail management.

No code in this module talks to SMTP, IMAP, DNS or the Native Engine.
Operational sender/report receiver configuration stays on the existing
environment values until a separately tested Phase 5 activation path exists.
"""
from __future__ import annotations

from collections.abc import Mapping

from django.conf import settings
from django.db import transaction
from rest_framework import serializers

from apps.domains.models import MailDomainOwnerKind, MailDomainOwnership

from .audit import record_platform_action
from .models import PlatformSystemMailPointer, PlatformSystemMailRevision


SYSTEM_MAIL_DOMAIN = "system.matemail.pro"
ROLE_LOCAL_PARTS = {
    "noreply_address": "noreply",
    "security_address": "security",
    "operations_address": "operations",
    "dmarc_address": "dmarc",
    "tlsrpt_address": "tlsrpt",
}
EDITABLE_FIELDS = ("domain", "sender_name", *ROLE_LOCAL_PARTS)
DEFAULT_PROPOSAL = {
    "domain": SYSTEM_MAIL_DOMAIN,
    "sender_name": "MateMail",
    **{key: f"{local}@{SYSTEM_MAIL_DOMAIN}" for key, local in ROLE_LOCAL_PARTS.items()},
}


class SystemMailProposalSerializer(serializers.Serializer):
    """Whole-revision updates; no arbitrary hostnames, flags or secrets."""

    expected_revision = serializers.IntegerField(min_value=0, max_value=2147483647)
    domain = serializers.CharField(max_length=255, trim_whitespace=True)
    sender_name = serializers.CharField(max_length=80, trim_whitespace=True)
    noreply_address = serializers.EmailField(max_length=254)
    security_address = serializers.EmailField(max_length=254)
    operations_address = serializers.EmailField(max_length=254)
    dmarc_address = serializers.EmailField(max_length=254)
    tlsrpt_address = serializers.EmailField(max_length=254)

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError("A JSON configuration object is required.")
        unexpected = set(data) - set(self.fields)
        if unexpected:
            raise serializers.ValidationError({key: "Not a supported configuration field." for key in unexpected})
        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs["domain"] = attrs["domain"].lower().rstrip(".")
        if attrs["domain"] != SYSTEM_MAIL_DOMAIN:
            raise serializers.ValidationError({
                "domain": "The staged system domain must be system.matemail.pro."
            })
        if "\n" in attrs["sender_name"] or "\r" in attrs["sender_name"]:
            raise serializers.ValidationError({
                "sender_name": "Header line breaks are not allowed."
            })
        for field, local in ROLE_LOCAL_PARTS.items():
            canonical = f"{local}@{SYSTEM_MAIL_DOMAIN}"
            if attrs[field].strip().lower() != canonical:
                raise serializers.ValidationError({
                    field: f"This role requires {canonical}."
                })
            attrs[field] = canonical
        return attrs


def serialize_revision(row):
    if row is None:
        return None
    return {
        "id": str(row.pk),
        "revision": row.revision,
        **{field: getattr(row, field) for field in EDITABLE_FIELDS},
        "created_at": row.created_at.isoformat(),
        "created_by": str(row.created_by_id) if row.created_by_id else None,
        "state": "draft",
    }


def operational_configuration():
    """Read-only factual baseline; never infer a deployed domain from a draft."""
    return {
        "sender": getattr(settings, "DEFAULT_FROM_EMAIL", ""),
        "dmarc_receiver": getattr(settings, "DMARC_REPORT_ADDRESS", ""),
        "tlsrpt_receiver": getattr(settings, "TLS_RPT_REPORT_ADDRESS", ""),
        "source": "environment",
        "draft_applied": False,
    }


def proposed_configuration():
    pointer = PlatformSystemMailPointer.objects.select_related("draft").filter(pk=1).first()
    return {
        "revision": pointer.revision if pointer else 0,
        "draft": serialize_revision(pointer.draft) if pointer else None,
        "defaults": dict(DEFAULT_PROPOSAL),
        "operational": operational_configuration(),
        "activation_supported": False,
        "mailboxes_provisioned": False,
    }


class RevisionConflict(Exception):
    """A concurrent admin saved a new version: require a fresh GET."""


class DomainNotReserved(Exception):
    """Only Platform owned names can be staged for later migration."""


def save_proposed_configuration(*, actor, request, payload):
    with transaction.atomic():
        pointer, _ = PlatformSystemMailPointer.objects.get_or_create(pk=1)
        pointer = PlatformSystemMailPointer.objects.select_for_update().get(pk=1)
        if payload["expected_revision"] != pointer.revision:
            raise RevisionConflict()
        owner = MailDomainOwnership.objects.filter(domain=SYSTEM_MAIL_DOMAIN).first()
        if owner is None or owner.owner_kind != MailDomainOwnerKind.PLATFORM:
            raise DomainNotReserved()
        next_revision = pointer.revision + 1
        row = PlatformSystemMailRevision.objects.create(
            revision=next_revision,
            created_by=actor,
            **{field: payload[field] for field in EDITABLE_FIELDS},
        )
        pointer.draft = row
        pointer.revision = next_revision
        pointer.save(update_fields=["draft", "revision"])
        record_platform_action(
            actor=actor, request=request,
            action="system_mail.configuration_draft",
            target_label=SYSTEM_MAIL_DOMAIN,
            metadata={"revision": next_revision, "state": "draft"},
        )
    return row
