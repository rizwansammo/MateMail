"""
Deleting a domain must not lose custody of its signing key.

The engine keeps a domain's DKIM keypair after the domain itself is removed, and
issues it to whoever registers that name next. `deprovision_domain_task` is what
removes it — so that task being queued is the only thing standing between a
domain deletion and an orphaned private key that can sign mail as that domain.

The local Domain row is the only durable record that cleanup is owed. If it is
deleted while the task failed to reach the broker, there is nothing left to
reconcile: no domain in MateMail, a live key in the engine, and no job anywhere
that will remove it. So a broker failure blocks the deletion.

This replaced two earlier behaviours, both wrong:

  except Exception: pass          — the failure vanished entirely
  log the failure, delete anyway  — recorded in a file nobody reads, while
                                    creating exactly the state above
"""
from unittest import mock

from django.test import TestCase

from apps.domains.models import Domain
from tests.factories import auth_client, disable_throttling, make_domain, make_tenant, make_user

BROKER_DOWN = OSError("redis: connection refused")


class DomainDeleteDurabilityTest(TestCase):
    def setUp(self):
        self.user = make_user("owner@example.com")
        self.tenant = make_tenant(self.user)
        self.domain = make_domain(self.tenant, "delete-me.example")
        self.domain.mail_engine_provisioned = True
        self.domain.save(update_fields=["mail_engine_provisioned"])

        disable_throttling(self)
        self.client = auth_client(self.user, self.tenant)
        self.url = f"/api/domains/{self.domain.id}/"

    def _delete(self, delay):
        with mock.patch(
            "apps.mail_engine.tasks.deprovision_domain_task.delay", delay
        ) as patched:
            response = self.client.delete(self.url)
        return response, patched

    def test_advanced_transport_domain_cannot_be_deleted_before_offboarding(self):
        from apps.transport_security.models import DomainTransportSecurity, TransportSecurityLifecycle
        for stage in (TransportSecurityLifecycle.PENDING_DNS,
                      TransportSecurityLifecycle.PROVISIONING,
                      TransportSecurityLifecycle.READY,
                      TransportSecurityLifecycle.ACTIVE,
                      TransportSecurityLifecycle.ERROR,
                      TransportSecurityLifecycle.DEACTIVATING):
            with self.subTest(stage=stage):
                row, _ = DomainTransportSecurity.objects.update_or_create(
                    domain=self.domain,
                    defaults={"enabled": True, "lifecycle": stage},
                )
                response, queued = self._delete(mock.Mock())
                self.assertEqual(response.status_code, 409)
                self.assertIn("Advanced transport security", response.data["detail"])
                self.assertTrue(Domain.objects.filter(pk=self.domain.pk).exists())
                queued.assert_not_called()

        row.enabled = False
        row.lifecycle = TransportSecurityLifecycle.DISABLED
        row.save(update_fields=["enabled", "lifecycle"])
        response, queued = self._delete(mock.Mock())
        self.assertEqual(response.status_code, 204)
        queued.assert_called_once()

    def test_direct_orm_delete_is_protected_during_transport_security(self):
        from django.db.models.deletion import ProtectedError
        from apps.transport_security.models import DomainTransportSecurity, TransportSecurityLifecycle
        DomainTransportSecurity.objects.create(
            domain=self.domain, enabled=True, lifecycle=TransportSecurityLifecycle.ACTIVE,
        )
        with self.assertRaises(ProtectedError):
            self.domain.delete()
        self.assertTrue(Domain.objects.filter(pk=self.domain.pk).exists())

    def test_api_deletes_disabled_transport_config_without_cascade_hazards(self):
        from apps.transport_security.models import DomainTransportSecurity, TransportSecurityLifecycle
        DomainTransportSecurity.objects.create(
            domain=self.domain, enabled=False, lifecycle=TransportSecurityLifecycle.DISABLED,
        )
        response, enqueued = self._delete(mock.Mock())
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Domain.objects.filter(pk=self.domain.pk).exists())
        self.assertFalse(DomainTransportSecurity.objects.filter(domain_id=self.domain.pk).exists())
        enqueued.assert_called_once()

    # ── the happy path still works ──────────────────────────────────────────

    def test_queue_succeeds_so_the_local_domain_is_deleted(self):
        response, delay = self._delete(mock.Mock())

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Domain.objects.filter(pk=self.domain.pk).exists())
        delay.assert_called_once_with("delete-me.example")

    def test_the_task_is_queued_by_name_not_by_row_id(self):
        """
        The task must be able to run after the local row is gone, so it takes
        the domain name. Passing a primary key would make cleanup depend on a
        record this request is about to delete.
        """
        _, delay = self._delete(mock.Mock())
        (arg,), _ = delay.call_args
        self.assertEqual(arg, "delete-me.example")

    def test_cleanup_is_queued_even_when_the_provisioned_flag_is_false(self):
        """
        `mail_engine_provisioned` is cleared at the start of a re-provision, so
        a domain can hold engine state — including a key — while the flag reads
        False. Gating on it would skip cleanup in exactly that window.
        """
        self.domain.mail_engine_provisioned = False
        self.domain.save(update_fields=["mail_engine_provisioned"])

        response, delay = self._delete(mock.Mock())

        self.assertEqual(response.status_code, 204)
        delay.assert_called_once_with("delete-me.example")

    # ── the failure path: fail closed ───────────────────────────────────────

    def test_queue_failure_keeps_the_local_domain(self):
        response, _ = self._delete(mock.Mock(side_effect=BROKER_DOWN))

        self.assertNotEqual(response.status_code, 204)
        self.assertTrue(
            Domain.objects.filter(pk=self.domain.pk).exists(),
            "the domain was deleted while its engine cleanup was never queued — "
            "its DKIM signing key is now orphaned with nothing to reconcile against",
        )

    def test_queue_failure_returns_a_temporary_error(self):
        response, _ = self._delete(mock.Mock(side_effect=BROKER_DOWN))

        self.assertEqual(
            response.status_code, 503,
            "a broker outage is temporary; the customer must be able to retry",
        )

    def test_the_customer_message_is_safe_and_says_nothing_changed(self):
        response, _ = self._delete(mock.Mock(side_effect=BROKER_DOWN))

        detail = str(response.data.get("detail", ""))
        self.assertTrue(detail)
        # No infrastructure detail: not the broker, not the engine, not the
        # exception text.
        for leak in ("redis", "celery", "broker", "mailcow", "connection refused",
                     "OSError", "traceback", "DKIM"):
            self.assertNotIn(leak.lower(), detail.lower(), f"leaked {leak!r}")
        self.assertIn("try again", detail.lower())

    def test_queue_failure_is_logged_as_a_security_event(self):
        with self.assertLogs("apps.domains.views", level="ERROR") as logs:
            self._delete(mock.Mock(side_effect=BROKER_DOWN))

        blob = " ".join(logs.output)
        self.assertIn("SECURITY", blob)
        self.assertIn("delete-me.example", blob)

    def test_no_audit_event_claims_the_domain_was_deleted(self):
        """A deletion that did not happen must not appear in the audit log."""
        from apps.logs.models import MailLog

        before = MailLog.objects.filter(tenant=self.tenant).count()
        self._delete(mock.Mock(side_effect=BROKER_DOWN))
        self.assertEqual(MailLog.objects.filter(tenant=self.tenant).count(), before)

    def test_the_domain_is_still_usable_after_a_failed_delete(self):
        """Fail closed, not fail broken: nothing about the domain changed."""
        self._delete(mock.Mock(side_effect=BROKER_DOWN))

        self.domain.refresh_from_db()
        self.assertTrue(self.domain.mail_engine_provisioned)
        self.assertEqual(self.domain.domain, "delete-me.example")

    def test_a_retry_after_the_broker_recovers_succeeds(self):
        self._delete(mock.Mock(side_effect=BROKER_DOWN))
        self.assertTrue(Domain.objects.filter(pk=self.domain.pk).exists())

        response, delay = self._delete(mock.Mock())

        self.assertEqual(response.status_code, 204)
        self.assertFalse(Domain.objects.filter(pk=self.domain.pk).exists())
        delay.assert_called_once_with("delete-me.example")

    def test_every_broker_failure_mode_fails_closed(self):
        """
        Kombu raises several unrelated types for an unreachable broker, and new
        ones appear across versions. The guard must be about the outcome, not
        about recognising a particular exception class.
        """
        for exc in (
            OSError("connection refused"),
            ConnectionError("broker unreachable"),
            TimeoutError("broker timed out"),
            RuntimeError("kombu: no channel available"),
        ):
            with self.subTest(exception=type(exc).__name__):
                domain = make_domain(self.tenant, f"x-{type(exc).__name__}.example")
                url = f"/api/domains/{domain.id}/"
                with mock.patch(
                    "apps.mail_engine.tasks.deprovision_domain_task.delay",
                    mock.Mock(side_effect=exc),
                ):
                    response = self.client.delete(url)
                self.assertEqual(response.status_code, 503)
                self.assertTrue(Domain.objects.filter(pk=domain.pk).exists())


