"""
Shared Mail Engine adapter contract.

`AdapterContractTests` is a mixin of behaviour every implementation must honour.
It is run twice — once against `StubAdapter`, once against a real
`MailcowAdapter` wired to an in-memory fake of the engine's REST API. That is
what stops the two drifting: a change that satisfies only one implementation
fails here.

The contract covers what `adapter.py` documents: DTO in / DTO out, typed errors,
and the per-method idempotency guarantees.
"""
from django.test import SimpleTestCase

from apps.mail_engine.dto import (
    AliasSpec,
    DkimKeyInfo,
    DomainSpec,
    EngineDomain,
    EngineHealth,
    EngineMailbox,
    ForwardingSpec,
    MailboxSpec,
    MailboxUsage,
)
from apps.mail_engine.errors import EngineUnavailable, MailEngineError, Rejected
from apps.mail_engine.stub_adapter import StubAdapter
from tests.fake_engine import build_mailcow_adapter

DOMAIN = "contract.example"
ADDRESS = f"alice@{DOMAIN}"


def domain_spec(name=DOMAIN, **kw) -> DomainSpec:
    return DomainSpec(name=name, **kw)


def mailbox_spec(address=ADDRESS, **kw) -> MailboxSpec:
    local, _, dom = address.partition("@")
    defaults = {"local_part": local, "domain": dom, "display_name": "Alice", "quota_mb": 2048}
    defaults.update(kw)
    return MailboxSpec(address=address, **defaults)


