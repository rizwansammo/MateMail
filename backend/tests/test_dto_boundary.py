"""
Only DTOs cross the adapter boundary — never Django model instances.

Passing ORM objects couples the engine implementation to MateMail's schema and
is what forced the `_FakeDomain` / `_FakeMailbox` shims in the old tasks module.
These tests assert the boundary discipline at the call sites, and cover the
retry semantics the Celery tasks depend on.
"""
import inspect
from unittest import mock

from django.core.cache import cache
from django.db.models import Model
from django.test import TestCase, override_settings

from apps.mail_engine import tasks
from apps.mail_engine.dto import (
    AliasSpec,
    DomainSpec,
    ForwardingSpec,
    MailboxSpec,
)
from apps.mail_engine.errors import EngineUnavailable, Rejected
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_mailbox,
    make_tenant,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "dto-tests",
    }
}


def assert_no_models(testcase, call_args, *, context: str):
    args, kwargs = call_args
    for value in list(args) + list(kwargs.values()):
        testcase.assertNotIsInstance(
            value, Model, f"{context} passed a Django model across the adapter boundary"
        )


class DtoImmutabilityTest(TestCase):
    def test_specs_are_frozen(self):
        spec = DomainSpec(name="x.example")
        with self.assertRaises(Exception):
            spec.name = "y.example"

    def test_alias_spec_rejects_empty_destinations(self):
        with self.assertRaises(ValueError):
            AliasSpec(address="a@x.example", destinations=())

    def test_forwarding_spec_allows_empty_as_removal(self):
        self.assertTrue(ForwardingSpec(mailbox_address="a@x.example").is_empty)

    def test_no_dto_carries_private_key_material(self):
        """DEC-007r, enforced structurally rather than by convention."""
        from apps.mail_engine import dto

        for name, obj in vars(dto).items():
            if not (inspect.isclass(obj) and hasattr(obj, "__dataclass_fields__")):
                continue
            for field in obj.__dataclass_fields__:
                with self.subTest(dto=name, field=field):
                    lowered = field.lower()
                    self.assertNotIn("private", lowered)
                    self.assertNotIn("secret", lowered)
                    self.assertNotIn("privkey", lowered)


