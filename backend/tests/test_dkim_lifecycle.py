"""
DKIM lifecycle against the engine's real semantics.

Every behaviour asserted here was measured against the running Mail Engine in
P4B, not inferred from documentation. Four defects were found, and all four
looked correct in code review:

1. Rotation called `add/dkim`, which the engine refuses whenever a key already
   exists — so rotation could never once have succeeded in production.
2. Domain creation omitted the selector, so the engine used its own default and
   MateMail would have published DNS for a selector it never chose.
3. A missing selector in the engine's reply was silently replaced with a guess,
   which would publish a record for a key nobody signs with.
4. Deleting a domain left its DKIM key in the engine. Re-registering the same
   domain adopted the surviving key — so a new tenant could inherit a previous
   tenant's private signing key.

The fourth is the reason this file exists. The others are the reason it is
thorough.
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from apps.mail_engine.dto import DkimKeyInfo, DomainSpec
from apps.mail_engine.errors import (
    EngineUnavailable,
    MailEngineError,
    NotFound,
    Rejected,
)
from apps.mail_engine.stub_adapter import StubAdapter
from tests.factories import make_domain, make_tenant, make_user


def _info(public="PUB", selector="mm1", domain="lifecycle.example"):
    return DkimKeyInfo(
        selector=selector,
        public_key=public,
        dns_record_name=f"{selector}._domainkey.{domain}",
        dns_record_value=f"v=DKIM1;k=rsa;p={public}",
    )


# ─── Deprovisioning: the cross-tenant stale-key scenario ─────────────────────


class DeprovisionRemovesDkimTest(SimpleTestCase):
    """
    `deprovision_domain_task` must remove the signing key, not just the domain.

    These use a mock adapter so the *call sequence* is observable. The end-state
    equivalent, exercised through a real adapter, is in
    `StaleKeyIsNotInheritedTest` below.
    """

    def _run(self, adapter, name="lifecycle.example", expect=None):
        from apps.mail_engine.tasks import deprovision_domain_task

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            if expect is not None:
                with self.assertRaises(expect):
                    deprovision_domain_task(name)
            else:
                deprovision_domain_task(name)

    def test_the_dkim_key_is_deleted(self):
        adapter = mock.Mock()
        self._run(adapter)
        adapter.delete_dkim_key.assert_called_once_with("lifecycle.example")
        adapter.delete_domain.assert_called_once_with("lifecycle.example")

    def test_the_key_is_deleted_before_the_domain(self):
        """
        Order matters. Failing after the domain is gone but before the key is
        leaves precisely the dangerous state; failing the other way round leaves
        an orphaned domain record, which is recoverable.
        """
        adapter = mock.Mock()
        order = []
        adapter.set_domain_active.side_effect = lambda *a: order.append("deactivate")
        adapter.delete_dkim_key.side_effect = lambda *a: order.append("dkim")
        adapter.delete_domain.side_effect = lambda *a: order.append("domain")

        self._run(adapter)

        self.assertEqual(order, ["deactivate", "dkim", "domain"])

    def test_an_already_absent_domain_still_has_its_key_deleted(self):
        """
        The state a previously failed run leaves behind: no domain, stale key.
        Short-circuiting here would make the cleanup permanently unable to
        repair itself.
        """
        adapter = mock.Mock()
        adapter.set_domain_active.side_effect = NotFound(
            "no such domain", operation="set_domain_active"
        )

        self._run(adapter)

        adapter.delete_dkim_key.assert_called_once_with("lifecycle.example")

    def test_the_engines_real_absent_domain_rejection_does_not_abort_cleanup(self):
        """
        Measured against the live engine: deactivating a domain it does not hold
        answers `domain_invalid`, which classifies as **Rejected**, not NotFound
        — "invalid" also covers a malformed name, so the mapping is right.

        Catching only NotFound was the obvious guess and was wrong: an
        already-absent domain would abort the task before the key was deleted,
        leaving exactly the stale key this whole path exists to remove.
        """
        adapter = mock.Mock()
        adapter.set_domain_active.side_effect = Rejected(
            "domain_invalid", operation="set_domain_active"
        )

        self._run(adapter)

        adapter.delete_dkim_key.assert_called_once_with("lifecycle.example")
        adapter.delete_domain.assert_called_once_with("lifecycle.example")

    def test_a_transport_failure_on_deactivate_still_retries(self):
        """Tolerating rejections must not also swallow a genuine outage."""
        adapter = mock.Mock()
        adapter.set_domain_active.side_effect = EngineUnavailable(
            "engine down", operation="set_domain_active"
        )
        from apps.mail_engine.tasks import deprovision_domain_task

        with mock.patch(
            "apps.mail_engine.factory.get_adapter", return_value=adapter
        ), mock.patch.object(
            deprovision_domain_task, "retry", side_effect=RuntimeError("retried")
        ) as retry:
            with self.assertRaises(RuntimeError):
                deprovision_domain_task("lifecycle.example")

        retry.assert_called_once()

    def test_an_already_absent_key_is_not_an_error(self):
        adapter = mock.Mock()
        adapter.delete_dkim_key.return_value = None
        self._run(adapter)
        adapter.delete_domain.assert_called_once()

    def test_a_transient_dkim_failure_retries_rather_than_reporting_success(self):
        adapter = mock.Mock()
        adapter.delete_dkim_key.side_effect = EngineUnavailable(
            "engine down", operation="delete_dkim_key"
        )
        from apps.mail_engine.tasks import deprovision_domain_task

        with mock.patch(
            "apps.mail_engine.factory.get_adapter", return_value=adapter
        ), mock.patch.object(
            deprovision_domain_task, "retry", side_effect=RuntimeError("retried")
        ) as retry:
            with self.assertRaises(RuntimeError):
                deprovision_domain_task("lifecycle.example")

        retry.assert_called_once()
        adapter.delete_domain.assert_not_called()

    def test_a_terminal_dkim_failure_raises_rather_than_returning_quietly(self):
        """
        A silent return would report clean removal while a usable signing key
        for a domain MateMail no longer controls stayed in the engine.
        """
        adapter = mock.Mock()
        adapter.delete_dkim_key.side_effect = Rejected(
            "refused", operation="delete_dkim_key"
        )
        self._run(adapter, expect=Rejected)
        adapter.delete_domain.assert_not_called()

    def test_a_transient_domain_failure_after_key_cleanup_retries(self):
        adapter = mock.Mock()
        adapter.delete_domain.side_effect = EngineUnavailable(
            "engine down", operation="delete_domain"
        )
        from apps.mail_engine.tasks import deprovision_domain_task

        with mock.patch(
            "apps.mail_engine.factory.get_adapter", return_value=adapter
        ), mock.patch.object(
            deprovision_domain_task, "retry", side_effect=RuntimeError("retried")
        ) as retry:
            with self.assertRaises(RuntimeError):
                deprovision_domain_task("lifecycle.example")

        adapter.delete_dkim_key.assert_called_once()
        retry.assert_called_once()

    def test_a_retry_after_partial_cleanup_converges(self):
        """
        Run the task twice, the first time failing at the domain step. Both
        operations are idempotent, so the second run completes without
        complaining about anything already gone.
        """
        adapter = mock.Mock()
        adapter.delete_domain.side_effect = [
            EngineUnavailable("engine down", operation="delete_domain"),
            None,
        ]
        from apps.mail_engine.tasks import deprovision_domain_task

        with mock.patch(
            "apps.mail_engine.factory.get_adapter", return_value=adapter
        ), mock.patch.object(deprovision_domain_task, "retry", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                deprovision_domain_task("lifecycle.example")
            deprovision_domain_task("lifecycle.example")

        self.assertEqual(adapter.delete_dkim_key.call_count, 2)


class StaleKeyIsNotInheritedTest(SimpleTestCase):
    """
    The end-to-end property, through a real adapter rather than a mock: after
    deprovisioning, re-registering the same domain produces a NEW signing key.
    """

    def setUp(self):
        self.adapter = StubAdapter()

    def _deprovision(self, name):
        from apps.mail_engine.tasks import deprovision_domain_task

        with mock.patch(
            "apps.mail_engine.factory.get_adapter", return_value=self.adapter
        ):
            deprovision_domain_task(name)

    def test_tenant_b_does_not_inherit_tenant_a_signing_key(self):
        name = "changes-hands.example"

        # Tenant A registers the domain and is issued a key.
        self.adapter.ensure_domain(DomainSpec(name=name, dkim_selector="tenanta"))
        tenant_a_key = self.adapter.get_dkim_public_key(name).public_key

        # Tenant A gives the domain up.
        self._deprovision(name)
        self.assertIsNone(
            self.adapter.get_dkim_public_key(name),
            "deprovisioning left a signing key behind",
        )

        # Tenant B legitimately registers the same domain later.
        self.adapter.ensure_domain(DomainSpec(name=name, dkim_selector="tenantb"))
        tenant_b_key = self.adapter.get_dkim_public_key(name)

        self.assertNotEqual(
            tenant_b_key.public_key, tenant_a_key,
            "tenant B inherited tenant A's DKIM signing key",
        )
        self.assertEqual(tenant_b_key.selector, "tenantb")

    def test_without_the_key_deletion_the_leak_would_happen(self):
        """
        A guard on the guard. If `delete_dkim_key` is ever dropped from the
        deprovisioning path, this documents exactly what comes back.
        """
        name = "control.example"
        self.adapter.ensure_domain(DomainSpec(name=name, dkim_selector="tenanta"))
        leaked = self.adapter.get_dkim_public_key(name).public_key

        self.adapter.delete_domain(name)  # domain only — the old behaviour
        self.adapter.ensure_domain(DomainSpec(name=name, dkim_selector="tenantb"))

        self.assertEqual(
            self.adapter.get_dkim_public_key(name).public_key, leaked,
            "the hazard this file exists for is no longer reproducible — the "
            "engine's behaviour changed and deprovisioning should be re-checked",
        )


# ─── Provisioning adopts the auto-generated key ──────────────────────────────


class ProvisioningAdoptsTheGeneratedKeyTest(SimpleTestCase):
    """
    Creating a domain mints its key, so provisioning reads rather than
    generates. A second generation attempt would be refused by the engine and
    would fail provisioning outright.
    """

    def _domain(self, selector="mm1"):
        d = mock.Mock()
        d.domain = "provision.example"
        d.dkim_selector = selector
        d.dkim_public_key = ""
        return d

    def test_no_second_key_is_requested_after_domain_creation(self):
        from apps.mail_engine.tasks import _adopt_engine_dkim

        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = _info("AUTOGENERATED")

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            _adopt_engine_dkim(self._domain())

        adapter.rotate_dkim_key.assert_not_called()

    def test_generation_still_runs_if_the_engine_somehow_holds_no_key(self):
        from apps.mail_engine.tasks import _adopt_engine_dkim

        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = None
        adapter.rotate_dkim_key.return_value = _info("RECOVERED")
        domain = self._domain()

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            _adopt_engine_dkim(domain)

        adapter.rotate_dkim_key.assert_called_once()
        self.assertEqual(domain.dkim_public_key, "RECOVERED")

    def test_the_selector_the_engine_reports_is_the_one_recorded(self):
        """
        MateMail publishes what the engine says it signs with — never what
        MateMail asked for. If the two ever disagree, the engine wins, because
        the engine is what actually signs.
        """
        from apps.mail_engine.tasks import _adopt_engine_dkim

        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = _info("PUB", selector="enginechose")
        domain = self._domain(selector="werequested")

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            _adopt_engine_dkim(domain)

        self.assertEqual(domain.dkim_selector, "enginechose")


# ─── Malformed engine replies fail closed ────────────────────────────────────


# ─── Rotation: concurrency and failure ───────────────────────────────────────


class RotationServiceTest(TestCase):
    def setUp(self):
        user = make_user("rot@example.com")
        self.tenant = make_tenant(user)
        self.domain = make_domain(self.tenant, "rotate.example")
        self.domain.dkim_selector = "mm1"
        self.domain.dkim_public_key = "ORIGINAL"
        self.domain.save(update_fields=["dkim_selector", "dkim_public_key"])

    def test_rotation_records_the_new_public_material(self):
        from apps.mail_engine.services import rotate_domain_dkim

        adapter = mock.Mock()
        adapter.rotate_dkim_key.return_value = _info("ROTATED", domain="rotate.example")

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            rotate_domain_dkim(self.domain.id)

        self.domain.refresh_from_db()
        self.assertEqual(self.domain.dkim_public_key, "ROTATED")
        self.assertEqual(self.domain.mail_engine_error, "")

    def test_rotation_serializes_on_the_domain_row(self):
        """
        Two concurrent rotations for one domain interleave destructively: the
        second can delete the key the first just created, and both then persist
        a public key the engine no longer holds.
        """
        from apps.mail_engine.services import rotate_domain_dkim

        adapter = mock.Mock()
        adapter.rotate_dkim_key.return_value = _info("ROTATED", domain="rotate.example")

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter), \
             mock.patch("apps.domains.models.Domain.objects") as manager:
            manager.select_for_update.return_value.get.return_value = self.domain
            rotate_domain_dkim(self.domain.id)

        manager.select_for_update.assert_called_once()

    def test_a_failed_rotation_records_a_customer_safe_reason(self):
        from apps.mail_engine.services import rotate_domain_dkim

        adapter = mock.Mock()
        adapter.rotate_dkim_key.side_effect = Rejected(
            "engine said no", operation="rotate_dkim_key"
        )

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            with self.assertRaises(Rejected):
                rotate_domain_dkim(self.domain.id)

        self.domain.refresh_from_db()
        self.assertNotEqual(self.domain.mail_engine_error, "")
        self.assertNotIn("engine said no", self.domain.mail_engine_error)
        # The previously published record is left alone for the operator to see.
        self.assertEqual(self.domain.dkim_public_key, "ORIGINAL")

    def test_unusable_material_after_rotation_fails_closed(self):
        from apps.mail_engine.services import rotate_domain_dkim

        adapter = mock.Mock()
        adapter.rotate_dkim_key.return_value = _info("", domain="rotate.example")

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            with self.assertRaises(MailEngineError):
                rotate_domain_dkim(self.domain.id)

        self.domain.refresh_from_db()
        self.assertEqual(self.domain.dkim_public_key, "ORIGINAL")
        self.assertNotEqual(self.domain.mail_engine_error, "")

    def test_no_private_key_is_written_by_rotation(self):
        from apps.mail_engine.services import rotate_domain_dkim

        adapter = mock.Mock()
        adapter.rotate_dkim_key.return_value = _info("ROTATED", domain="rotate.example")

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            rotate_domain_dkim(self.domain.id)

        self.domain.refresh_from_db()
        self.assertEqual(self.domain.dkim_private_key, "")


class RotationIsNotOnARetryPathTest(SimpleTestCase):
    """
    Rotation invalidates the DNS record the customer has already published.
    Celery retries; a rotation on a retry path would mint a new key on every
    attempt and leave DNS permanently stale. It is therefore deliberately a
    synchronous service, not a task.
    """

    def test_no_celery_task_wraps_the_rotation_service(self):
        import apps.mail_engine.tasks as tasks

        names = [n for n in dir(tasks) if "rotate" in n.lower()]
        self.assertEqual(
            names, [],
            f"rotation must not be a retryable task, found: {names}",
        )

    def test_the_services_module_declares_no_shared_task(self):
        import inspect

        import apps.mail_engine.services as services

        self.assertNotIn("shared_task", inspect.getsource(services))


# ─── Spec carries DKIM settings, engine-neutrally ────────────────────────────


class DomainSpecCarriesDkimSettingsTest(TestCase):
    def test_defaults_are_engine_neutral_and_sane(self):
        spec = DomainSpec(name="spec.example")
        self.assertEqual(spec.dkim_key_size, 2048)
        self.assertTrue(spec.dkim_selector)

    @override_settings(DKIM_SELECTOR="deployment7")
    def test_from_model_falls_back_to_the_configured_selector(self):
        user = make_user("spec@example.com")
        tenant = make_tenant(user)
        domain = make_domain(tenant, "spec.example")
        domain.dkim_selector = ""
        domain.save(update_fields=["dkim_selector"])

        spec = DomainSpec.from_model(domain)

        self.assertEqual(
            spec.dkim_selector, "deployment7",
            "a hard-coded fallback disagrees with DKIM_SELECTOR the moment it changes",
        )

    def test_a_domains_own_selector_wins_over_the_default(self):
        user = make_user("spec2@example.com")
        tenant = make_tenant(user)
        domain = make_domain(tenant, "spec2.example")
        domain.dkim_selector = "perdomain"
        domain.save(update_fields=["dkim_selector"])

        self.assertEqual(DomainSpec.from_model(domain).dkim_selector, "perdomain")
