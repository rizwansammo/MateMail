"""Preview only; never tell customers to publish DNS before HTTPS is valid."""
from django.conf import settings
from rest_framework import serializers

from .models import TransportSecurityLifecycle, TransportSecurityCertificateStatus


class TransportSecurityToggleSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=True)

    def validate(self, attrs):
        # Deliberately one writable field: policy mode, MX, DNS verification,
        # certificate and internal lifecycle cannot be user-controlled.
        if set(self.initial_data) != {"enabled"}:
            raise serializers.ValidationError("Only the enabled field is supported.")
        return attrs



def self_service_available_for(domain) -> bool:
    """Global launch or an operator-approved exact Domain UUID canary.

    Unknown/invalid values cannot expand access; domain names, tenant IDs and
    wildcard entries never grant canary access.
    """
    if getattr(settings, "TRANSPORT_SECURITY_SELF_SERVICE_ENABLED", False):
        return True
    approved = (getattr(settings, "TRANSPORT_SECURITY_CANARY_DOMAIN_IDS", "") or "")
    if not isinstance(approved, str):
        return False
    canonical_id = str(domain.pk).lower()
    return any(part.strip().lower() == canonical_id for part in approved.split(","))


def describe_transport_security(domain, config=None) -> dict:
    """Tenant-safe instructions, with explicit gates and no false Active status.

    P4-C.B DOES NOT validate, provision, or publish external DNS/HTTPS.
    Hosts are full FQDNs, not guessed DNS-zone-relative labels (subdomains may
    be managed in a parent zone). P4-C.C will supply a verified central edge.
    """
    name = domain.domain
    enabled = bool(config and config.enabled)
    lifecycle = config.lifecycle if config else TransportSecurityLifecycle.DISABLED
    edge = (getattr(settings, "MTA_STS_POLICY_EDGE_TARGET", "") or "").strip().lower().rstrip(".")
    # The edge must be explicitly configured in P4-C.C and validated before it
    # is presented as a DNS target. No use of the Hub/PostBox custom-host edge
    # here: its current vhosts cannot serve MTA-STS policies.
    edge_configured = bool(
        edge
        and len(edge) <= 253
        and all(part and len(part) <= 63 and part.isascii()
                and part.replace("-", "").isalnum() and not part.startswith("-")
                and not part.endswith("-") for part in edge.split("."))
        and "." in edge
    )
    policy_host = f"mta-sts.{name}"
    mx_host = getattr(settings, "MAIL_HOSTNAME", "mx.matemail.pro").strip().rstrip(".").lower()
    report_address = getattr(settings, "TLS_RPT_REPORT_ADDRESS", "tlsrpt@mail.matemail.pro")
    # P4-C.B's static preview MUST NOT be mistaken for verified service readiness.
    records = []
    if enabled and lifecycle not in (
        TransportSecurityLifecycle.DEACTIVATING, TransportSecurityLifecycle.DRAINING,
    ):
        records = [
            {
                "type": "CNAME",
                "host": policy_host,
                "value": edge if edge_configured else None,
                "publish_ready": False,
                "requirement": "P4-C.C verified policy gateway and certificate",
            },
            {
                "type": "TXT",
                "host": f"_mta-sts.{name}",
                "value": f"v=STSv1; id={config.policy_id}",
                "publish_ready": False,
                "requirement": "HTTPS 200, trusted certificate and MX validation",
            },
            {
                "type": "TXT",
                "host": f"_smtp._tls.{name}",
                "value": f"v=TLSRPTv1; rua=mailto:{report_address}",
                "publish_ready": False,
                "requirement": "P4-C.E verified TLS-RPT intake and parser",
            },
        ]
        # "Ready to publish" is permission; it is NOT a live DNS result.
        # Each record displays only the last explicitly checked state.
        for index, record in enumerate(records):
            verified_at = (
                config.dns_verified_at if index == 0 else
                config.sts_txt_verified_at if index == 1 else
                config.tls_rpt_txt_verified_at
            )
            checked_at = (
                config.dns_verified_at if index == 0 else
                config.dns_records_checked_at
            )
            record["verified_at"] = verified_at
            record["verification_status"] = (
                "not_checked" if checked_at is None else
                "verified" if verified_at is not None else "missing"
            )
    # CNAME becomes displayable only when the platform operator has confirmed
    # that the dedicated policy gateway resolves to the HTTPS provisioner.
    edge_ready = bool(getattr(settings, "MTA_STS_POLICY_EDGE_READY", False))
    if enabled and domain.is_ownership_verified and records and edge_configured and edge_ready:
        records[0]["publish_ready"] = True
        records[0]["requirement"] = "Create this CNAME, then verify DNS in MateMail Hub."
    # TLS-RPT TXT is separately gated by an operator-tested recipient and
    # verified parser. Merely deploying Phase E never unlocks DNS publication.
    if (enabled and domain.is_ownership_verified and records and getattr(settings, "TLS_RPT_INGEST_ENABLED", False)
            and getattr(settings, "TLS_RPT_DNS_PUBLICATION_ENABLED", False)
            and getattr(settings, "TLS_RPT_RECEIVER_VERIFIED", False)
            and report_address.strip().lower() == "tlsrpt@mail.matemail.pro"):
        records[2]["publish_ready"] = True
        records[2]["requirement"] = "Verified central TLS report intake; publish this TXT when approved."
    # STS TXT must remain withheld until the worker has actually served and
    # TLS-verified the requested hostname (never use an optimistic status).
    if enabled and domain.is_ownership_verified and config and config.lifecycle in (
        TransportSecurityLifecycle.READY,
        TransportSecurityLifecycle.ACTIVE,
    ) and config.certificate_status == TransportSecurityCertificateStatus.ACTIVE and config.cert_verified_at:
        records[1]["publish_ready"] = True
        records[1]["requirement"] = "Verified HTTPS policy; publish this TXT to announce testing mode."
    offboarding = lifecycle in (
        TransportSecurityLifecycle.DEACTIVATING, TransportSecurityLifecycle.DRAINING,
    )
    return {
        "domain": name,
        "ownership_verified": domain.is_ownership_verified,
        "optional": True,
        "enabled": enabled,
        "self_service_available": self_service_available_for(domain),
        "lifecycle": lifecycle,
        "certificate_status": config.certificate_status if config else "not_requested",
        "policy_mode": "none" if (offboarding and config.deactivation_policy_none_at) else "testing",  # actual worker-confirmed mode.
        "mx": mx_host,
        "max_age_seconds": 86400,
        "policy_url": f"https://{policy_host}/.well-known/mta-sts.txt" if enabled else None,
        "edge_configured": edge_configured,
        "dns_records": records,
        "dns_verified_at": config.dns_verified_at if config else None,
        "dns_records_checked_at": config.dns_records_checked_at if config else None,
        "cert_verified_at": config.cert_verified_at if config else None,
        "activated_at": config.activated_at if config else None,
        "offboarding": offboarding,
        "deactivation_requested_at": config.deactivation_requested_at if config else None,
        "deactivation_policy_none_at": config.deactivation_policy_none_at if config else None,
        "deactivation_dns_absent_since": config.deactivation_dns_absent_since if config else None,
        "deactivation_completed_at": config.deactivation_completed_at if config else None,
        "offboarding_dns_records": [
            {"type": "TXT", "host": f"_mta-sts.{name}", "action": "Remove after the HTTPS policy shows mode none"},
            {"type": "TXT", "host": f"_smtp._tls.{name}", "action": "Remove to stop aggregate TLS reports"},
        ] if offboarding else [],
        "last_error": config.last_error if config else "",
        "can_publish_dns": bool(records) and all(item["publish_ready"] for item in records),  # TLS-RPT waits for P4-C.E.
        "detail": (
            "Safe offboarding in progress. Keep the MTA-STS CNAME until cleanup completes."
            if offboarding else
            "Transport security is optional. DNS records are not ready to publish "
            "until verified HTTPS hosting and TLS report ingestion are available."
            if enabled else
            "Optional advanced transport security is disabled; mail remains available."
        ),
    }
