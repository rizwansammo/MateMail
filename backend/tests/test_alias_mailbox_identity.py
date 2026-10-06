from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.aliases.models import Alias
from apps.mail_engine.dto import AliasSpec
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_mailbox,
    make_tenant,
    make_user,
)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class MailboxOnlyAliasApiTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("owner@alias.test")
        self.tenant = make_tenant(self.owner, name="Alias Co", slug="alias-co")
        self.domain = make_domain(self.tenant, "alias.test")
        self.mailbox = make_mailbox(self.tenant, self.domain, "alice")
        self.client = auth_client(self.owner, self.tenant)

    def create(self, **overrides):
        payload = {
            "source_local_part": "hello",
            "domain_id": str(self.domain.id),
            "destination_mailbox_id": str(self.mailbox.id),
            **overrides,
        }
        return self.client.post("/api/aliases/", payload, format="json")

    def test_alias_is_created_for_exactly_one_internal_mailbox(self):
        with mock.patch(
            "apps.mail_engine.stub_adapter.StubAdapter.ensure_alias"
        ) as ensure:
            response = self.create()

        self.assertEqual(201, response.status_code)
        alias = Alias.objects.get(source_address="hello@alias.test")
        self.assertEqual(self.mailbox, alias.destination_mailbox)
        self.assertEqual(self.mailbox.email, response.data["destination_email"])
        self.assertNotIn("destination_address", response.data)

        ensure.assert_called_once()
        (spec,), _ = ensure.call_args
        self.assertIsInstance(spec, AliasSpec)
        self.assertEqual(("alice@alias.test",), spec.destinations)

    def test_external_destination_shape_is_rejected(self):
        before = Alias.objects.count()
        response = self.client.post(
            "/api/aliases/",
            {
                "source_local_part": "legacy",
                "domain_id": str(self.domain.id),
                "destination_address": "outside@example.test",
            },
            format="json",
        )

        self.assertEqual(400, response.status_code)
        self.assertIn("destination_address", response.data)
        self.assertIn("Forwarding", str(response.data["destination_address"]))
        self.assertEqual(before, Alias.objects.count())

    def test_destination_mailbox_is_required(self):
        response = self.client.post(
            "/api/aliases/",
            {
                "source_local_part": "missing",
                "domain_id": str(self.domain.id),
            },
            format="json",
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("destination_mailbox_id", response.data)

    def test_foreign_mailbox_cannot_be_alias_destination(self):
        other_owner = make_user("other@alias.test")
        other_tenant = make_tenant(
            other_owner, name="Other Alias", slug="other-alias"
        )
        other_domain = make_domain(other_tenant, "other-alias.test")
        other_mailbox = make_mailbox(other_tenant, other_domain, "other")

        response = self.create(
            source_local_part="foreign",
            destination_mailbox_id=str(other_mailbox.id),
        )
        self.assertEqual(400, response.status_code)
        self.assertIn("destination_mailbox_id", response.data)
        self.assertFalse(
            Alias.objects.filter(source_address="foreign@alias.test").exists()
        )

    def test_model_rejects_cross_tenant_destination(self):
        other_owner = make_user("model-other@alias.test")
        other_tenant = make_tenant(
            other_owner, name="Model Other", slug="model-other-alias"
        )
        other_domain = make_domain(other_tenant, "model-other.test")
        other_mailbox = make_mailbox(other_tenant, other_domain, "other")

        with self.assertRaises(ValidationError):
            Alias.objects.create(
                tenant=self.tenant,
                domain=self.domain,
                source_address="bad@alias.test",
                destination_mailbox=other_mailbox,
            )