class AdapterContractTests:
    """Mixin. Subclasses set `self.adapter` in setUp."""

    # ── Domains ─────────────────────────────────────────────────────────────

    def test_ensure_domain_creates(self):
        self.adapter.ensure_domain(domain_spec())
        self.assertIn(DOMAIN, [d.name for d in self.adapter.list_domains()])

    def test_ensure_domain_is_idempotent(self):
        spec = domain_spec()
        self.adapter.ensure_domain(spec)
        self.adapter.ensure_domain(spec)
        self.adapter.ensure_domain(spec)
        matching = [d for d in self.adapter.list_domains() if d.name == DOMAIN]
        self.assertEqual(len(matching), 1, "repeated ensure_domain duplicated the domain")

    def test_ensure_domain_reconciles_changed_spec(self):
        self.adapter.ensure_domain(domain_spec(max_mailboxes=5))
        self.adapter.ensure_domain(domain_spec(max_mailboxes=50))
        self.assertEqual(len([d for d in self.adapter.list_domains() if d.name == DOMAIN]), 1)

    def test_set_domain_active_is_assignment(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.set_domain_active(DOMAIN, False)
        self.adapter.set_domain_active(DOMAIN, False)  # same value: no-op success
        found = next(d for d in self.adapter.list_domains() if d.name == DOMAIN)
        self.assertFalse(found.active)
        self.adapter.set_domain_active(DOMAIN, True)
        found = next(d for d in self.adapter.list_domains() if d.name == DOMAIN)
        self.assertTrue(found.active)

    def test_delete_domain_is_idempotent(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.delete_domain(DOMAIN)
        # Deleting again must succeed: the caller's intent is already satisfied.
        self.adapter.delete_domain(DOMAIN)
        self.assertNotIn(DOMAIN, [d.name for d in self.adapter.list_domains()])

    def test_delete_unknown_domain_does_not_raise(self):
        self.adapter.delete_domain("never-existed.example")

    def test_list_domains_returns_dtos(self):
        self.adapter.ensure_domain(domain_spec())
        for item in self.adapter.list_domains():
            self.assertIsInstance(item, EngineDomain)

    # ── Mailboxes ───────────────────────────────────────────────────────────

    def test_ensure_mailbox_creates(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        self.assertIn(ADDRESS, [m.address for m in self.adapter.list_mailboxes()])

    def test_ensure_mailbox_is_idempotent(self):
        self.adapter.ensure_domain(domain_spec())
        spec = mailbox_spec()
        self.adapter.ensure_mailbox(spec, "Initial-Passphrase-1")
        self.adapter.ensure_mailbox(spec, "Initial-Passphrase-1")
        matching = [m for m in self.adapter.list_mailboxes() if m.address == ADDRESS]
        self.assertEqual(len(matching), 1, "repeated ensure_mailbox duplicated the mailbox")

    def test_ensure_mailbox_retry_without_password_keeps_credential(self):
        """
        The documented guarantee: a retry that omits the password must not clear
        a working credential.
        """
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        self.adapter.ensure_mailbox(mailbox_spec(display_name="Renamed"))
        self.assertTrue(self.credential_present(ADDRESS))

    def test_ensure_mailbox_updates_quota(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(quota_mb=1024), "Initial-Passphrase-1")
        self.adapter.ensure_mailbox(mailbox_spec(quota_mb=4096))
        found = next(m for m in self.adapter.list_mailboxes() if m.address == ADDRESS)
        self.assertEqual(found.quota_mb, 4096)

    def test_set_mailbox_quota_is_idempotent(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        self.adapter.set_mailbox_quota(ADDRESS, 8192)
        self.adapter.set_mailbox_quota(ADDRESS, 8192)
        found = next(m for m in self.adapter.list_mailboxes() if m.address == ADDRESS)
        self.assertEqual(found.quota_mb, 8192)

    def test_set_mailbox_active_is_assignment(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        self.adapter.set_mailbox_active(ADDRESS, False)
        found = next(m for m in self.adapter.list_mailboxes() if m.address == ADDRESS)
        self.assertFalse(found.active)

    def test_empty_password_is_rejected(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        with self.assertRaises(Rejected):
            self.adapter.set_mailbox_password(ADDRESS, "")

    def test_delete_mailbox_is_idempotent(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        self.adapter.delete_mailbox(ADDRESS)
        self.adapter.delete_mailbox(ADDRESS)
        self.assertNotIn(ADDRESS, [m.address for m in self.adapter.list_mailboxes()])

    def test_delete_unknown_mailbox_does_not_raise(self):
        self.adapter.delete_mailbox("nobody@never-existed.example")

    def test_list_mailboxes_scopes_to_domain(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_domain(domain_spec(name="other.example"))
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        self.adapter.ensure_mailbox(mailbox_spec(address="bob@other.example"), "Initial-Passphrase-1")
        scoped = self.adapter.list_mailboxes(DOMAIN)
        self.assertEqual([m.address for m in scoped], [ADDRESS])

    def test_get_mailbox_usage_returns_dto_or_none(self):
        self.adapter.ensure_domain(domain_spec())
        self.assertIsNone(self.adapter.get_mailbox_usage("nobody@never-existed.example"))
        self.adapter.ensure_mailbox(mailbox_spec(quota_mb=2048), "Initial-Passphrase-1")
        usage = self.adapter.get_mailbox_usage(ADDRESS)
        self.assertIsInstance(usage, MailboxUsage)
        self.assertEqual(usage.address, ADDRESS)
        self.assertEqual(usage.quota_mb, 2048)
        self.assertGreaterEqual(usage.used_mb, 0)
        self.assertIsInstance(usage.percent_used, int)

    def test_get_last_login_returns_string_or_none(self):
        self.adapter.ensure_domain(domain_spec())
        self.assertIsNone(self.adapter.get_last_login("nobody@never-existed.example"))
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        value = self.adapter.get_last_login(ADDRESS)
        self.assertTrue(value is None or isinstance(value, str))

    # ── Aliases and forwarding ──────────────────────────────────────────────

    def test_ensure_alias_is_idempotent(self):
        self.adapter.ensure_domain(domain_spec())
        spec = AliasSpec(address=f"sales@{DOMAIN}", destinations=(ADDRESS,))
        self.adapter.ensure_alias(spec)
        self.adapter.ensure_alias(spec)

    def test_ensure_alias_replaces_destination_set(self):
        self.adapter.ensure_domain(domain_spec())
        source = f"sales@{DOMAIN}"
        self.adapter.ensure_alias(AliasSpec(address=source, destinations=(ADDRESS,)))
        self.adapter.ensure_alias(
            AliasSpec(address=source, destinations=("a@x.example", "b@x.example"))
        )
        self.assertEqual(self.alias_destinations(source), ("a@x.example", "b@x.example"))

    def test_delete_alias_is_idempotent(self):
        self.adapter.ensure_domain(domain_spec())
        source = f"sales@{DOMAIN}"
        self.adapter.ensure_alias(AliasSpec(address=source, destinations=(ADDRESS,)))
        self.adapter.delete_alias(source)
        self.adapter.delete_alias(source)

    def test_alias_spec_requires_a_destination(self):
        with self.assertRaises(ValueError):
            AliasSpec(address=f"sales@{DOMAIN}", destinations=())

    def test_ensure_forwarding_applies_destinations_verbatim(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        self.adapter.ensure_forwarding(
            ForwardingSpec(mailbox_address=ADDRESS, destinations=("x@e.example", ADDRESS))
        )
        self.assertEqual(self.alias_destinations(ADDRESS), ("x@e.example", ADDRESS))

    def test_ensure_forwarding_with_empty_set_removes_forwarding(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        self.adapter.ensure_forwarding(
            ForwardingSpec(mailbox_address=ADDRESS, destinations=("x@e.example",))
        )
        self.adapter.ensure_forwarding(ForwardingSpec(mailbox_address=ADDRESS, destinations=()))
        self.assertIsNone(self.alias_destinations(ADDRESS))

    def test_ensure_forwarding_is_idempotent(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        spec = ForwardingSpec(mailbox_address=ADDRESS, destinations=("x@e.example",))
        self.adapter.ensure_forwarding(spec)
        self.adapter.ensure_forwarding(spec)
        self.assertEqual(self.alias_destinations(ADDRESS), ("x@e.example",))

    # ── DKIM: public material only (DEC-007r) ───────────────────────────────

    def test_get_dkim_returns_none_for_a_domain_the_engine_does_not_hold(self):
        self.assertIsNone(self.adapter.get_dkim_public_key("never-created.example"))

    def test_creating_a_domain_also_creates_its_dkim_key(self):
        """
        The engine mints the keypair as part of creating the domain — there is
        no separate generation step, and asking for one afterwards is refused.

        This test asserted the opposite until P4C-A (that no key existed after
        ensure_domain). It passed only because both test doubles had been
        written to match the adapter's assumption rather than the engine's
        behaviour.
        """
        self.adapter.ensure_domain(domain_spec())
        info = self.adapter.get_dkim_public_key(DOMAIN)
        self.assertIsNotNone(info, "the engine generates a key at domain creation")
        self.assertTrue(info.public_key)

    def test_the_selector_on_the_spec_is_the_one_the_engine_uses(self):
        """
        The selector has to travel on the domain-create call. If it does not,
        the engine picks its own default and MateMail publishes DNS for a
        selector the engine never signs with.
        """
        self.adapter.ensure_domain(domain_spec(dkim_selector="sel7"))
        info = self.adapter.get_dkim_public_key(DOMAIN)
        self.assertEqual(info.selector, "sel7")
        self.assertEqual(info.dns_record_name, f"sel7._domainkey.{DOMAIN}")

    def test_delete_dkim_removes_the_key_and_is_idempotent(self):
        self.adapter.ensure_domain(domain_spec())
        self.assertIsNotNone(self.adapter.get_dkim_public_key(DOMAIN))

        self.adapter.delete_dkim_key(DOMAIN)
        self.assertIsNone(self.adapter.get_dkim_public_key(DOMAIN))

        # Already absent is the desired end state, not an error.
        self.adapter.delete_dkim_key(DOMAIN)
        self.adapter.delete_dkim_key("never-created.example")

    def test_deleting_a_domain_does_not_delete_its_dkim_key(self):
        """
        The behaviour that makes `delete_dkim_key` necessary, pinned so it
        cannot be quietly "fixed" in a test double again.

        Measured against the real engine in P4B: the key survives the domain,
        and re-adding the domain adopts the surviving key. Both adapters must
        reproduce it, because deprovisioning code is written against this.
        """
        self.adapter.ensure_domain(domain_spec())
        original = self.adapter.get_dkim_public_key(DOMAIN)

        self.adapter.delete_domain(DOMAIN)

        survivor = self.adapter.get_dkim_public_key(DOMAIN)
        self.assertIsNotNone(survivor, "the engine keeps DKIM keys after the domain")
        self.assertEqual(survivor.public_key, original.public_key)

    def test_recreating_a_domain_inherits_a_surviving_key(self):
        """
        The cross-tenant hazard itself: tenant B re-registers a domain tenant A
        gave up and silently gets tenant A's signing key.
        """
        self.adapter.ensure_domain(domain_spec(dkim_selector="tenanta"))
        leaked = self.adapter.get_dkim_public_key(DOMAIN).public_key

        self.adapter.delete_domain(DOMAIN)
        self.adapter.ensure_domain(domain_spec(dkim_selector="tenantb"))

        inherited = self.adapter.get_dkim_public_key(DOMAIN)
        self.assertEqual(
            inherited.public_key, leaked,
            "if this ever stops being true the engine changed; re-check "
            "deprovisioning before relaxing anything",
        )
        # And the remedy: deleting the key first gives the new owner a new key.
        self.adapter.delete_domain(DOMAIN)
        self.adapter.delete_dkim_key(DOMAIN)
        self.adapter.ensure_domain(domain_spec(dkim_selector="tenantb"))

        clean = self.adapter.get_dkim_public_key(DOMAIN)
        self.assertNotEqual(clean.public_key, leaked)
        self.assertEqual(clean.selector, "tenantb")

    def test_rotate_dkim_returns_public_material(self):
        self.adapter.ensure_domain(domain_spec())
        info = self.adapter.rotate_dkim_key(DOMAIN)
        self.assertIsInstance(info, DkimKeyInfo)
        self.assertTrue(info.public_key)
        self.assertTrue(info.selector)

    def test_dkim_result_carries_no_private_material(self):
        """
        DEC-007r: the private key never crosses this boundary. Assert on the DTO
        shape, so adding a private-key field would fail here.
        """
        self.adapter.ensure_domain(domain_spec())
        info = self.adapter.rotate_dkim_key(DOMAIN)
        fields = set(vars(info).keys())
        for forbidden in ("private_key", "privkey", "private", "secret", "pem"):
            self.assertNotIn(forbidden, fields)
        blob = " ".join(str(v) for v in vars(info).values()).upper()
        self.assertNotIn("PRIVATE KEY", blob)
        self.assertNotIn("BEGIN RSA", blob)

    def test_rotate_dkim_is_not_idempotent(self):
        """Each rotation must produce new public material — that is the point."""
        self.adapter.ensure_domain(domain_spec())
        first = self.adapter.rotate_dkim_key(DOMAIN)
        second = self.adapter.rotate_dkim_key(DOMAIN)
        self.assertNotEqual(first.public_key, second.public_key)

    def test_get_dkim_after_rotation_matches(self):
        self.adapter.ensure_domain(domain_spec())
        rotated = self.adapter.rotate_dkim_key(DOMAIN)
        fetched = self.adapter.get_dkim_public_key(DOMAIN)
        self.assertEqual(fetched.public_key, rotated.public_key)

    # ── Queue and quarantine ────────────────────────────────────────────────

    def test_queue_and_quarantine_return_lists(self):
        self.assertIsInstance(self.adapter.get_queue_status(), list)
        self.assertIsInstance(self.adapter.get_quarantine_items(), list)

    def test_queue_and_quarantine_actions_are_idempotent(self):
        self.adapter.cancel_queue_message("no-such-id")
        self.adapter.cancel_queue_message("no-such-id")
        # Quarantine release is deliberately NOT part of the idempotency
        # contract: mailcow 2026-07b has no endpoint for it, so both adapters
        # refuse rather than pretending. Asserted in test_engine_capabilities.py.

    # ── Health ──────────────────────────────────────────────────────────────

    def test_check_health_returns_dto_and_never_raises(self):
        health = self.adapter.check_health()
        self.assertIsInstance(health, EngineHealth)
        self.assertIsInstance(health.reachable, bool)

    # ── Nothing engine-shaped comes back ────────────────────────────────────

    def test_read_methods_return_only_matemail_types(self):
        self.adapter.ensure_domain(domain_spec())
        self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")
        for item in self.adapter.list_domains():
            self.assertIsInstance(item, EngineDomain)
        for item in self.adapter.list_mailboxes():
            self.assertIsInstance(item, EngineMailbox)
        self.assertIsInstance(self.adapter.get_mailbox_usage(ADDRESS), MailboxUsage)

    def test_mutating_methods_return_none(self):
        """No result object to inspect: success is the absence of an exception."""
        self.assertIsNone(self.adapter.ensure_domain(domain_spec()))
        self.assertIsNone(self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1"))
        self.assertIsNone(self.adapter.set_mailbox_quota(ADDRESS, 1024))
        self.assertIsNone(self.adapter.delete_mailbox(ADDRESS))
        self.assertIsNone(self.adapter.delete_domain(DOMAIN))


class StubAdapterContractTest(AdapterContractTests, SimpleTestCase):
    def setUp(self):
        self.adapter = StubAdapter()

    def credential_present(self, address) -> bool:
        return self.adapter._passwords.get(address, False)

    def alias_destinations(self, address):
        spec = self.adapter._aliases.get(address)
        return spec.destinations if spec else None


class MailcowAdapterContractTest(AdapterContractTests, SimpleTestCase):
    """The real adapter, driven against an in-memory fake of the engine's API."""

    def setUp(self):
        self.adapter, self.engine = build_mailcow_adapter()

    def credential_present(self, address) -> bool:
        return self.engine.mailboxes.get(address, {}).get("_has_password", False)

    def alias_destinations(self, address):
        row = self.engine.aliases.get(address)
        return tuple(row["goto"].split(",")) if row else None

    # ── Error mapping — only meaningful against the real adapter ────────────

    def test_transport_failure_maps_to_engine_unavailable(self):
        self.engine.fail_next_transport = True
        with self.assertRaises(EngineUnavailable):
            self.adapter.ensure_domain(domain_spec())

    def test_server_error_maps_to_engine_unavailable(self):
        self.engine.fail_next_status = 502
        with self.assertRaises(EngineUnavailable):
            self.adapter.ensure_domain(domain_spec())

    def test_auth_rejection_maps_to_engine_unavailable(self):
        """A bad API key is an operator problem, not a customer-actionable one."""
        self.engine.fail_next_status = 401
        with self.assertRaises(EngineUnavailable):
            self.adapter.ensure_domain(domain_spec())

    def test_quota_rejection_maps_to_quota_exceeded(self):
        from apps.mail_engine.errors import QuotaExceeded

        self.adapter.ensure_domain(domain_spec())
        self.engine.fail_next_message = "mailbox quota exceeds domain maxquota"
        with self.assertRaises(QuotaExceeded):
            self.adapter.ensure_mailbox(mailbox_spec(), "Initial-Passphrase-1")

    def test_unrecognised_rejection_maps_to_rejected(self):
        self.engine.fail_next_message = "something the adapter has never seen"
        with self.assertRaises(Rejected):
            self.adapter.ensure_domain(domain_spec())

    def test_every_mapped_error_is_a_mail_engine_error(self):
        """Product code can catch MailEngineError and be exhaustive."""
        for injected in ("already exists", "not found", "quota exceeded", "weird"):
            with self.subTest(engine_says=injected):
                self.engine.fail_next_message = injected
                with self.assertRaises(MailEngineError):
                    self.adapter.ensure_domain(domain_spec(name="err.example"))

    def test_health_reports_unreachable_instead_of_raising(self):
        self.engine.fail_all_transport = True
        health = self.adapter.check_health()
        self.assertFalse(health.reachable)