class NoBroadExceptionSwallowTest(TestCase):
    """
    The original code was `except Exception: pass` around this exact call. The
    catch is still broad — it has to be, because an unreachable broker surfaces
    as several unrelated types — but it must never again end in a bare pass or
    fall through to the deletion.
    """

    def test_the_delete_view_does_not_swallow_and_continue(self):
        import inspect

        from apps.domains import views

        source = inspect.getsource(views.DomainDetailView.delete)
        self.assertNotIn("except Exception:\n                pass", source)
        self.assertNotIn("pass\n", source.split("except Exception")[-1][:80])

    def test_the_failure_branch_returns_rather_than_falling_through(self):
        import inspect

        from apps.domains import views

        source = inspect.getsource(views.DomainDetailView.delete)
        after_except = source.split("except Exception")[-1]
        # The handler must return before reaching the deletion.
        self.assertIn("return Response", after_except.split("domain.delete()")[0])


class DeprovisionTaskRunsWithoutTheLocalRowTest(TestCase):
    """
    The task takes a domain name precisely so it can complete after the local
    record is gone — which is the normal case, since the row is deleted as soon
    as the task is queued.
    """

    def test_the_task_needs_no_local_domain_record(self):
        from apps.mail_engine.tasks import deprovision_domain_task

        self.assertFalse(Domain.objects.filter(domain="vanished.example").exists())

        adapter = mock.Mock()
        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            deprovision_domain_task("vanished.example")

        adapter.delete_dkim_key.assert_called_once_with("vanished.example")
        adapter.delete_domain.assert_called_once_with("vanished.example")

    def test_the_task_remains_idempotent(self):
        from apps.mail_engine.tasks import deprovision_domain_task

        adapter = mock.Mock()
        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            deprovision_domain_task("twice.example")
            deprovision_domain_task("twice.example")

        self.assertEqual(adapter.delete_dkim_key.call_count, 2)
        self.assertEqual(adapter.delete_domain.call_count, 2)
