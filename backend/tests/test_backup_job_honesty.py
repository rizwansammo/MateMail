"""
The per-tenant backup endpoint must not claim a backup it did not take.

Regression. `run_backup_task` used to count the tenant's domains and mailboxes,
derive a plausible `size_mb` from those counts, write a `storage_location`
pointing at a .tar.gz that was never created, and mark the job COMPLETED. No
data was read and no archive was written.

Those fields are read as evidence — by a customer asking when their last backup
ran, and by an operator checking before a risky change. The only moment the
claim would have been tested is a restore, which is the one moment there is
nothing to fall back on.

The real disaster-recovery backups are the platform-level restic system in
deploy/backup/, covered by tests/test_backup_restore.py.
"""
from django.test import TestCase

from apps.backups.models import BackupJob, BackupStatus
from apps.backups.tasks import NOT_IMPLEMENTED_MESSAGE, run_backup_task
from tests.factories import make_tenant, make_user


class BackupJobHonestyTest(TestCase):
    def setUp(self):
        self.owner = make_user("owner@acme.test")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        self.job = BackupJob.objects.create(tenant=self.tenant, scope="full")

    def run_job(self) -> BackupJob:
        run_backup_task(str(self.job.id))
        self.job.refresh_from_db()
        return self.job

    def test_the_job_does_not_report_success(self):
        self.assertEqual(BackupStatus.FAILED, self.run_job().status)

    def test_it_explains_itself_rather_than_failing_blankly(self):
        """
        A bare "failed" is what sends an operator hunting a broken worker. The
        message has to say that nothing is broken and nothing was backed up.
        """
        job = self.run_job()
        self.assertEqual(NOT_IMPLEMENTED_MESSAGE, job.error_message)
        self.assertIn("not implemented", job.error_message.lower())
        self.assertIn("deploy/backup/", job.error_message)

    def test_no_archive_is_described(self):
        """A size and a path are claims about an artefact that does not exist."""
        job = self.run_job()
        self.assertEqual(0, job.size_mb)
        self.assertEqual("", job.storage_location)
        self.assertEqual({}, job.restore_metadata)

    def test_the_size_is_not_derived_from_object_counts(self):
        """
        The specific fabrication: size_mb = domains * 2 + mailboxes * 5. With
        objects present, a number that tracks them is the old behaviour
        returning.
        """
        from apps.domains.models import Domain

        Domain.objects.create(tenant=self.tenant, domain="acme.test")
        self.assertEqual(0, self.run_job().size_mb)

    def test_a_missing_job_is_survivable(self):
        import uuid

        run_backup_task(str(uuid.uuid4()))  # must not raise
