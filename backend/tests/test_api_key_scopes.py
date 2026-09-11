"""
API key scopes.

Before P3b an API key authenticated *as the user who created it* and inherited
every permission that user held. Two consequences: a key minted by a workspace
owner could do anything the owner could, and a key minted by a member of
MateMail staff was a platform-admin credential sitting in a customer's
configuration file.

The tests that matter most here are the negative ones. A scope model that
grants correctly but fails to deny is not a scope model.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.logs.models import LogEventType, MailLog
from apps.security.scopes import APIKeyScope, normalise, permits, required_scope
from apps.teams.models import APIKey
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    auth_client,
    bearer_client,
    disable_throttling,
    make_api_key,
    make_domain,
    make_tenant,
    make_user,
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "api-key-scope-tests",
    }
}


class ScopeModelTest(TestCase):
    """The mapping itself, without the HTTP stack."""

    def test_unmapped_write_paths_are_denied(self):
        """
        Default deny. A mutating endpoint added later is refused to API keys
        until someone maps it deliberately — the alternative is that every new
        endpoint is open to every key by omission.
        """
        self.assertIsNone(required_scope("/api/something-new/", "POST"))
        self.assertFalse(permits(list(APIKeyScope.values), "/api/something-new/", "POST"))

    def test_platform_and_internal_prefixes_are_never_permitted(self):
        for path in ("/api/platform/stats/", "/api/internal/smtp/inbound/"):
            for method in ("GET", "POST"):
                self.assertIsNone(required_scope(path, method))
                self.assertFalse(permits(list(APIKeyScope.values), path, method))

    def test_auth_endpoints_are_never_permitted(self):
        """A key is not a session and must not be able to mint one."""
        self.assertFalse(permits(list(APIKeyScope.values), "/api/auth/login/", "POST"))

    def test_reads_need_only_the_read_scope(self):
        self.assertTrue(permits(["read"], "/api/domains/", "GET"))
        self.assertTrue(permits(["read"], "/api/mailboxes/", "HEAD"))

    def test_write_scopes_do_not_leak_across_resources(self):
        self.assertTrue(permits(["domains:write"], "/api/domains/", "POST"))
        self.assertFalse(permits(["domains:write"], "/api/mailboxes/", "POST"))
        self.assertFalse(permits(["mailboxes:write"], "/api/domains/", "POST"))

    def test_admin_does_not_imply_the_resource_scopes(self):
        """`admin` covers workspace administration, not day-to-day mail objects."""
        self.assertTrue(permits(["admin"], "/api/teams/apikeys/", "POST"))
        self.assertFalse(permits(["admin"], "/api/mailboxes/", "POST"))

    def test_corrupt_scopes_degrade_to_read_only(self):
        for value in (None, "domains:write", {}, ["nonsense"], []):
            self.assertEqual(normalise(value), ["read"])

    def test_read_is_always_present(self):
        self.assertIn("read", normalise(["admin"]))


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class KeyEnforcementTest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("keyowner@example.test")
        self.tenant = make_tenant(self.owner, name="Keys", slug="keys")
        self.domain = make_domain(self.tenant, "keys.example")

    def _key(self, scopes=None):
        raw, _ = make_api_key(self.tenant, self.owner, scopes=scopes)
        return bearer_client(raw)

    # ── the default ────────────────────────────────────────────────────────

    def test_a_new_key_is_read_only(self):
        raw, key = make_api_key(self.tenant, self.owner)
        self.assertEqual(key.scopes, ["read"])
        self.assertTrue(key.is_read_only)

    def test_a_default_key_can_read(self):
        res = self._key().get("/api/domains/")
        self.assertEqual(res.status_code, 200)

    def test_a_default_key_cannot_write(self):
        res = self._key().post("/api/domains/", {"domain": "newthing.example"})
        self.assertEqual(res.status_code, 403)

    def test_a_default_key_cannot_delete(self):
        res = self._key().delete(f"/api/domains/{self.domain.id}/")
        self.assertEqual(res.status_code, 403)

    def test_the_owner_who_minted_it_grants_the_key_nothing(self):
        """
        The owner can create domains. A key they minted still cannot, unless
        the scope was named.
        """
        self.assertEqual(
            auth_client(self.owner, self.tenant)
            .post("/api/domains/", {"domain": "byowner.example"})
            .status_code,
            201,
        )
        self.assertEqual(
            self._key().post("/api/domains/", {"domain": "bykey.example"}).status_code,
            403,
        )

    # ── granted scopes ─────────────────────────────────────────────────────

    def test_a_scoped_key_can_perform_its_own_mutations(self):
        res = self._key(["domains:write"]).post(
            "/api/domains/", {"domain": "scoped.example"}
        )
        self.assertEqual(res.status_code, 201)

    def test_a_scoped_key_cannot_perform_other_mutations(self):
        res = self._key(["domains:write"]).post(
            "/api/mailboxes/",
            {
                "local_part": "nope",
                "domain_id": str(self.domain.id),
                "full_name": "Nope",
                "password": TEST_PASSWORD,
                "quota_mb": 1024,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("mailboxes:write", res.json()["detail"])

    def test_a_scoped_key_can_still_read_everything_in_its_workspace(self):
        self.assertEqual(self._key(["domains:write"]).get("/api/mailboxes/").status_code, 200)

    # ── platform admin ─────────────────────────────────────────────────────

    def test_a_key_cannot_reach_the_platform_api(self):
        res = self._key(["admin"]).get("/api/platform/stats/")
        self.assertEqual(res.status_code, 403)

    def test_a_platform_admin_creator_grants_the_key_no_platform_access(self):
        """
        The defect this scope model exists for: the key authenticated as its
        creator, so a staff-minted key was a platform-admin credential.
        """
        staff = make_user("staff@example.test")
        staff.is_platform_admin = True
        staff.save(update_fields=["is_platform_admin"])
        staff_tenant = make_tenant(staff, name="Staff", slug="staff")

        # The person really is a platform admin.
        self.assertEqual(
            auth_client(staff, staff_tenant).get("/api/platform/stats/").status_code, 200
        )

        raw, _ = make_api_key(staff_tenant, staff, scopes=["admin"])
        self.assertEqual(
            bearer_client(raw).get("/api/platform/stats/").status_code, 403
        )

    def test_a_key_cannot_reach_the_internal_bridge(self):
        res = self._key(["admin"]).post("/api/internal/smtp/inbound/", {}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_a_key_cannot_use_the_auth_endpoints(self):
        res = self._key(["admin"]).post(
            "/api/auth/login/", {"email": self.owner.email, "password": TEST_PASSWORD}
        )
        self.assertEqual(res.status_code, 403)

    # ── tenancy and revocation ─────────────────────────────────────────────

    def test_a_key_cannot_reach_another_workspace(self):
        stranger = make_user("stranger-key@example.test")
        other = make_tenant(stranger, name="Other", slug="other-keys")
        other_domain = make_domain(other, "otherkeys.example")

        res = self._key(["domains:write"]).get(f"/api/domains/{other_domain.id}/")
        self.assertEqual(res.status_code, 404)

    def test_a_key_cannot_delete_another_workspaces_resource(self):
        stranger = make_user("stranger-key2@example.test")
        other = make_tenant(stranger, name="Other2", slug="other-keys-2")
        other_domain = make_domain(other, "otherkeys2.example")

        res = self._key(["domains:write"]).delete(f"/api/domains/{other_domain.id}/")
        self.assertIn(res.status_code, (403, 404))

    def test_a_revoked_key_is_rejected(self):
        raw, key = make_api_key(self.tenant, self.owner, scopes=["domains:write"])
        key.is_active = False
        key.save(update_fields=["is_active"])
        self.assertEqual(bearer_client(raw).get("/api/domains/").status_code, 401)

    def test_an_expired_key_is_rejected(self):
        from datetime import timedelta

        from django.utils import timezone

        raw, key = make_api_key(self.tenant, self.owner)
        key.expires_at = timezone.now() - timedelta(hours=1)
        key.save(update_fields=["expires_at"])
        self.assertEqual(bearer_client(raw).get("/api/domains/").status_code, 401)

    def test_a_key_stored_without_scopes_is_read_only(self):
        """A row from before the migration, or edited by hand, must not be open."""
        raw, key = make_api_key(self.tenant, self.owner)
        APIKey.objects.filter(pk=key.pk).update(scopes=[])
        client = bearer_client(raw)
        self.assertEqual(client.get("/api/domains/").status_code, 200)
        self.assertEqual(
            client.post("/api/domains/", {"domain": "empty.example"}).status_code, 403
        )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class KeyManagementAPITest(TestCase):
    def setUp(self):
        cache.clear()
        disable_throttling(self)
        self.owner = make_user("keyadmin@example.test")
        self.tenant = make_tenant(self.owner, name="KeyAdmin", slug="key-admin")
        self.api = auth_client(self.owner, self.tenant)

    def test_creating_without_scopes_yields_a_read_only_key(self):
        res = self.api.post("/api/teams/apikeys/", {"name": "ci"})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["scopes"], ["read"])
        self.assertTrue(res.data["is_read_only"])

    def test_the_secret_is_returned_once_and_never_again(self):
        created = self.api.post("/api/teams/apikeys/", {"name": "once"})
        self.assertTrue(created.data["key"].startswith("mm_"))

        listed = self.api.get("/api/teams/apikeys/")
        self.assertNotIn("key", listed.data[0])
        self.assertNotIn(created.data["key"], str(listed.data))

    def test_scopes_are_visible_in_the_listing(self):
        self.api.post(
            "/api/teams/apikeys/",
            {"name": "scoped", "scopes": ["mailboxes:write"]},
            format="json",
        )
        listed = self.api.get("/api/teams/apikeys/")
        self.assertIn("mailboxes:write", listed.data[0]["scopes"])

    def test_an_unknown_scope_is_rejected_not_ignored(self):
        res = self.api.post(
            "/api/teams/apikeys/",
            {"name": "bad", "scopes": ["domains:admin"]},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_scopes_can_be_changed_and_the_change_is_audited(self):
        key_id = self.api.post("/api/teams/apikeys/", {"name": "grow"}).data["id"]
        res = self.api.patch(
            f"/api/teams/apikeys/{key_id}/scopes/",
            {"scopes": ["domains:write"]},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["scopes"], ["domains:write", "read"])
        self.assertTrue(
            MailLog.objects.filter(
                tenant=self.tenant, event_type=LogEventType.API_KEY_SCOPES_CHANGED
            ).exists()
        )

    def test_creation_and_revocation_are_audited(self):
        key_id = self.api.post("/api/teams/apikeys/", {"name": "audited"}).data["id"]
        self.assertTrue(
            MailLog.objects.filter(
                tenant=self.tenant, event_type=LogEventType.API_KEY_CREATED
            ).exists()
        )
        self.api.delete(f"/api/teams/apikeys/{key_id}/")
        self.assertTrue(
            MailLog.objects.filter(
                tenant=self.tenant, event_type=LogEventType.API_KEY_REVOKED
            ).exists()
        )

    def test_the_audit_record_never_contains_the_secret(self):
        created = self.api.post("/api/teams/apikeys/", {"name": "secret-check"})
        raw = created.data["key"]
        for entry in MailLog.objects.filter(tenant=self.tenant):
            self.assertNotIn(raw, str(entry.metadata))

    def test_a_read_only_member_cannot_manage_keys(self):
        from tests.factories import add_member

        reader = make_user("reader@example.test")
        add_member(self.tenant, reader, "read_only")
        res = auth_client(reader, self.tenant).post(
            "/api/teams/apikeys/", {"name": "sneaky"}
        )
        self.assertEqual(res.status_code, 403)

    def test_a_key_cannot_widen_its_own_scopes(self):
        """
        Escalation check: /api/teams/ needs `admin`, so a domains-scoped key is
        refused; and an admin-scoped key granting itself more is at least
        audited and confined to its own workspace.
        """
        raw, _ = make_api_key(self.tenant, self.owner, scopes=["domains:write"])
        key_id = self.api.post("/api/teams/apikeys/", {"name": "target"}).data["id"]
        res = bearer_client(raw).patch(
            f"/api/teams/apikeys/{key_id}/scopes/",
            {"scopes": ["admin"]},
            format="json",
        )
        self.assertEqual(res.status_code, 403)
