"""P4-C.B optional transport security intent, scoped to the existing verified mail domain.

No DNS, web server, ACME or Postfix operations occur in this application.
The independent, root-owned provisioning worker arrives in P4-C.C.
"""
import secrets

from django.db import models


def new_policy_id() -> str:
    """An opaque MTA-STS version identifier, stable across repeated opt-in calls."""
    return secrets.token_hex(12)


class TransportSecurityLifecycle(models.TextChoices):
    DISABLED = "disabled", "Disabled"
    PENDING_DNS = "pending_dns", "Waiting for DNS and policy hosting"
    PROVISIONING = "provisioning", "Provisioning"
    READY = "ready", "HTTPS verified, DNS publication pending"
    ACTIVE = "active", "Active"
    ERROR = "error", "Provisioning error"
    DEACTIVATING = "deactivating", "Replacing HTTPS policy with mode none"
    DRAINING = "draining", "Awaiting customer DNS removal and MTA-STS cache expiry"


class TransportSecurityCertificateStatus(models.TextChoices):
    NOT_REQUESTED = "not_requested", "Not requested"
    ISSUING = "issuing", "Issuing"
    ACTIVE = "active", "Active"
    ERROR = "error", "Error"
    REVOKED = "revoked", "Revoked"


class DomainTransportSecurity(models.Model):
    """Exactly one optional transport-security policy per existing mail domain.

    The domain owns tenant identity. Never accept tenant, domain, lifecycle,
    certificate or policy_id as client input. The database OneToOne constraint
    prevents duplicate concurrent enrolments.
    """

    domain = models.OneToOneField(
        "domains.Domain", on_delete=models.PROTECT,
        primary_key=True, related_name="transport_security",
    )
    enabled = models.BooleanField(default=False)
    lifecycle = models.CharField(
        max_length=20, choices=TransportSecurityLifecycle.choices,
        default=TransportSecurityLifecycle.DISABLED,
    )
    certificate_status = models.CharField(
        max_length=20, choices=TransportSecurityCertificateStatus.choices,
        default=TransportSecurityCertificateStatus.NOT_REQUESTED,
    )
    policy_id = models.CharField(max_length=24, default=new_policy_id, editable=False)
    dns_verified_at = models.DateTimeField(null=True, blank=True)
    # Last explicit publication check; not a continuous DNS-monitoring claim.
    dns_records_checked_at = models.DateTimeField(null=True, blank=True)
    sts_txt_verified_at = models.DateTimeField(null=True, blank=True)
    tls_rpt_txt_verified_at = models.DateTimeField(null=True, blank=True)
    cert_verified_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    deactivation_requested_at = models.DateTimeField(null=True, blank=True)
    deactivation_policy_none_at = models.DateTimeField(null=True, blank=True)
    deactivation_dns_absent_since = models.DateTimeField(null=True, blank=True)
    deactivation_completed_at = models.DateTimeField(null=True, blank=True)
    # Only application-authored safe messages; no raw Certbot error or DNS output.
    last_error = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "transport_security_configuration"

    def __str__(self) -> str:
        return f"{self.domain.domain}: {self.lifecycle}"
