import uuid
from django.db import models
from apps.tenants.managers import TenantScopedManager


class BackupScope(models.TextChoices):
    WORKSPACE = "workspace", "Workspace"
    DOMAIN = "domain", "Domain"
    MAILBOX = "mailbox", "Mailbox"


class BackupStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class BackupJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "tenants.Tenant", on_delete=models.CASCADE, related_name="backup_jobs"
    )
    scope = models.CharField(
        max_length=20, choices=BackupScope.choices, default=BackupScope.WORKSPACE
    )
    status = models.CharField(
        max_length=20, choices=BackupStatus.choices, default=BackupStatus.PENDING
    )
    size_mb = models.PositiveIntegerField(default=0)
    storage_location = models.CharField(max_length=500, blank=True)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    restore_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantScopedManager()

    class Meta:
        db_table = "backups_job"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.scope} backup [{self.status}]"
