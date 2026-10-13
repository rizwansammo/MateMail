"""Platform Admin controlled inactive Native Engine staging, default disabled.

The operation is safe to retry and never accepts a caller-defined mailbox,
password, domain, DKIM selector or active flag. HTTP outcomes that may have
occurred upstream are recorded as UNKNOWN rather than falsely saying no change.
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings
from rest_framework import serializers

from apps.domains.models import MailDomainOwnerKind, MailDomainOwnership

from .audit import record_platform_action
from .models import PlatformSystemMailPointer
from .system_mail import SYSTEM_MAIL_DOMAIN

logger = logging.getLogger(__name__)


class SystemMailStageSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=1)
    confirmation = serializers.ChoiceField(choices=["stage_inactive"])

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({"non_field_errors": ["A JSON object is required."]})
        unexpected = set(data) - set(self.fields)
        if unexpected:
            raise serializers.ValidationError({k: "Unsupported field." for k in unexpected})
        return super().to_internal_value(data)


class StageUnavailable(Exception):
    pass


class StageConflict(Exception):
    pass


def staging_prerequisites(revision: int) -> None:
    if not getattr(settings, "SYSTEM_MAIL_STAGING_ENABLED", False):
        raise StageUnavailable("System mail staging is disabled.")
    secret = getattr(settings, "NATIVE_ENGINE_PLATFORM_SECRET", "")
    regular = getattr(settings, "NATIVE_ENGINE_API_SECRET", "")
    base = getattr(settings, "NATIVE_ENGINE_API_URL", "")
    if (
        len(secret) < 32 or not regular or secret == regular or
        not base or not base.startswith(("http://", "https://"))
    ):
        raise StageUnavailable("Dedicated Native Engine staging is not configured.")
    pointer = PlatformSystemMailPointer.objects.select_related("draft").filter(pk=1).first()
    if not pointer or not pointer.draft or pointer.revision != revision:
        raise StageConflict("The configuration revision is missing or outdated.")
    if pointer.draft.domain != SYSTEM_MAIL_DOMAIN:
        raise StageConflict("The draft domain does not match the system domain.")
    if not MailDomainOwnership.objects.filter(
        domain=SYSTEM_MAIL_DOMAIN, owner_kind=MailDomainOwnerKind.PLATFORM,
        tenant__isnull=True, source_domain_id__isnull=True,
    ).exists():
        raise StageConflict("The platform ownership reservation is missing.")


def stage_system_mail(*, actor, request, expected_revision: int) -> dict:
    staging_prerequisites(expected_revision)
    requested = record_platform_action(
        actor=actor, request=request, action="system_mail.stage_requested",
        target_label=SYSTEM_MAIL_DOMAIN, result="pending",
        metadata={"revision": expected_revision, "scope": "inactive_only"},
    )

    try:
        response = requests.post(
            getattr(settings, "NATIVE_ENGINE_API_URL").rstrip("/") + "/v1/platform/system/stage",
            headers={
                "X-Native-Platform-Secret": getattr(settings, "NATIVE_ENGINE_PLATFORM_SECRET"),
            },
            json={"domain": SYSTEM_MAIL_DOMAIN, "operation": "stage_inactive"},
            timeout=(3, 10),
        )
        if response.status_code != 200:
            raise StageUnavailable("Native Engine refused inactive staging.")
        payload = response.json()
        if (
            not isinstance(payload, dict) or payload.get("domain") != SYSTEM_MAIL_DOMAIN or
            payload.get("state") != "inactive" or
            payload.get("passwords_created") is not False or
            payload.get("delivery_activated") is not False or
            payload.get("dkim_modified") is not False or
            payload.get("roles") != 5
        ):
            raise StageUnavailable("Native Engine staging result could not be verified.")
    except (requests.RequestException, ValueError, StageUnavailable):
        logger.warning("Native Engine system mail staging outcome requires review")
        record_platform_action(
            actor=actor, request=request, action="system_mail.stage_outcome_unknown",
            target_label=SYSTEM_MAIL_DOMAIN, result="unknown",
            metadata={"revision": expected_revision, "request_id": str(requested.pk)},
        )
        raise StageUnavailable("Staging outcome is unconfirmed. Review engine state before retrying.")

    record_platform_action(
        actor=actor, request=request, action="system_mail.stage_confirmed",
        target_label=SYSTEM_MAIL_DOMAIN,
        metadata={"revision": expected_revision, "request_id": str(requested.pk)},
    )
    return {
        "state": "inactive",
        "roles": 5,
        "delivery_activated": False,
        "dkim_modified": False,
        "activation_supported": False,
        "request_id": str(requested.pk),
    }
