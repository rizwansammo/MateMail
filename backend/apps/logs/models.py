import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class LogEventType(models.TextChoices):
    MAILBOX_CREATED = "mailbox_created", "Mailbox Created"
    MAILBOX_DELETED = "mailbox_deleted", "Mailbox Deleted"
    MAILBOX_DISABLED = "mailbox_disabled", "Mailbox Disabled"
    # Platform-admin abuse actions, distinct from a tenant disabling its
    # own mailbox: only MateMail can set or clear these.
    DOMAIN_OWNERSHIP_STALE = "domain_ownership_stale", "Domain Ownership Record Missing"
    MAILBOX_SUSPENDED = "mailbox_suspended", "Mailbox Suspended by Platform"
    MAILBOX_UNSUSPENDED = "mailbox_unsuspended", "Mailbox Unsuspended by Platform"
    DOMAIN_ADDED = "domain_added", "Domain Added"
    DOMAIN_DELETED = "domain_deleted", "Domain Deleted"
    # Ownership verification (P3). Worth auditing in its own right: it is the
    # event that authorises a domain to receive mail.
    DOMAIN_OWNERSHIP_VERIFIED = "domain_ownership_verified", "Domain Ownership Verified"
    DOMAIN_OWNERSHIP_FAILED = "domain_ownership_failed", "Domain Ownership Check Failed"
    DOMAIN_TOKEN_ROTATED = "domain_token_rotated", "Domain Verification Token Rotated"
    TENANT_SUSPENDED = "tenant_suspended", "Tenant Suspended"
    TENANT_REACTIVATED = "tenant_reactivated", "Tenant Reactivated"
    # Approval lifecycle (P5). A workspace being allowed to send mail is a
    # platform decision with consequences for everyone's sending reputation,
    # so who made it and when is part of the permanent record.
    TENANT_APPROVED = "tenant_approved", "Tenant Approved for Mail"
    TENANT_REJECTED = "tenant_rejected", "Tenant Rejected"
    TENANT_OUTBOUND_DISABLED = "tenant_outbound_disabled", "Tenant Outbound Mail Disabled"
    TENANT_OUTBOUND_ENABLED = "tenant_outbound_enabled", "Tenant Outbound Mail Re-enabled"
    PLAN_CHANGED = "plan_changed", "Plan Changed"
    LOGIN = "login", "Login"
    LOGOUT = "logout", "Logout"
    # API keys are long-lived credentials that act without a person present,
    # so their whole lifecycle is auditable — including scope changes, which
    # are what decides how much damage a leaked key can do.
    API_KEY_CREATED = "api_key_created", "API Key Created"
    API_KEY_REVOKED = "api_key_revoked", "API Key Revoked"
    API_KEY_SCOPES_CHANGED = "api_key_scopes_changed", "API Key Scopes Changed"


class MailLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="mail_logs",
    )
    event_type = models.CharField(max_length=64)
    source = models.CharField(max_length=255, blank=True, default="")
    result = models.CharField(max_length=64, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        # Explicit table name, matching every other model in this project.
        # Migration 0001 created this table as "logs_mail_log"; the Phase 10 model
        # rewrite dropped the db_table line, silently repointing the ORM at
        # "logs_maillog" (which does not exist) and breaking every audit write.
        db_table = "logs_mail_log"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "event_type"]),
        ]

    def __str__(self):
        return f"{self.event_type} [{self.tenant_id}] @ {self.created_at}"
