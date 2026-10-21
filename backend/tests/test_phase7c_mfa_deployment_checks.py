"""Phase 7C release: fail-closed MFA deployment configuration checks.

These tests do not access production configuration or encryption keys.
"""
from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from apps.tenants.checks import mfa_deployment_guards

HUB = Fernet.generate_key().decode("ascii")
POSTBOX = Fernet.generate_key().decode("ascii")
BASE = {
    "POSTBOX_2FA_ENABLED": False,
    "SMTP_IMAP_SECURITY_ENABLED": False,
    "ORGANIZATION_2FA_POLICY_ENABLED": False,
    "ORGANIZATION_2FA_ENFORCEMENT_READY": False,
    "ORGANIZATION_2FA_NATIVE_ENFORCEMENT_ACCEPTED": False,
    "MAIL_ENGINE_ADAPTER": "stub",
    "HUB_2FA_FERNET_KEY": "",
    "POSTBOX_2FA_FERNET_KEY": "",
}


def check_ids(**values):
    with override_settings(**{**BASE, **values}):
        return {issue.id for issue in mfa_deployment_guards(None)}


class MFADeploymentGuardTest(SimpleTestCase):
    def test_all_flags_off_requires_no_keys_or_native_adapter(self):
        self.assertEqual(check_ids(), set())

    def test_policy_only_encourage_is_allowed(self):
        self.assertEqual(check_ids(ORGANIZATION_2FA_POLICY_ENABLED=True), set())

    def test_postbox_without_smtp_imap_is_rejected(self):
        self.assertIn("tenants.E030", check_ids(
            POSTBOX_2FA_ENABLED=True, POSTBOX_2FA_FERNET_KEY=POSTBOX,
            MAIL_ENGINE_ADAPTER="native",
        ))

    def test_smtp_imap_without_postbox_is_rejected(self):
        self.assertIn("tenants.E030", check_ids(
            SMTP_IMAP_SECURITY_ENABLED=True, POSTBOX_2FA_FERNET_KEY=POSTBOX,
            MAIL_ENGINE_ADAPTER="native",
        ))

    def test_mailbox_security_without_native_adapter_is_rejected(self):
        self.assertIn("tenants.E031", check_ids(
            POSTBOX_2FA_ENABLED=True, SMTP_IMAP_SECURITY_ENABLED=True,
            POSTBOX_2FA_FERNET_KEY=POSTBOX,
        ))

    def test_missing_or_malformed_postbox_key_is_rejected(self):
        for key in ("", "not-a-fernet-key"):
            with self.subTest(key_empty=not bool(key)):
                self.assertIn("tenants.E033", check_ids(
                    POSTBOX_2FA_ENABLED=True, SMTP_IMAP_SECURITY_ENABLED=True,
                    POSTBOX_2FA_FERNET_KEY=key, MAIL_ENGINE_ADAPTER="native",
                ))

    def test_valid_mailbox_only_configuration_passes(self):
        self.assertEqual(check_ids(
            POSTBOX_2FA_ENABLED=True, SMTP_IMAP_SECURITY_ENABLED=True,
            POSTBOX_2FA_FERNET_KEY=POSTBOX, MAIL_ENGINE_ADAPTER="native",
        ), set())

    def test_require_without_mailbox_protection_and_policy_is_rejected(self):
        issues = check_ids(ORGANIZATION_2FA_ENFORCEMENT_READY=True)
        self.assertIn("tenants.E032", issues)
        self.assertIn("tenants.E037", issues)
        self.assertIn("tenants.E036", issues)

    def test_require_missing_hub_key_and_duplicate_key_are_rejected(self):
        values = dict(
            POSTBOX_2FA_ENABLED=True,
            SMTP_IMAP_SECURITY_ENABLED=True,
            ORGANIZATION_2FA_POLICY_ENABLED=True,
            ORGANIZATION_2FA_ENFORCEMENT_READY=True,
            MAIL_ENGINE_ADAPTER="native",
            POSTBOX_2FA_FERNET_KEY=POSTBOX,
        )
        self.assertIn("tenants.E034", check_ids(**values))
        self.assertIn("tenants.E035", check_ids(
            **{**values, "HUB_2FA_FERNET_KEY": POSTBOX},
        ))

    def test_require_cannot_be_unlocked_just_by_setting_flags(self):
        issues = check_ids(
            POSTBOX_2FA_ENABLED=True, SMTP_IMAP_SECURITY_ENABLED=True,
            ORGANIZATION_2FA_POLICY_ENABLED=True,
            ORGANIZATION_2FA_ENFORCEMENT_READY=True,
            MAIL_ENGINE_ADAPTER="native",
            POSTBOX_2FA_FERNET_KEY=POSTBOX,
            HUB_2FA_FERNET_KEY=HUB,
        )
        self.assertEqual(issues, {"tenants.E036"})

    def test_accepted_enforcement_requires_all_independent_gates(self):
        required = dict(
            POSTBOX_2FA_ENABLED=True,
            SMTP_IMAP_SECURITY_ENABLED=True,
            ORGANIZATION_2FA_POLICY_ENABLED=True,
            ORGANIZATION_2FA_ENFORCEMENT_READY=True,
            ORGANIZATION_2FA_NATIVE_ENFORCEMENT_ACCEPTED=True,
            MAIL_ENGINE_ADAPTER="native",
            POSTBOX_2FA_FERNET_KEY=POSTBOX,
            HUB_2FA_FERNET_KEY=HUB,
        )
        self.assertEqual(check_ids(**required), set())
        for omitted in (
            "POSTBOX_2FA_ENABLED",
            "SMTP_IMAP_SECURITY_ENABLED",
            "ORGANIZATION_2FA_POLICY_ENABLED",
            "ORGANIZATION_2FA_ENFORCEMENT_READY",
            "ORGANIZATION_2FA_NATIVE_ENFORCEMENT_ACCEPTED",
        ):
            with self.subTest(gate=omitted):
                self.assertIn("tenants.E036", check_ids(
                    **{**required, omitted: False},
                ))
