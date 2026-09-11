"""
The storage quota contract: `default <= per-mailbox <= total`.

The Mail Engine enforces three storage numbers at once and refuses a domain
whose values contradict each other. MateMail shipped sending a hard-coded
domain total of `0` alongside a positive per-mailbox ceiling, on the assumption
that zero meant unlimited. It does not. The result was that **no domain could be
created at all** — every attempt returned
`mailbox_quota_exceeds_domain_quota`, and every mailbox call afterwards returned
`access_denied` because the domain did not exist.

Nothing caught it. The request-contract tests asserted which keys were sent, not
what the values meant, and the fake engine accepted any combination. So these
tests pin the relationship, at every layer that can break it:

    Plan  ->  DomainSpec  ->  the wire  ->  the engine's own rule

The architecture point the last class defends: the total is **carried, never
derived**. `total = mailboxes x ceiling` is one valid plan shape, not the only
one, and baking it into the adapter would foreclose shared-pool plans.
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase

from apps.mail_engine.dto import DomainSpec
from apps.mail_engine.errors import InvalidStorageConfiguration, MailEngineError
from apps.mail_engine.mailcow_adapter import MailcowAdapter
from tests.fake_engine import build_mailcow_adapter

#: The Private Beta policy: free, admin-approved, 1 GB mailboxes.
BETA_MB = 1024


def spec(**kw):
    base = {
        "name": "quota.example",
        "max_mailboxes": 10,
        "default_quota_mb": BETA_MB,
        "max_quota_mb": BETA_MB,
        "total_quota_mb": 10 * BETA_MB,
    }
    base.update(kw)
    return DomainSpec(**base)


class BetaPlanSpecTest(SimpleTestCase):
    def test_the_beta_plan_shape_is_valid(self):
        s = spec()
        self.assertEqual(s.default_quota_mb, 1024)
        self.assertEqual(s.max_quota_mb, 1024)
        self.assertEqual(s.total_quota_mb, 10240)

    def test_the_spec_carries_an_explicit_total(self):
        """Not a derived value, and not absent — a field the plan sets."""
        self.assertIn("total_quota_mb", DomainSpec.__dataclass_fields__)

    def test_defaults_alone_are_internally_consistent(self):
        """
        A spec built with no plan must still satisfy the engine's rule, because
        provisioning falls back to these when a plan cannot be resolved.
        """
        s = DomainSpec(name="defaults.example")
        self.assertLessEqual(s.default_quota_mb, s.max_quota_mb)
        self.assertLessEqual(s.max_quota_mb, s.total_quota_mb)


class SpecValidationFailsClosedTest(SimpleTestCase):
    """
    Validation happens in the DTO, before any request is built, so an impossible
    plan is MateMail's error in MateMail's words rather than an engine rejection
    the customer sees seconds later.
    """

    def test_a_default_above_the_ceiling_is_refused(self):
        with self.assertRaises(InvalidStorageConfiguration):
            spec(default_quota_mb=4096, max_quota_mb=1024)

    def test_a_ceiling_above_the_total_is_refused(self):
        with self.assertRaises(InvalidStorageConfiguration):
            spec(max_quota_mb=51200, total_quota_mb=10240)

    def test_the_exact_shipped_bug_is_refused(self):
        """quota=0 with a positive ceiling — what production actually sent."""
        with self.assertRaises(InvalidStorageConfiguration):
            spec(max_quota_mb=51200, total_quota_mb=0)

    def test_zero_and_negative_values_are_refused(self):
        for field in ("default_quota_mb", "max_quota_mb", "total_quota_mb"):
            for bad in (0, -1):
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(InvalidStorageConfiguration):
                        spec(**{field: bad})

    def test_non_integer_values_are_refused(self):
        for bad in ("1024", 10.5, None, True):
            with self.subTest(value=bad):
                with self.assertRaises(InvalidStorageConfiguration):
                    spec(max_quota_mb=bad)

    def test_equal_values_are_allowed(self):
        """`<=`, not `<`. The beta plan sets default == ceiling deliberately."""
        s = spec(default_quota_mb=1024, max_quota_mb=1024, total_quota_mb=1024)
        self.assertEqual(s.max_quota_mb, 1024)

    def test_the_error_is_typed_and_customer_safe(self):
        """
        It must reach the same handler as every other engine failure, and must
        not leak the numbers or the engine's vocabulary to a customer.
        """
        try:
            spec(max_quota_mb=51200, total_quota_mb=1024)
        except InvalidStorageConfiguration as exc:
            self.assertIsInstance(exc, MailEngineError)
            message = str(exc)
            self.assertNotIn("51200", message)
            self.assertNotIn("quota_mb", message)
            for term in ("mailcow", "maxquota", "defquota", "postfix"):
                self.assertNotIn(term, message.lower())
            # The detail is kept for the log, just not for the customer.
            self.assertIn("51200", exc.technical_detail)
        else:
            self.fail("an impossible spec was accepted")

    def test_nothing_is_silently_clamped(self):
        """
        Lowering a ceiling to fit a pool would quietly sell less than the plan
        promises. The contradiction has to surface.
        """
        with self.assertRaises(InvalidStorageConfiguration):
            spec(max_quota_mb=20480, total_quota_mb=10240)


class WireContractTest(SimpleTestCase):
    """What actually reaches the engine."""

    def _payload(self, s):
        adapter = MailcowAdapter()
        with mock.patch.object(adapter, "_write") as write:
            adapter.ensure_domain(s)
        return write.call_args[0][1]

    def test_the_domain_total_comes_from_the_spec(self):
        payload = self._payload(spec(total_quota_mb=10240))
        self.assertEqual(payload["quota"], 10240)

    def test_all_three_storage_fields_are_sent(self):
        payload = self._payload(spec())
        self.assertEqual(payload["defquota"], 1024)
        self.assertEqual(payload["maxquota"], 1024)
        self.assertEqual(payload["quota"], 10240)

    def test_the_domain_quota_is_never_hard_coded_to_zero(self):
        payload = self._payload(spec(total_quota_mb=25600))
        self.assertNotEqual(payload["quota"], 0)
        self.assertEqual(payload["quota"], 25600)

    def test_the_adapter_source_contains_no_zero_quota_literal(self):
        import inspect

        import apps.mail_engine.mailcow_adapter as module

        source = inspect.getsource(module.MailcowAdapter.ensure_domain)
        self.assertNotIn('"quota": 0', source)

    def test_the_reconcile_path_also_sends_the_total(self):
        """
        A plan upgrade raising the ceiling above the old pool would otherwise
        recreate the contradiction on an existing domain.
        """
        from apps.mail_engine.errors import AlreadyExists

        adapter = MailcowAdapter()
        calls = []

        def fake_write(path, payload, **kw):
            calls.append((path, payload))
            if path.endswith("/add/domain"):
                raise AlreadyExists("exists", operation="ensure_domain")

        with mock.patch.object(adapter, "_write", side_effect=fake_write):
            adapter.ensure_domain(spec(total_quota_mb=25600, max_quota_mb=5120))

        edit = [p for path, p in calls if path.endswith("/edit/domain")]
        self.assertTrue(edit, "no reconcile call was made")
        self.assertEqual(edit[0]["attr"]["quota"], 25600)
        self.assertEqual(edit[0]["attr"]["maxquota"], 5120)


class FakeEngineEnforcesTheInvariantTest(SimpleTestCase):
    """
    The double must refuse what the real engine refuses. A fake that accepts
    more than production turns a loud outage into a green suite — which is
    precisely what happened here.
    """

    def setUp(self):
        self.adapter, self.session = build_mailcow_adapter()

    def _add(self, **body):
        payload = {
            "domain": "fake.example", "description": "t", "aliases": 400,
            "mailboxes": 10, "defquota": 1024, "maxquota": 1024, "quota": 10240,
            "active": "1", "relay_all_recipients": "0", "relay_unknown_only": "0",
            "dkim_selector": "mm1", "key_size": 2048,
        }
        payload.update(body)
        return self.session._route("POST", "/api/v1/add/domain", payload)

    def _rejected(self, response):
        payload = response.json()
        items = payload if isinstance(payload, list) else [payload]
        return any(str(i.get("type", "")).lower() in ("error", "danger") for i in items)

    def _message(self, response):
        payload = response.json()
        items = payload if isinstance(payload, list) else [payload]
        return " ".join(str(i.get("msg", "")) for i in items)

    def test_a_valid_combination_is_accepted(self):
        self.assertFalse(self._rejected(self._add()))

    def test_zero_total_with_a_positive_ceiling_is_rejected(self):
        """The exact shipped bug."""
        response = self._add(quota=0, maxquota=1024)
        self.assertTrue(self._rejected(response))
        self.assertIn("mailbox_quota_exceeds_domain_quota", self._message(response))

    def test_a_default_above_the_ceiling_is_rejected(self):
        response = self._add(defquota=3072, maxquota=1024, quota=10240)
        self.assertTrue(self._rejected(response))
        self.assertIn("mailbox_defquota_exceeds_mailbox_maxquota", self._message(response))

    def test_a_ceiling_above_the_total_is_rejected(self):
        response = self._add(maxquota=51200, quota=10240)
        self.assertTrue(self._rejected(response))
        self.assertIn("mailbox_quota_exceeds_domain_quota", self._message(response))

    def test_negative_values_are_rejected(self):
        for field in ("defquota", "maxquota", "quota"):
            with self.subTest(field=field):
                self.assertTrue(self._rejected(self._add(**{field: -1})))

    def test_a_domain_is_not_created_when_the_quotas_are_rejected(self):
        self._add(quota=0, maxquota=1024)
        self.assertNotIn("fake.example", self.session.domains)

    def test_the_real_adapter_surfaces_the_rejection_as_a_typed_error(self):
        """End to end through the genuine adapter, not a mock."""
        from apps.mail_engine.errors import QuotaExceeded

        # Bypass DomainSpec validation to prove the adapter still copes if a
        # bad combination somehow reaches the wire.
        bad = spec()
        object.__setattr__(bad, "total_quota_mb", 512)
        with self.assertRaises(QuotaExceeded):
            self.adapter.ensure_domain(bad)


class TotalIsNotDerivedTest(SimpleTestCase):
    """
    The architecture claim: a shared pool smaller than mailboxes x ceiling is a
    legitimate plan shape and must work. If the total were ever derived in the
    adapter, this is the test that would fail.
    """

    def test_a_shared_pool_plan_is_valid(self):
        s = spec(max_mailboxes=10, default_quota_mb=1024,
                 max_quota_mb=5120, total_quota_mb=25600)
        self.assertEqual(s.total_quota_mb, 25600)
        # Deliberately NOT 10 * 5120 = 51200.
        self.assertLess(s.total_quota_mb, s.max_mailboxes * s.max_quota_mb)

    def test_the_shared_pool_reaches_the_wire_unchanged(self):
        adapter = MailcowAdapter()
        s = spec(max_mailboxes=10, default_quota_mb=1024,
                 max_quota_mb=5120, total_quota_mb=25600)
        with mock.patch.object(adapter, "_write") as write:
            adapter.ensure_domain(s)
        payload = write.call_args[0][1]
        self.assertEqual(payload["quota"], 25600)
        self.assertEqual(payload["maxquota"], 5120)


class PlanDerivedSpecTest(TestCase):
    """`DomainSpec.from_model` must take all three numbers from the plan."""

    def _plan(self, **kw):
        from apps.billing.models import Plan

        defaults = {
            "tier": "trial", "display_name": "Free Trial", "price_monthly": 0,
            "max_domains": 2, "max_mailboxes": 5, "max_members": 3,
            "default_storage_per_mailbox_mb": 1024,
            "max_storage_per_mailbox_mb": 1024,
            "max_storage_total_mb": 5120,
        }
        defaults.update(kw)
        plan, _ = Plan.objects.update_or_create(
            tier=defaults["tier"], defaults=defaults
        )
        return plan

    def _domain(self):
        from tests.factories import make_domain, make_tenant, make_user

        user = make_user("plan@example.com")
        return make_domain(make_tenant(user), "plan.example")

    def test_all_three_numbers_come_from_the_plan(self):
        plan = self._plan(max_mailboxes=10, default_storage_per_mailbox_mb=1024,
                          max_storage_per_mailbox_mb=5120,
                          max_storage_total_mb=25600)
        s = DomainSpec.from_model(self._domain(), plan=plan)

        self.assertEqual(s.max_mailboxes, 10)
        self.assertEqual(s.default_quota_mb, 1024)
        self.assertEqual(s.max_quota_mb, 5120)
        self.assertEqual(s.total_quota_mb, 25600)

    def test_the_seeded_beta_plan_produces_a_valid_spec(self):
        plan = self._plan()
        s = DomainSpec.from_model(self._domain(), plan=plan)
        self.assertLessEqual(s.default_quota_mb, s.max_quota_mb)
        self.assertLessEqual(s.max_quota_mb, s.total_quota_mb)

    def test_an_impossible_plan_fails_closed_rather_than_reaching_the_engine(self):
        """
        The old shape: a 512 MB ceiling with the dataclass default of 3072 MB.
        It must now be impossible to build a spec from, rather than producing a
        request the engine refuses.
        """
        plan = self._plan(default_storage_per_mailbox_mb=3072,
                          max_storage_per_mailbox_mb=512,
                          max_storage_total_mb=5120)
        with self.assertRaises(InvalidStorageConfiguration):
            DomainSpec.from_model(self._domain(), plan=plan)


class ProvisioningRecordsTheFailureTest(TestCase):
    """
    An impossible plan must land in the same place as every other provisioning
    failure — recorded on the domain, not raised as an unhandled exception that
    leaves a customer staring at a stuck domain.
    """

    def test_the_task_records_a_customer_safe_reason(self):
        from apps.mail_engine.tasks import provision_domain_task
        from tests.factories import make_domain, make_tenant, make_user

        user = make_user("prov@example.com")
        domain = make_domain(make_tenant(user), "impossible.example")

        bad_plan = mock.Mock()
        bad_plan.max_mailboxes = 5
        bad_plan.default_storage_per_mailbox_mb = 3072
        bad_plan.max_storage_per_mailbox_mb = 512
        bad_plan.max_storage_total_mb = 5120

        with mock.patch("apps.billing.utils.get_plan", return_value=bad_plan):
            provision_domain_task(str(domain.id))

        domain.refresh_from_db()
        self.assertFalse(domain.mail_engine_provisioned)
        self.assertNotEqual(domain.mail_engine_error, "")
        self.assertNotIn("512", domain.mail_engine_error)
