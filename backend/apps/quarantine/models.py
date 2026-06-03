import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class QuarantineStatus(models.TextChoices):
    HELD = "held", "Held"
    RELEASED = "released", "Released"
    DELETED = "deleted", "Deleted"
    SENDER_BLOCKED = "sender_blocked", "Sender Blocked"


class QuarantineMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="quarantine_messages"
    )
    engine_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    sender = models.EmailField()
    recipient = models.EmailField()
    subject = models.CharField(max_length=998, blank=True)
    spam_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20, choices=QuarantineStatus.choices, default=QuarantineStatus.HELD
    )
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actioned_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "quarantine_message"
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.sender} [{self.spam_score}] {self.status}"
