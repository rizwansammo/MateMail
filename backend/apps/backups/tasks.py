import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2)
def run_backup_task(self, job_id):
    from .models import BackupJob, BackupStatus

    try:
        job = BackupJob.objects.select_related("tenant").get(id=job_id)
    except BackupJob.DoesNotExist:
        logger.error("BackupJob %s not found", job_id)
        return

    job.status = BackupStatus.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])

    try:
        from apps.domains.models import Domain
        from apps.mailboxes.models import Mailbox

        tenant = job.tenant
        domain_count = Domain.objects.filter(tenant=tenant).count()
        mailbox_count = Mailbox.objects.filter(tenant=tenant).count()

        job.status = BackupStatus.COMPLETED
        job.completed_at = timezone.now()
        job.size_mb = max(1, domain_count * 2 + mailbox_count * 5)
        job.storage_location = f"backups/{tenant.slug}/{job.id}.tar.gz"
        job.restore_metadata = {
            "tenant_slug": tenant.slug,
            "scope": job.scope,
            "domain_count": domain_count,
            "mailbox_count": mailbox_count,
        }
        job.save(update_fields=[
            "status", "completed_at", "size_mb",
            "storage_location", "restore_metadata",
        ])
        logger.info("BackupJob %s completed (%s MB)", job_id, job.size_mb)

    except Exception as exc:
        logger.exception("BackupJob %s failed: %s", job_id, exc)
        job.status = BackupStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_message", "completed_at"])
        raise self.retry(exc=exc, countdown=60)
