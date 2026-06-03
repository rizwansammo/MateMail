import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class QueueStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    DEFERRED = "deferred", "Deferred"
    FAILED = "failed", "Failed"
    DELIVERED = "delivered", "Delivered"
    CANCELLED = "cancelled", "Cancelled"


class QueueMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="queue_messages"
    )
    # Opaque ID from the mail engine queue
    engine_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    sender = models.EmailField()
    recipient = models.EmailField()
    subject = models.CharField(max_length=998, blank=True)
    status = models.CharField(
        max_length=20, choices=QueueStatus.choices, default=QueueStatus.PENDING
    )
    reason = models.TextField(blank=True)
    queued_at = models.DateTimeField(auto_now_add=True)
    last_retry = models.DateTimeField(null=True, blank=True)
    next_retry = models.DateTimeField(null=True, blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "mailqueue_message"
        ordering = ["-queued_at"]

    def __str__(self):
        return f"{self.sender} -> {self.recipient} [{self.status}]"
