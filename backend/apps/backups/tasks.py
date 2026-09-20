import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

#: What the customer and the operator are told. Deliberately specific: "failed"
#: with no explanation is what sends somebody looking for a broken worker.
NOT_IMPLEMENTED_MESSAGE = (
    "Per-organization backup export is not implemented. MateMail's disaster-"
    "recovery backups are taken at the platform level by the restic-based "
    "system in deploy/backup/ and are not exposed here. No archive was created "
    "for this request."
)


@shared_task(bind=True, max_retries=0)
def run_backup_task(self, job_id):
    """
    Record that a per-tenant backup was requested, and that none was taken.

    WHAT THIS USED TO DO, AND WHY IT WAS DANGEROUS
        The previous implementation counted the tenant's domains and mailboxes,
        set `size_mb = domain_count * 2 + mailbox_count * 5`, wrote a
        `storage_location` of "backups/<slug>/<id>.tar.gz" pointing at a file
        that was never created, and marked the job COMPLETED. Nothing was ever
        read, archived, encrypted or stored.

        Every one of those fields reads as evidence. A customer asking "when
        was my last backup?" got a green row and a plausible size; an operator
        checking before a risky change got the same. The one moment the lie
        would have surfaced is a restore, which is the moment there is nothing
        left to fall back on. A backup system that reports success it did not
        earn is worse than no backup system, because it removes the reason to
        build one.

        So this no longer claims anything. It fails the job with an explanation
        rather than inventing an archive, and `size_mb` and `storage_location`
        stay empty because there is no archive to describe.

    THE REAL BACKUPS
        Platform disaster recovery is the P6 restic system — nightly via
        `matemail-backup.timer`, with restore drills and offsite copies, run on
        the host and documented in docs/BACKUP_RESTORE.md. It backs up the
        whole platform, not one organization, which is why it has no
        per-tenant API to surface here.
    """
    from .models import BackupJob, BackupStatus

    try:
        job = BackupJob.objects.select_related("tenant").get(id=job_id)
    except BackupJob.DoesNotExist:
        logger.error("BackupJob %s not found", job_id)
        return

    now = timezone.now()
    job.status = BackupStatus.FAILED
    job.started_at = job.started_at or now
    job.completed_at = now
    job.error_message = NOT_IMPLEMENTED_MESSAGE
    # Left at their defaults on purpose. A size and a path are claims about an
    # artefact, and there is no artefact.
    job.size_mb = 0
    job.storage_location = ""
    job.restore_metadata = {}
    job.save(update_fields=[
        "status", "started_at", "completed_at", "error_message",
        "size_mb", "storage_location", "restore_metadata",
    ])

    logger.warning(
        "BackupJob %s requested for tenant %s: per-tenant export is not "
        "implemented; no archive was created.",
        job_id, job.tenant_id,
    )
