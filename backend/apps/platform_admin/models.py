"""
Platform admin models.

The audit log for internal staff actions lives here rather than in
`apps.logs.MailLog`, which requires a tenant. Some of the actions that most
need recording have no organization attached — a platform login, a password
reset, a failed attempt to reach the console — and a log that could only
describe tenant-scoped events would be missing exactly those.
"""
import uuid

from django.conf import settings
from django.db import models


class PlatformAuditLog(models.Model):
    """
    One internal staff action, attributable after the fact.

    Rows are append-only by convention: nothing in the application updates or
    deletes one. `actor` is SET_NULL rather than CASCADE on purpose — removing
    an administrator must not erase the record of what they did, which is the
    one thing an audit log exists to prevent.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_actions",
    )
    #: Denormalised so the record still names who acted after the account is
    #: deleted and `actor` becomes NULL.
    actor_email = models.CharField(max_length=255, blank=True, default="")

    #: Dotted verb, e.g. "tenant.suspend", "owner.password_recovery".
    action = models.CharField(max_length=64)

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_actions",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_actions_received",
    )
    #: Free-form label for a target that is not a tenant or a user — a mailbox
    #: address, a domain, a queue id.
    target_label = models.CharField(max_length=255, blank=True, default="")

    result = models.CharField(max_length=32, default="success")
    reason = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    #: Never a secret. Codes, tokens, password hashes and mailbox contents do
    #: not belong here; see `record_platform_action`, which is the only writer.
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "platform_admin_audit_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.actor_email or 'system'} {self.action} @ {self.created_at:%Y-%m-%d %H:%M}"


class PlatformSystemMailRevision(models.Model):
    """Immutable platform-owned system mail *proposal*, never live settings.

    Phase 3 intentionally contains no activation or native provisioning API.
    Every change writes a new row and an audited pointer update atomically.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    revision = models.PositiveIntegerField(unique=True)
    domain = models.CharField(max_length=255)
    sender_name = models.CharField(max_length=80, default="MateMail")
    noreply_address = models.EmailField(max_length=254)
    security_address = models.EmailField(max_length=254)
    operations_address = models.EmailField(max_length=254)
    dmarc_address = models.EmailField(max_length=254)
    tlsrpt_address = models.EmailField(max_length=254)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="system_mail_config_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "platform_system_mail_revision"
        ordering = ["-revision"]


class PlatformSystemMailPointer(models.Model):
    """Single locked row serialises edits; no active pointer in Phase 3."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    draft = models.ForeignKey(
        PlatformSystemMailRevision, null=True, blank=True,
        on_delete=models.PROTECT, related_name="+",
    )
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "platform_system_mail_pointer"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1), name="system_mail_pointer_singleton",
            ),
        ]
