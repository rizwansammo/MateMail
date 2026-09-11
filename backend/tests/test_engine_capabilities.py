"""
What the engine can and cannot actually do, at the version we pin.

Both facts asserted here were found in P4A by reading mailcow 2026-07b's own
OpenAPI specification and source, not by remembering how the API behaves. Both
were wrong in the P1 adapter, and both were the kind of wrong that looks fine
until production:

- `release_quarantine_item` POSTed to an endpoint that does not exist. It would
  have 404'd, been classified NotFound, and been swallowed by a handler that
  treats NotFound as "already actioned" — a silent success over a message still
  sitting in quarantine.
- the DKIM read returns a `privkey` field on every call. It is empty under a
  default configuration, but an engine with SHOW_DKIM_PRIV_KEYS=y returns real
  private key material, which DEC-007r says must never cross this boundary.
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase

from apps.mail_engine.dto import DkimKeyInfo
from apps.mail_engine.errors import EngineCapabilityMissing
from apps.mail_engine.mailcow_adapter import (
    VERIFIED_AGAINST_ENGINE_VERSION,
    MailcowAdapter,
)
from apps.mail_engine.stub_adapter import StubAdapter
from tests.factories import make_domain, make_tenant, make_user

#: What the engine really returns when an operator has enabled private-key
#: display. Shape taken from functions.dkim.inc.php at 2026-07b.
ENGINE_DKIM_WITH_PRIVKEY = {
    "pubkey": "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAfakepublickeymaterial",
    "dkim_txt": "v=DKIM1;k=rsa;t=s;s=email;p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAfakepublickeymaterial",
    "dkim_selector": "mm1",
    "length": "2048",
    "privkey": "LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tZmFrZXByaXZhdGVrZXltYXRlcmlhbA==",
}


class PinnedVersionTest(SimpleTestCase):
    def test_the_verified_engine_version_is_recorded(self):
        """
        The endpoint mapping is only true for a specific engine release. Pinning
        the version in code is what makes "re-verify on upgrade" actionable
        rather than folklore.
        """
        self.assertEqual(VERIFIED_AGAINST_ENGINE_VERSION, "2026-07b")

    def test_the_pin_is_not_a_moving_target(self):
        self.assertNotIn(VERIFIED_AGAINST_ENGINE_VERSION.lower(), ("latest", "master", "main"))


class QuarantineReleaseTest(SimpleTestCase):
    """
    mailcow 2026-07b defines only `GET /api/v1/get/quarantine/all` and
    `POST /api/v1/edit/quarantine_notification`. There is no release endpoint.
    """

    def test_the_real_adapter_refuses_rather_than_calling_a_missing_endpoint(self):
        adapter = MailcowAdapter()
        with mock.patch.object(adapter, "_request") as request:
            with self.assertRaises(EngineCapabilityMissing):
                adapter.release_quarantine_item("msg-1")
        request.assert_not_called()

    def test_the_stub_refuses_identically(self):
        """
        A stub that succeeds where the engine cannot lets a feature be built,
        tested and merged against a capability that does not exist.
        """
        with self.assertRaises(EngineCapabilityMissing):
            StubAdapter().release_quarantine_item("msg-1")

    def test_the_refusal_does_not_leak_engine_identity(self):
        try:
            StubAdapter().release_quarantine_item("msg-1")
        except EngineCapabilityMissing as exc:
            customer_text = str(exc).lower()
        for leak in ("mailcow", "postfix", "dovecot", "rspamd", "quarantine/release", "api/v1"):
            self.assertNotIn(leak, customer_text)

    def test_reading_quarantine_still_works(self):
        """Only the release action is missing; listing is unaffected."""
        adapter = MailcowAdapter()
        with mock.patch.object(adapter, "_request", return_value=[{"id": 1}]) as request:
            items = adapter.get_quarantine_items()
        self.assertEqual(items, [{"id": 1}])
        self.assertIn("/api/v1/get/quarantine/all", request.call_args[0])


class DkimPrivateMaterialTest(SimpleTestCase):
    """DEC-007r: the private key never crosses the adapter boundary."""

    def _info(self, payload) -> DkimKeyInfo:
        adapter = MailcowAdapter()
        with mock.patch.object(adapter, "_request", return_value=payload):
            return adapter.get_dkim_public_key("example.test")

    def test_public_material_is_returned_normally(self):
        info = self._info(dict(ENGINE_DKIM_WITH_PRIVKEY, privkey=""))
        self.assertEqual(info.selector, "mm1")
        self.assertIn("fakepublickeymaterial", info.public_key)
        self.assertEqual(info.dns_record_name, "mm1._domainkey.example.test")

    def test_private_key_never_reaches_the_dto(self):
        """
        The engine is *configured* not to send this, but MateMail does not rely
        on the engine's configuration for a DEC-007r guarantee.
        """
        info = self._info(dict(ENGINE_DKIM_WITH_PRIVKEY))
        blob = repr(info)
        self.assertNotIn("PRIVATE KEY", blob)
        self.assertNotIn(ENGINE_DKIM_WITH_PRIVKEY["privkey"], blob)
        self.assertNotIn("fakeprivatekey", blob.lower())

    def test_the_dto_has_no_field_that_could_hold_one(self):
        """Second line of defence: there is nowhere to put it."""
        fields = set(DkimKeyInfo.__dataclass_fields__)
        for forbidden in ("privkey", "private_key", "priv_key", "secret"):
            self.assertNotIn(forbidden, fields)

    def test_a_populated_private_key_is_logged_as_a_misconfiguration(self):
        with self.assertLogs("apps.mail_engine.mailcow_adapter", level="ERROR") as logs:
            self._info(dict(ENGINE_DKIM_WITH_PRIVKEY))
        output = "".join(logs.output)
        self.assertIn("SHOW_DKIM_PRIV_KEYS", output)
        # The warning must not itself print the key it is warning about.
        self.assertNotIn(ENGINE_DKIM_WITH_PRIVKEY["privkey"], output)

    def test_alternative_private_field_names_are_stripped_too(self):
        for field in ("private_key", "priv_key", "key"):
            payload = {
                "pubkey": "PUBLICMATERIAL",
                "dkim_selector": "mm1",
                field: "-----BEGIN PRIVATE KEY-----leak",
            }
            info = self._info(payload)
            self.assertNotIn("BEGIN PRIVATE KEY", repr(info), f"{field} leaked")

    def test_an_absent_key_is_none_not_an_error(self):
        self.assertIsNone(self._info({}))

    def test_the_port_exposes_no_private_key_method(self):
        from apps.mail_engine.adapter import MailEngineAdapter

        names = [n for n in dir(MailEngineAdapter) if not n.startswith("_")]
        for name in names:
            self.assertNotIn("private", name.lower(), f"{name} suggests private material")


class DkimAdoptionFailsClosedTest(SimpleTestCase):
    """
    DKIM adoption is part of provisioning, not an optional extra.

    A domain marked `mail_engine_provisioned=True` without DKIM would be shown
    to the customer as ready while unable to sign a single message. Unsigned
    mail from a brand-new domain is how a sending reputation is destroyed
    before it exists — so adoption fails closed.

    The second property is that `rotate_dkim_key` is NOT idempotent: each call
    invalidates the DNS record the customer has published, and provisioning is
    a retryable Celery task.
    """

    def _domain(self, selector="mm1"):
        d = mock.Mock()
        d.domain = "adopt.example"
        d.dkim_selector = selector
        d.dkim_public_key = ""
        return d

    def _adopt(self, adapter, domain, task=None):
        from apps.mail_engine.tasks import _adopt_engine_dkim

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            _adopt_engine_dkim(domain, task=task)

    def _info(self, public="PUB"):
        return DkimKeyInfo(
            selector="mm1",
            public_key=public,
            dns_record_name="mm1._domainkey.adopt.example",
            dns_record_value=f"v=DKIM1;k=rsa;p={public}",
        )

    # ── success ────────────────────────────────────────────────────────────

    def test_successful_adoption_persists_public_material(self):
        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = self._info("EXISTINGPUB")
        domain = self._domain()

        self._adopt(adapter, domain)

        adapter.rotate_dkim_key.assert_not_called()
        self.assertEqual(domain.dkim_public_key, "EXISTINGPUB")

    def test_a_key_is_generated_only_when_the_engine_has_none(self):
        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = None
        adapter.rotate_dkim_key.return_value = self._info("FRESHPUB")
        domain = self._domain()

        self._adopt(adapter, domain)

        adapter.rotate_dkim_key.assert_called_once()
        self.assertEqual(domain.dkim_public_key, "FRESHPUB")

    def test_retry_with_an_existing_engine_key_never_rotates(self):
        """The retry scenario: a published DNS record must survive."""
        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = self._info("PUBLISHED")

        for _ in range(3):
            self._adopt(adapter, self._domain())

        adapter.rotate_dkim_key.assert_not_called()

    def test_no_private_key_is_ever_persisted(self):
        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = None
        adapter.rotate_dkim_key.return_value = self._info("FRESHPUB")
        domain = self._domain()

        self._adopt(adapter, domain)

        for call in domain.save.call_args_list:
            self.assertNotIn(
                "dkim_private_key", call.kwargs.get("update_fields", [])
            )

    # ── failure: nothing may be marked provisioned ─────────────────────────

    def test_dkim_read_failure_raises_rather_than_continuing(self):
        from apps.mail_engine.errors import Rejected

        adapter = mock.Mock()
        adapter.get_dkim_public_key.side_effect = Rejected(
            "refused", operation="get_dkim_public_key"
        )
        with self.assertRaises(Rejected):
            self._adopt(adapter, self._domain())

    def test_dkim_generate_failure_raises_rather_than_continuing(self):
        from apps.mail_engine.errors import Rejected

        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = None
        adapter.rotate_dkim_key.side_effect = Rejected(
            "refused", operation="rotate_dkim_key"
        )
        with self.assertRaises(Rejected):
            self._adopt(adapter, self._domain())

    def test_empty_public_material_is_treated_as_failure(self):
        """An engine that answers without usable material is not a success."""
        from apps.mail_engine.errors import MailEngineError

        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = self._info("")
        adapter.rotate_dkim_key.return_value = self._info("")
        with self.assertRaises(MailEngineError):
            self._adopt(adapter, self._domain())

    def test_a_transient_failure_uses_the_task_retry_path(self):
        from apps.mail_engine.errors import EngineUnavailable

        adapter = mock.Mock()
        adapter.get_dkim_public_key.side_effect = EngineUnavailable(
            "engine down", operation="get_dkim_public_key"
        )
        task = mock.Mock()
        task.retry.side_effect = RuntimeError("retried")

        with self.assertRaises(RuntimeError):
            self._adopt(adapter, self._domain(), task=task)
        task.retry.assert_called_once()


class ProvisioningNotMarkedWithoutDkimTest(TestCase):
    """
    End to end through the real task: a DKIM failure must leave
    `mail_engine_provisioned` False on the persisted row.
    """

    def setUp(self):
        self.owner = make_user("prov@example.test")
        self.tenant = make_tenant(self.owner, name="Prov", slug="prov")
        self.domain = make_domain(self.tenant, "provision.example")

    def _run(self, adapter, *, expect=None):
        """
        Run the real task.

        `expect` names the exception this call should raise, and it is the only
        one tolerated. Catching everything would swallow an AttributeError or
        TypeError raised by the code under test and still let the assertions
        below pass — the test would go green over a broken task.
        """
        from apps.mail_engine.tasks import provision_domain_task

        with mock.patch("apps.mail_engine.factory.get_adapter", return_value=adapter):
            if expect is None:
                provision_domain_task(str(self.domain.id))
            else:
                with self.assertRaises(expect):
                    provision_domain_task(str(self.domain.id))
        self.domain.refresh_from_db()

    def test_dkim_read_failure_leaves_the_domain_unprovisioned(self):
        from apps.mail_engine.errors import Rejected

        adapter = mock.Mock()
        adapter.get_dkim_public_key.side_effect = Rejected("no", operation="get_dkim")
        self._run(adapter, expect=Rejected)

        self.assertFalse(
            self.domain.mail_engine_provisioned,
            "domain was marked provisioned without DKIM material",
        )
        self.assertNotEqual(self.domain.mail_engine_error, "")

    def test_dkim_generate_failure_leaves_the_domain_unprovisioned(self):
        from apps.mail_engine.errors import Rejected

        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = None
        adapter.rotate_dkim_key.side_effect = Rejected("no", operation="rotate")
        self._run(adapter, expect=Rejected)

        self.assertFalse(self.domain.mail_engine_provisioned)
        self.assertNotEqual(self.domain.mail_engine_error, "")

    def test_successful_adoption_marks_the_domain_provisioned(self):
        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = DkimKeyInfo(
            selector="mm1",
            public_key="GOODPUB",
            dns_record_name="mm1._domainkey.provision.example",
            dns_record_value="v=DKIM1;k=rsa;p=GOODPUB",
        )
        self._run(adapter)

        self.assertTrue(self.domain.mail_engine_provisioned)
        self.assertEqual(self.domain.dkim_public_key, "GOODPUB")
        self.assertEqual(self.domain.dkim_private_key, "", "a private key was stored")
        self.assertEqual(self.domain.mail_engine_error, "")

    def test_empty_public_material_records_an_error_and_does_not_provision(self):
        """
        The case that previously escaped the shared error handling: the engine
        answers, so nothing raises out of the adapter, but the material is
        unusable. It must land in the same place as every other failure —
        including recording a customer-safe reason.
        """
        from apps.mail_engine.errors import MailEngineError

        adapter = mock.Mock()
        adapter.get_dkim_public_key.return_value = DkimKeyInfo(
            selector="mm1", public_key="",
            dns_record_name="mm1._domainkey.provision.example",
            dns_record_value="",
        )
        self._run(adapter, expect=MailEngineError)

        self.assertFalse(
            self.domain.mail_engine_provisioned,
            "provisioned without usable DKIM material",
        )
        self.assertNotEqual(
            self.domain.mail_engine_error, "",
            "an empty-material failure left the customer with no explanation",
        )
        self.assertEqual(self.domain.dkim_private_key, "")
        self.assertEqual(self.domain.dkim_public_key, "")


class RequestContractTest(SimpleTestCase):
    """
    The exact wire contract, asserted against what mailcow 2026-07b's router
    actually reads.

    Three defects were found here in review, all of which would have failed
    only against a real engine:

    - every /delete/ endpoint rejects non-POST with HTTP 405
      ("only POST method is allowed" — json_api.php). All three deletes used
      DELETE.
    - `delete/mailq` is routed as `mailq('delete', array('qid' => $items))`
      where `$items` is the whole request body, so it needs a bare array. The
      adapter sent `{"id": [...]}`, which could never match a queue id.
    - `set/quarantine/release` does not exist at all (covered above).
    """

    def _capture(self, fn):
        adapter = MailcowAdapter()
        with mock.patch.object(adapter, "_request", return_value={}) as request:
            fn(adapter)
        return request.call_args

    def test_delete_domain_posts_a_bare_array(self):
        call = self._capture(lambda a: a.delete_domain("example.test"))
        self.assertEqual(call[0][0], "POST", "the engine rejects non-POST with 405")
        self.assertEqual(call[0][1], "/api/v1/delete/domain")
        self.assertEqual(call[1]["json"], ["example.test"])

    def test_delete_mailbox_posts_a_bare_array(self):
        call = self._capture(lambda a: a.delete_mailbox("a@example.test"))
        self.assertEqual(call[0][0], "POST")
        self.assertEqual(call[0][1], "/api/v1/delete/mailbox")
        self.assertEqual(call[1]["json"], ["a@example.test"])

    def test_cancel_queue_message_posts_a_bare_array_of_queue_ids(self):
        call = self._capture(lambda a: a.cancel_queue_message("QID123"))
        self.assertEqual(call[0][0], "POST")
        self.assertEqual(call[0][1], "/api/v1/delete/mailq")
        self.assertEqual(
            call[1]["json"], ["QID123"],
            "the router wraps the whole body as qid; a dict cannot match",
        )

    def test_no_adapter_method_uses_the_delete_verb(self):
        import inspect

        import apps.mail_engine.mailcow_adapter as module

        source = inspect.getsource(module)
        self.assertNotIn('"DELETE"', source, "the engine answers 405 to DELETE")

    def test_dkim_generation_sends_the_documented_fields(self):
        adapter = MailcowAdapter()
        info = DkimKeyInfo(
            selector="mm1", public_key="P", dns_record_name="n", dns_record_value="v",
        )
        with mock.patch.object(adapter, "_write") as write, \
             mock.patch.object(adapter, "delete_dkim_key"), \
             mock.patch.object(adapter, "get_dkim_public_key", return_value=info):
            adapter.rotate_dkim_key("example.test", selector="mm1")
        payload = write.call_args[0][1]
        self.assertEqual(set(payload), {"domains", "dkim_selector", "key_size"})
        self.assertEqual(payload["domains"], "example.test")

    def test_delete_dkim_posts_a_bare_array(self):
        call = self._capture(lambda a: a.delete_dkim_key("example.test"))
        self.assertEqual(call[0][0], "POST", "the engine rejects non-POST with 405")
        self.assertEqual(call[0][1], "/api/v1/delete/dkim")
        self.assertEqual(call[1]["json"], ["example.test"])

    def test_domain_creation_carries_the_dkim_selector_and_key_size(self):
        """
        The engine generates the keypair inside its domain-add handler using
        exactly these two fields. Sending them later is not an option: add/dkim
        refuses a domain that already has a key.
        """
        from apps.mail_engine.dto import DomainSpec

        adapter = MailcowAdapter()
        with mock.patch.object(adapter, "_write") as write:
            adapter.ensure_domain(
                DomainSpec(name="example.test", dkim_selector="mm1", dkim_key_size=2048)
            )
        path, payload = write.call_args[0][0], write.call_args[0][1]
        self.assertEqual(path, "/api/v1/add/domain")
        self.assertEqual(payload["dkim_selector"], "mm1")
        self.assertEqual(payload["key_size"], 2048)

    def test_rotation_deletes_the_existing_key_before_generating(self):
        """
        add/dkim is refused while a key exists, so a bare add is not a rotation
        — it is a guaranteed failure. Assert the order, not just the calls.
        """
        adapter = MailcowAdapter()
        existing = DkimKeyInfo(
            selector="mm1", public_key="OLD", dns_record_name="n", dns_record_value="v",
        )
        fresh = DkimKeyInfo(
            selector="mm1", public_key="NEW", dns_record_name="n", dns_record_value="v",
        )
        order = []
        with mock.patch.object(
                 adapter, "delete_dkim_key",
                 side_effect=lambda d: order.append("delete")), \
             mock.patch.object(
                 adapter, "_write",
                 side_effect=lambda *a, **k: order.append("add")), \
             mock.patch.object(
                 adapter, "get_dkim_public_key", side_effect=[existing, fresh]):
            result = adapter.rotate_dkim_key("example.test", selector="mm1")

        self.assertEqual(order, ["delete", "add"])
        self.assertEqual(result.public_key, "NEW")

    def test_rotation_without_an_existing_key_does_not_delete(self):
        """
        The retry case. A previous rotation deleted the old key and then failed
        to add the new one; the retry must not issue a delete it does not need,
        and must certainly not delete a replacement key on a later retry.
        """
        adapter = MailcowAdapter()
        fresh = DkimKeyInfo(
            selector="mm1", public_key="NEW", dns_record_name="n", dns_record_value="v",
        )
        with mock.patch.object(adapter, "delete_dkim_key") as delete, \
             mock.patch.object(adapter, "_write"), \
             mock.patch.object(
                 adapter, "get_dkim_public_key", side_effect=[None, fresh]):
            adapter.rotate_dkim_key("example.test", selector="mm1")

        delete.assert_not_called()

    def test_rotation_refuses_an_unusable_selector_before_touching_the_engine(self):
        """Fail closed *before* deleting a working key for a request we cannot honour."""
        from apps.mail_engine.errors import Rejected

        adapter = MailcowAdapter()
        # "" is deliberately absent: the port documents an empty selector as
        # "use the deployment default", not as a malformed value. That case is
        # covered by the test below.
        for bad in (" ", ".", "..", "a b", "a/b", "sel.", ".sel", "a..b", "@"):
            with mock.patch.object(adapter, "delete_dkim_key") as delete, \
                 mock.patch.object(adapter, "_write") as write, \
                 mock.patch.object(adapter, "get_dkim_public_key") as read:
                with self.assertRaises(Rejected, msg=f"accepted selector {bad!r}"):
                    adapter.rotate_dkim_key("example.test", selector=bad)
                delete.assert_not_called()
                write.assert_not_called()
                read.assert_not_called()

    def test_an_omitted_selector_uses_the_configured_default(self):
        """
        Empty means "whatever this deployment publishes", which is
        DKIM_SELECTOR — never a literal baked into the adapter, because a
        literal silently disagrees with the setting the day anyone changes it.
        """
        from django.test import override_settings

        adapter = MailcowAdapter()
        info = DkimKeyInfo(
            selector="dep7", public_key="P", dns_record_name="n", dns_record_value="v",
        )
        with override_settings(DKIM_SELECTOR="dep7"), \
             mock.patch.object(adapter, "_write") as write, \
             mock.patch.object(adapter, "get_dkim_public_key", side_effect=[None, info]):
            adapter.rotate_dkim_key("example.test")

        self.assertEqual(write.call_args[0][1]["dkim_selector"], "dep7")

    def test_the_api_key_header_is_sent(self):
        adapter = MailcowAdapter()
        self.assertIn("X-API-Key", adapter._session.headers)
