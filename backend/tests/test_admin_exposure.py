"""
Django admin must not disclose secrets.

Before Phase 0, DomainAdmin and TwoFactorSetupAdmin declared readonly_fields but
no field restriction, so their change forms rendered every editable field. That
put each tenant's DKIM signing key and each user's TOTP secret in front of any
staff user, and let them be overwritten too.

These tests assert on the ModelAdmin's *effective* form and on rendered admin
responses, so they fail if a future change re-exposes a secret — including via
list_display, readonly_fields, or a new fieldsets declaration.
"""
from django.contrib.admin.sites import site
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import (
    EmailVerificationToken,
    PasswordResetToken,
    TwoFactorBackupCode,
    TwoFactorSetup,
    User,
)
from apps.domains.models import Domain
from apps.teams.models import APIKey, TeamInvite
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    enable_2fa,
    make_domain,
    make_tenant,
    make_user,
)

# (model, field names that must never reach an admin form or changelist)
SECRET_FIELDS = [
    (Domain, ["dkim_private_key"]),
    (TwoFactorSetup, ["totp_secret"]),
    (TwoFactorBackupCode, ["code_hash"]),
    (EmailVerificationToken, ["token_hash"]),
    (PasswordResetToken, ["token_hash"]),
    (APIKey, ["key_hash"]),
    (TeamInvite, ["token_hash"]),
]


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class AdminSecretExposureTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser(
            email="staff@example.test", password=TEST_PASSWORD, full_name="Staff"
        )
        self.client.force_login(self.staff)

    # ── Declarative checks on the ModelAdmin itself ──────────────────────────

    def test_secret_fields_are_not_in_admin_forms(self):
        for model, secrets in SECRET_FIELDS:
            model_admin = site._registry[model]
            form_fields = set(model_admin.get_form(self._request())().fields)
            for field in secrets:
                with self.subTest(model=model.__name__, field=field):
                    self.assertNotIn(
                        field, form_fields,
                        f"{model.__name__}Admin form exposes {field}",
                    )

    def test_secret_fields_are_not_in_changelist_columns(self):
        for model, secrets in SECRET_FIELDS:
            model_admin = site._registry[model]
            columns = set(model_admin.get_list_display(self._request()))
            for field in secrets:
                with self.subTest(model=model.__name__, field=field):
                    self.assertNotIn(
                        field, columns,
                        f"{model.__name__}Admin list_display exposes {field}",
                    )

    def test_secret_fields_are_not_rendered_as_readonly(self):
        """readonly_fields still *renders* a value — it only blocks editing."""
        for model, secrets in SECRET_FIELDS:
            model_admin = site._registry[model]
            readonly = set(model_admin.get_readonly_fields(self._request()))
            for field in secrets:
                with self.subTest(model=model.__name__, field=field):
                    self.assertNotIn(
                        field, readonly,
                        f"{model.__name__}Admin renders {field} as a readonly field",
                    )

    # ── End-to-end: the secret must not appear in the HTTP response ──────────

    def test_dkim_private_key_absent_from_rendered_change_page(self):
        owner = make_user("dkim-owner@example.test")
        tenant = make_tenant(owner, name="DKIM Co", slug="dkim-co")
        domain = make_domain(tenant, "dkim-test.example")
        marker = "PRIVATE-KEY-CANARY-VALUE"
        domain.dkim_private_key = f"-----BEGIN PRIVATE KEY-----\n{marker}\n"
        domain.save(update_fields=["dkim_private_key"])

        res = self.client.get(
            reverse("admin:domains_domain_change", args=[domain.pk])
        )
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, marker)
        # No form input bound to the field (note: the safe boolean indicator is
        # named has_dkim_private_key, so match the exact input name).
        self.assertNotContains(res, 'name="dkim_private_key"')

    def test_totp_secret_absent_from_rendered_change_page(self):
        user = make_user("totp-user@example.test")
        secret = enable_2fa(user)
        setup = TwoFactorSetup.objects.get(user=user)

        res = self.client.get(
            reverse("admin:accounts_twofactorsetup_change", args=[setup.pk])
        )
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, secret)
        self.assertNotContains(res, 'name="totp_secret"')

    def test_totp_secret_absent_from_changelist(self):
        user = make_user("totp-list@example.test")
        secret = enable_2fa(user)
        res = self.client.get(reverse("admin:accounts_twofactorsetup_changelist"))
        self.assertEqual(res.status_code, 200)
        self.assertNotContains(res, secret)

    def test_admin_can_still_confirm_a_dkim_key_exists(self):
        """Excluding the secret must not remove the operator's ability to work."""
        owner = make_user("dkim-owner2@example.test")
        tenant = make_tenant(owner, name="DKIM Two", slug="dkim-two")
        domain = make_domain(tenant, "dkim-two.example")
        domain.dkim_private_key = "-----BEGIN PRIVATE KEY-----\nx\n"
        domain.save(update_fields=["dkim_private_key"])

        model_admin = site._registry[Domain]
        self.assertTrue(model_admin.has_dkim_private_key(domain))
        domain.dkim_private_key = ""
        self.assertFalse(model_admin.has_dkim_private_key(domain))

    # ── helpers ──────────────────────────────────────────────────────────────

    def _request(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/django-admin/")
        request.user = self.staff
        return request