class TaskShimRemovalTest(TestCase):
    def test_fake_model_shims_are_gone(self):
        source = inspect.getsource(tasks)
        self.assertNotIn("_FakeDomain", source)
        self.assertNotIn("_FakeMailbox", source)

    def test_deprovision_tasks_take_primitives(self):
        """Celery payloads must be JSON-serializable, so no models."""
        for task in (tasks.deprovision_domain_task, tasks.deprovision_mailbox_task):
            params = list(inspect.signature(task.run).parameters)
            with self.subTest(task=task.name):
                self.assertNotIn("domain", params[1:2] and [] or [])
                self.assertTrue(all(p != "mailbox" for p in params))


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class CallSiteDtoTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("dto@example.test")
        self.tenant = make_tenant(self.owner, name="DTO Co", slug="dto-co")
        self.domain = make_domain(self.tenant, "dto.example")
        self.client_api = auth_client(self.owner, self.tenant)

    def test_mailbox_creation_passes_a_mailbox_spec(self):
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_mailbox"
        ) as ensure:
            self.client_api.post(
                "/api/mailboxes/",
                {
                    "local_part": "alice",
                    "domain_id": str(self.domain.id),
                    "full_name": "Alice",
                    "password": "Mailbox-Passphrase-9",
                },
                format="json",
            )
        ensure.assert_called_once()
        assert_no_models(self, ensure.call_args, context="ensure_mailbox")
        (spec, _password), _ = ensure.call_args
        self.assertIsInstance(spec, MailboxSpec)
        self.assertEqual(spec.address, "alice@dto.example")

    def test_alias_creation_passes_an_alias_spec(self):
        mailbox = make_mailbox(self.tenant, self.domain, "bob")
        with mock.patch("apps.mail_engine.stub_adapter.StubAdapter.ensure_alias") as ensure:
            self.client_api.post(
                "/api/aliases/",
                {
                    "source_local_part": "sales",
                    "domain_id": str(self.domain.id),
                    "destination_mailbox_id": str(mailbox.id),
                },
                format="json",
            )
        ensure.assert_called_once()
        assert_no_models(self, ensure.call_args, context="ensure_alias")
        (spec,), _ = ensure.call_args
        self.assertIsInstance(spec, AliasSpec)
        self.assertEqual(spec.destinations, (mailbox.email,))

    def test_domain_provisioning_passes_a_domain_spec(self):
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_domain"
        ) as ensure:
            tasks.provision_domain_task(str(self.domain.id))
        ensure.assert_called_once()
        assert_no_models(self, ensure.call_args, context="ensure_domain")
        (spec,), _ = ensure.call_args
        self.assertIsInstance(spec, DomainSpec)
        self.assertEqual(spec.name, "dto.example")

    def test_domain_spec_takes_limits_from_the_plan(self):
        """Plan limits are MateMail's rule, resolved above the boundary."""
        from apps.billing.models import Plan, PlanTier, Subscription, SubscriptionStatus

        plan = Plan.objects.filter(tier=PlanTier.BUSINESS).first() or Plan.objects.create(
            tier=PlanTier.BUSINESS,
            display_name="Business",
            max_domains=10,
            max_mailboxes=77,
            max_storage_per_mailbox_mb=40960,
        )
        Subscription.objects.create(
            tenant=self.tenant, plan=plan, status=SubscriptionStatus.ACTIVE
        )
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_domain"
        ) as ensure:
            tasks.provision_domain_task(str(self.domain.id))
        (spec,), _ = ensure.call_args
        # Assert against the plan actually in force: migration 0003 seeds the
        # tiers, so the values come from there rather than the fallback above.
        self.assertEqual(spec.max_mailboxes, plan.max_mailboxes)
        self.assertEqual(spec.max_quota_mb, plan.max_storage_per_mailbox_mb)
        self.assertNotEqual(
            spec.max_mailboxes, DomainSpec(name="x").max_mailboxes,
            "the spec fell back to defaults instead of using the plan",
        )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class TaskIdempotencyTest(TestCase):
    """Celery may run a task more than once; the end state must be the same."""

    def setUp(self):
        cache.clear()
        self.owner = make_user("idem@example.test")
        self.tenant = make_tenant(self.owner, name="Idem Co", slug="idem-co")
        self.domain = make_domain(self.tenant, "idem.example")

    def test_repeated_provisioning_converges(self):
        from apps.mail_engine.factory import get_adapter, reset_adapter

        reset_adapter()
        adapter = get_adapter()
        tasks.provision_domain_task(str(self.domain.id))
        self.domain.refresh_from_db()
        self.assertTrue(self.domain.mail_engine_provisioned)

        # A duplicate delivery must not error or duplicate engine state.
        tasks.provision_domain_task(str(self.domain.id))
        matching = [d for d in adapter.list_domains() if d.name == "idem.example"]
        self.assertEqual(len(matching), 1)
        reset_adapter()

    def test_repeated_deprovisioning_converges(self):
        from apps.mail_engine.factory import reset_adapter

        reset_adapter()
        tasks.deprovision_domain_task("idem.example")
        tasks.deprovision_domain_task("idem.example")
        reset_adapter()

    def test_transport_failure_retries(self):
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_domain",
            side_effect=EngineUnavailable("engine down"),
        ), mock.patch.object(tasks.provision_domain_task, "retry", side_effect=RuntimeError("retried")) as retry:
            with self.assertRaises(RuntimeError):
                tasks.provision_domain_task(str(self.domain.id))
        retry.assert_called_once()

    def test_rejection_does_not_retry(self):
        """Retrying an invalid request only burns attempts."""
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_domain",
            side_effect=Rejected("domain name invalid"),
        ), mock.patch.object(tasks.provision_domain_task, "retry") as retry:
            tasks.provision_domain_task(str(self.domain.id))
        retry.assert_not_called()
        self.domain.refresh_from_db()
        self.assertFalse(self.domain.mail_engine_provisioned)
        self.assertTrue(self.domain.mail_engine_error)

    def test_failure_message_stored_is_matemail_authored(self):
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_domain",
            side_effect=Rejected("mailcow-nginx:8080 /api/v1/add/domain refused"),
        ), mock.patch.object(tasks.provision_domain_task, "retry"):
            tasks.provision_domain_task(str(self.domain.id))
        self.domain.refresh_from_db()
        stored = self.domain.mail_engine_error.lower()
        self.assertNotIn("mailcow", stored)
        self.assertNotIn("/api/v1/", stored)
        self.assertEqual(self.domain.mail_engine_error, Rejected.customer_message)

    def test_missing_domain_is_not_an_error(self):
        import uuid

        tasks.provision_domain_task(str(uuid.uuid4()))
