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


class TotalIsNotDerivedTest(SimpleTestCase):
    """A shared storage pool is carried explicitly rather than derived."""

    def test_a_shared_pool_plan_is_valid(self):
        s = spec(
            max_mailboxes=10,
            default_quota_mb=1024,
            max_quota_mb=5120,
            total_quota_mb=25600,
        )
        self.assertEqual(s.total_quota_mb, 25600)
        self.assertLess(s.total_quota_mb, s.max_mailboxes * s.max_quota_mb)


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
