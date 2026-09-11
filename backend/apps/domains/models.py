import uuid

from django.db import models
from django.db.models import Q

from apps.tenants.managers import TenantScopedManager


class DomainStatus(models.TextChoices):
    """Operational/DNS health state. Distinct from ownership — see below."""

    PENDING = "pending", "Pending"
    ACTIVE = "active", "Active"
    WARNING = "warning", "Warning"
    FAILED = "failed", "Failed"
    PAUSED = "paused", "Paused"


class DomainOwnership(models.TextChoices):
    """
    Whether the tenant has proved control of the domain.

    This is deliberately separate from `status`, which tracks mail DNS health
    (MX/SPF/DKIM/DMARC). A domain can be ownership-VERIFIED but DNS-unhealthy,
    and it can look DNS-healthy while its ownership is unproven — the second
    case is exactly the hole P3 closes.

    State machine:

        PENDING ──(correct TXT found)──▶ VERIFIED
           │                                │
           └──(token rotated)───────────────┘   rotation returns to PENDING

    There is no third state. A failed check leaves the claim PENDING and
    records why, so the customer can fix DNS and retry with the same token.
    """

    PENDING = "pending", "Pending verification"
    VERIFIED = "verified", "Verified"


class Domain(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="domains"
    )
    # NOT globally unique any more. Several tenants may hold a *pending* claim
    # on the same name — that is the only way an honest customer can claim a
    # domain a squatter has already typed in. Exclusivity applies at
    # verification, enforced by the partial unique index in Meta.
    domain = models.CharField(max_length=255, db_index=True)
    status = models.CharField(
        max_length=20, choices=DomainStatus.choices, default=DomainStatus.PENDING
    )
    dns_health_score = models.IntegerField(default=0)

    # ── Ownership verification (P3) ─────────────────────────────────────────
    ownership_status = models.CharField(
        max_length=20,
        choices=DomainOwnership.choices,
        default=DomainOwnership.PENDING,
        db_index=True,
    )
    #: The value the customer publishes at _matemail-verify.<domain>.
    #: Generated with secrets.token_urlsafe — never a hash, counter or uuid4,
    #: which would be guessable or enumerable.
    verification_token = models.CharField(max_length=128, blank=True)
    ownership_verified_at = models.DateTimeField(null=True, blank=True)
    verification_last_checked_at = models.DateTimeField(null=True, blank=True)
    #: MateMail-authored explanation of the last failed check. Never raw
    #: resolver output — see apps.domains.verification.
    verification_last_error = models.TextField(blank=True)

    # DKIM
    dkim_selector = models.CharField(max_length=63, default="mm1")
    # Public key text, shown to the customer for the DNS TXT record.
    dkim_public_key = models.TextField(blank=True)
    # DEPRECATED (DEC-007r): the Mail Engine must own the private key. This
    # column is technical debt scheduled for removal in P4 — do not read or
    # write it in new code, and do not build anything on the assumption that
    # Django holds DKIM keys. Never exposed via API or Django admin.
    #
    # Encrypted at rest as of P3c (apps.domains.keystore), because until P4
    # removes it, this column is a domain's signing authority sitting in every
    # database backup. Do NOT read or write it directly: use the
    # `dkim_private_key_pem` property below, which handles decryption and rows
    # written before the change. Read raw, this field is ciphertext.
    dkim_private_key = models.TextField(blank=True)

    # Provisioning state in the mail engine
    mail_engine_provisioned = models.BooleanField(default=False)
    mail_engine_error = models.TextField(blank=True)

    added_at = models.DateTimeField(auto_now_add=True)
    #: DNS *health* verification timestamp (MX+SPF seen). Not ownership —
    #: ownership has its own ownership_verified_at above.
    verified_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "domains_domain"
        ordering = ["-added_at"]
        constraints = [
            # One tenant cannot stack duplicate claims on the same name.
            models.UniqueConstraint(
                fields=["tenant", "domain"],
                name="uniq_domain_per_tenant",
            ),
            # THE ownership invariant: at most one VERIFIED row per domain,
            # across every tenant. A partial unique index, so pending claims
            # are unaffected.
            #
            # Enforced by the database rather than a check-then-save in Python:
            # two tenants verifying concurrently would both pass an application
            # check and both commit. Here the second transaction fails with
            # IntegrityError, which verification.py translates into a clear
            # customer message.
            models.UniqueConstraint(
                fields=["domain"],
                condition=Q(ownership_status="verified"),
                name="uniq_verified_domain_owner",
            ),
        ]

    def __str__(self):
        return self.domain

    # ── DKIM key access (INTERIM — removed in P4, DEC-007r) ─────────────────

    @property
    def dkim_private_key_pem(self) -> str:
        """
        The DKIM private key as PEM, decrypted.

        This whole accessor disappears in P4 when the Mail Engine owns the key.
        It raises DkimKeyUnavailable rather than returning "" when the stored
        value cannot be read: an empty string would read as "this domain has no
        DKIM key" and the domain would be provisioned to send unsigned mail.
        """
        from .keystore import decrypt

        return decrypt(self.dkim_private_key)

    @dkim_private_key_pem.setter
    def dkim_private_key_pem(self, pem: str) -> None:
        from .keystore import encrypt

        self.dkim_private_key = encrypt(pem)

    @property
    def has_dkim_private_key(self) -> bool:
        """Presence, without touching the key material."""
        return bool(self.dkim_private_key)

    # ── Ownership helpers ───────────────────────────────────────────────────

    @property
    def is_ownership_verified(self) -> bool:
        return self.ownership_status == DomainOwnership.VERIFIED

    @property
    def verification_record_name(self) -> str:
        """The DNS host the customer must create."""
        return f"_matemail-verify.{self.domain}"

    @property
    def verification_record_value(self) -> str:
        """The exact TXT value expected at that host."""
        return self.verification_token
