"""
Forwarding product logic lives above the adapter, not inside it.

Before P1, `MailcowAdapter.provision_forwarding` queried `ForwardingRule` and
implemented `keep_copy` itself. These tests assert the rules now resolve in
`apps.forwarding.services` and that the adapter receives a finished instruction
it applies verbatim.
"""
import inspect
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.forwarding.models import ForwardingRule, ForwardingStatus
from apps.forwarding.services import apply_forwarding, resolve_forwarding_spec
from apps.mail_engine.dto import ForwardingSpec
from apps.mail_engine.errors import EngineUnavailable
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
        "LOCATION": "forwarding-tests",
    }
}


class AdapterPurityTest(TestCase):
    """The adapter layer must not import or query MateMail product models."""

    def test_adapter_modules_do_not_import_product_models(self):
        from apps.mail_engine import adapter, dto, errors, mailcow_adapter, stub_adapter

        for module in (adapter, dto, errors, mailcow_adapter, stub_adapter):
            source = inspect.getsource(module)
            with self.subTest(module=module.__name__):
                for forbidden in (
                    "from apps.forwarding",
                    "from apps.domains.models",
                    "from apps.mailboxes.models",
                    "ForwardingRule",
                    "ForwardingStatus",
                ):
                    self.assertNotIn(
                        forbidden, source,
                        f"{module.__name__} reaches into product code: {forbidden}",
                    )

    def test_forwarding_helpers_are_gone_from_the_port(self):
        from apps.mail_engine.adapter import MailEngineAdapter

        for removed in ("provision_forwarding", "delete_forwarding"):
            self.assertFalse(
                hasattr(MailEngineAdapter, removed),
                f"{removed} should have been replaced by ensure_forwarding",
            )
        self.assertTrue(hasattr(MailEngineAdapter, "ensure_forwarding"))

    def test_provision_result_no_longer_exists(self):
        """Success is the absence of an exception; there is no result object."""
        from apps.mail_engine import adapter

        self.assertFalse(hasattr(adapter, "ProvisionResult"))


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class ForwardingResolutionTest(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("fwd@example.test")
        self.tenant = make_tenant(self.owner, name="Fwd Co", slug="fwd-co")
        self.domain = make_domain(self.tenant, "fwd.example")
        self.mailbox = make_mailbox(self.tenant, self.domain, "alice")

    def _rule(self, destination, *, keep_copy=True, status=ForwardingStatus.ACTIVE):
        return ForwardingRule.objects.create(
            tenant=self.tenant,
            source_mailbox=self.mailbox,
            destination_email=destination,
            keep_copy=keep_copy,
            status=status,
        )

    def test_no_rules_yields_empty_spec(self):
        spec = resolve_forwarding_spec(self.mailbox)
        self.assertIsInstance(spec, ForwardingSpec)
        self.assertTrue(spec.is_empty)

    def test_single_rule_with_keep_copy_includes_the_mailbox(self):
        self._rule("out@elsewhere.example", keep_copy=True)
        spec = resolve_forwarding_spec(self.mailbox)
        self.assertIn("out@elsewhere.example", spec.destinations)
        self.assertIn(self.mailbox.email, spec.destinations)

    def test_single_rule_without_keep_copy_excludes_the_mailbox(self):
        self._rule("out@elsewhere.example", keep_copy=False)
        spec = resolve_forwarding_spec(self.mailbox)
        self.assertEqual(spec.destinations, ("out@elsewhere.example",))

    def test_any_rule_with_keep_copy_keeps_the_copy(self):
        self._rule("a@elsewhere.example", keep_copy=False)
        self._rule("b@elsewhere.example", keep_copy=True)
        spec = resolve_forwarding_spec(self.mailbox)
        self.assertIn(self.mailbox.email, spec.destinations)

    def test_paused_and_disabled_rules_are_excluded(self):
        self._rule("active@elsewhere.example", keep_copy=False)
        self._rule("paused@elsewhere.example", keep_copy=False, status=ForwardingStatus.PAUSED)
        self._rule("off@elsewhere.example", keep_copy=False, status=ForwardingStatus.DISABLED)
        spec = resolve_forwarding_spec(self.mailbox)
        self.assertEqual(spec.destinations, ("active@elsewhere.example",))

    def test_destinations_are_deduplicated_and_ordered(self):
        self._rule("dup@elsewhere.example", keep_copy=False)
        ForwardingRule.objects.create(
            tenant=self.tenant,
            source_mailbox=self.mailbox,
            destination_email="aaa@elsewhere.example",
            keep_copy=False,
            status=ForwardingStatus.ACTIVE,
        )
        spec = resolve_forwarding_spec(self.mailbox)
        self.assertEqual(spec.destinations, tuple(sorted(set(spec.destinations))))

    def test_spec_is_stable_across_calls(self):
        """Stability is what makes re-applying safe."""
        self._rule("a@elsewhere.example")
        self._rule("b@elsewhere.example", keep_copy=False)
        self.assertEqual(
            resolve_forwarding_spec(self.mailbox), resolve_forwarding_spec(self.mailbox)
        )

    def test_excluding_rule_predicts_post_delete_state(self):
        keep = self._rule("keep@elsewhere.example", keep_copy=False)
        drop = self._rule("drop@elsewhere.example", keep_copy=False)
        spec = resolve_forwarding_spec(self.mailbox, excluding_rule_pk=drop.pk)
        self.assertEqual(spec.destinations, ("keep@elsewhere.example",))
        self.assertTrue(ForwardingRule.objects.filter(pk=keep.pk).exists())

    def test_excluding_the_only_rule_yields_empty_spec(self):
        only = self._rule("only@elsewhere.example")
        spec = resolve_forwarding_spec(self.mailbox, excluding_rule_pk=only.pk)
        self.assertTrue(spec.is_empty)

    def test_apply_forwarding_hands_the_adapter_a_finished_spec(self):
        self._rule("out@elsewhere.example", keep_copy=True)
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_forwarding"
        ) as ensure:
            apply_forwarding(self.mailbox)
        ensure.assert_called_once()
        (spec,), _ = ensure.call_args
        self.assertIsInstance(spec, ForwardingSpec)
        self.assertEqual(spec.mailbox_address, self.mailbox.email)
        self.assertIn("out@elsewhere.example", spec.destinations)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class ForwardingApiTest(TestCase):
    """The API must not report success the customer's mail flow will not reflect."""

    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("fwdapi@example.test")
        self.tenant = make_tenant(self.owner, name="FwdApi", slug="fwd-api")
        self.domain = make_domain(self.tenant, "fwdapi.example")
        self.mailbox = make_mailbox(self.tenant, self.domain, "alice")
        self.client_api = auth_client(self.owner, self.tenant)

    def _create(self, destination="out@elsewhere.example"):
        return self.client_api.post(
            "/api/forwarding/",
            {
                "source_mailbox_id": str(self.mailbox.id),
                "destination_email": destination,
                "keep_copy": True,
            },
            format="json",
        )

    def test_create_succeeds_when_engine_accepts(self):
        res = self._create()
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["mail_service_ready"])

    def test_create_reports_pending_when_engine_is_unavailable(self):
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_forwarding",
            side_effect=EngineUnavailable("engine down"),
        ):
            res = self._create()
        self.assertEqual(res.status_code, 202)
        self.assertFalse(res.data["mail_service_ready"])

    def test_delete_is_refused_when_engine_is_unavailable(self):
        """
        Deleting our record while the engine still forwards would silently keep
        copying the customer's mail to a third party.
        """
        create = self._create()
        rule_id = create.data["id"]
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_forwarding",
            side_effect=EngineUnavailable("engine down"),
        ):
            res = self.client_api.delete(f"/api/forwarding/{rule_id}/")
        self.assertEqual(res.status_code, 503)
        self.assertTrue(ForwardingRule.objects.filter(pk=rule_id).exists())

    def test_delete_applies_remaining_rules(self):
        first = self._create("one@elsewhere.example")
        self._create("two@elsewhere.example")
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_forwarding"
        ) as ensure:
            res = self.client_api.delete(f"/api/forwarding/{first.data['id']}/")
        self.assertEqual(res.status_code, 204)
        (spec,), _ = ensure.call_args
        self.assertNotIn("one@elsewhere.example", spec.destinations)
        self.assertIn("two@elsewhere.example", spec.destinations)

    def test_pausing_a_rule_reapplies_the_remaining_set(self):
        first = self._create("one@elsewhere.example")
        self._create("two@elsewhere.example")
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_forwarding"
        ) as ensure:
            res = self.client_api.patch(
                f"/api/forwarding/{first.data['id']}/status/",
                {"status": "paused"},
                format="json",
            )
        self.assertEqual(res.status_code, 200)
        (spec,), _ = ensure.call_args
        self.assertNotIn("one@elsewhere.example", spec.destinations)

    def test_status_change_rolls_back_when_engine_rejects(self):
        created = self._create()
        rule_id = created.data["id"]
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_forwarding",
            side_effect=EngineUnavailable("engine down"),
        ):
            res = self.client_api.patch(
                f"/api/forwarding/{rule_id}/status/", {"status": "paused"}, format="json"
            )
        self.assertEqual(res.status_code, 503)
        ForwardingRule.objects.get(pk=rule_id).refresh_from_db()
        self.assertEqual(
            ForwardingRule.objects.get(pk=rule_id).status, ForwardingStatus.ACTIVE
        )
