"""Platform-only preparation of the target system DKIM public DNS record.

Even a successful operation does not authorize activation or sender cutover.
The Native Engine keeps the private key; the platform receives only DNS TXT.
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings
from rest_framework import serializers

from .audit import record_platform_action
from .system_mail import SYSTEM_MAIL_DOMAIN
from .system_mail_dns import inspect_system_mail_dns
from .system_mail_stage import staging_prerequisites, StageUnavailable

logger = logging.getLogger(__name__)


class PrepareSystemDkimSerializer(serializers.Serializer):
    expected_revision = serializers.IntegerField(min_value=1)
    confirmation = serializers.ChoiceField(choices=["prepare_dkim_inactive"])

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({"non_field_errors": ["JSON object required."]})
        unexpected = set(data) - set(self.fields)
        if unexpected:
            raise serializers.ValidationError({
                key: "Unsupported field." for key in unexpected
            })
        return super().to_internal_value(data)


def prepare_system_dkim(*, actor, request, expected_revision: int) -> dict:
    if not getattr(settings, "SYSTEM_MAIL_DKIM_PREPARE_ENABLED", False):
        raise StageUnavailable("System DKIM preparation is disabled.")
    # This checks a live revision, platform ownership, dedicated platform
    # credentials and the independent Django staging feature gate.
    staging_prerequisites(expected_revision)

    dns = inspect_system_mail_dns()
    if any(dns[record] != "observed" for record in ("mx", "spf", "dmarc")):
        raise StageUnavailable("Target domain MX, SPF and DMARC must pass first.")

    action = record_platform_action(
        actor=actor, request=request,
        action="system_mail.dkim_prepare_requested",
        target_label=SYSTEM_MAIL_DOMAIN, result="pending",
        metadata={"revision": expected_revision, "scope": "inactive_dkim_only"},
    )

    try:
        response = requests.post(
            getattr(settings, "NATIVE_ENGINE_API_URL").rstrip("/")
            + "/v1/platform/system/prepare-dkim",
            headers={
                "X-Native-Platform-Secret": getattr(
                    settings, "NATIVE_ENGINE_PLATFORM_SECRET"
                ),
            },
            json={"domain": SYSTEM_MAIL_DOMAIN, "operation": "prepare_dkim_inactive"},
            timeout=(3, 30),
        )
        if response.status_code != 200:
            raise StageUnavailable("Native Engine refused DKIM preparation.")
        payload = response.json()
        if not isinstance(payload, dict) or (
            payload.get("domain") != SYSTEM_MAIL_DOMAIN
            or payload.get("state") != "inactive"
            or payload.get("selector") != "mm1"
            or payload.get("dns_record_name") != f"mm1._domainkey.{SYSTEM_MAIL_DOMAIN}"
            or not isinstance(payload.get("dns_record_value"), str)
            or not payload["dns_record_value"].startswith("v=DKIM1; k=rsa; p=")
            or payload.get("mail_delivery_activated") is not False
            or payload.get("credentials_created") is not False
        ):
            raise StageUnavailable("Native Engine DKIM preparation result unconfirmed.")
    except (requests.RequestException, ValueError, StageUnavailable):
        logger.warning("System DKIM preparation outcome requires engine verification")
        record_platform_action(
            actor=actor, request=request,
            action="system_mail.dkim_prepare_outcome_unknown",
            target_label=SYSTEM_MAIL_DOMAIN, result="unknown",
            metadata={"revision": expected_revision, "request_id": str(action.pk)},
        )
        raise StageUnavailable(
            "DKIM preparation outcome unconfirmed. Inspect Native Engine before retrying."
        )

    record_platform_action(
        actor=actor, request=request,
        action="system_mail.dkim_prepare_confirmed",
        target_label=SYSTEM_MAIL_DOMAIN,
        metadata={"revision": expected_revision, "request_id": str(action.pk)},
    )
    return {
        "domain": SYSTEM_MAIL_DOMAIN,
        "state": "inactive",
        "dns_record_name": payload["dns_record_name"],
        "dns_record_value": payload["dns_record_value"],
        "activation_supported": False,
        "mail_delivery_activated": False,
        "credentials_created": False,
    }
