"""
DKIM private key encryption at rest (INTERIM — DEC-007r).

P4 removes `Domain.dkim_private_key` from Django entirely and moves generation
and storage into the Mail Engine. This is not that. It exists because the
column exists *today*, and a DKIM private key is a domain's authority to sign
mail as itself — sitting in a plain TextField it was readable from any backup,
replica or snapshot.

The tests that matter most are the ones about not losing anything: an
unreadable DKIM key means a domain silently sends unsigned mail, which is
discovered by recipients rather than by us.
"""
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from apps.domains.keystore import (
    PREFIX,
    DkimKeyUnavailable,
    decrypt,
    encrypt,
    encryption_configured,
    is_encrypted,
)
from apps.domains.models import Domain
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    auth_client,
    disable_throttling,
    make_domain,
    make_tenant,
    make_user,
)

KEY_A = Fernet.generate_key().decode()
KEY_B = Fernet.generate_key().decode()

SAMPLE_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEAxfakekeymaterialfortestsonly0000000000000000000\n"
    "-----END RSA PRIVATE KEY-----\n"
)

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "dkim-tests",
    }
}


@override_settings(DKIM_ENCRYPTION_KEYS=KEY_A)
class KeystoreTest(TestCase):
    def test_configured(self):
        self.assertTrue(encryption_configured())

    def test_round_trip(self):
        sealed = encrypt(SAMPLE_PEM)
        self.assertEqual(decrypt(sealed), SAMPLE_PEM)

    def test_the_stored_value_is_not_the_key(self):
        """The point of the exercise."""
        sealed = encrypt(SAMPLE_PEM)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", sealed)
        self.assertNotIn("fakekeymaterial", sealed)

    def test_the_stored_value_is_marked(self):
        self.assertTrue(encrypt(SAMPLE_PEM).startswith(PREFIX))
        self.assertTrue(is_encrypted(encrypt(SAMPLE_PEM)))

    def test_encryption_is_not_deterministic(self):
        """Two identical keys must not produce identical ciphertext."""
        self.assertNotEqual(encrypt(SAMPLE_PEM), encrypt(SAMPLE_PEM))

    def test_empty_stays_empty(self):
        self.assertEqual(encrypt(""), "")
        self.assertEqual(decrypt(""), "")

    def test_already_encrypted_is_not_double_wrapped(self):
        once = encrypt(SAMPLE_PEM)
        self.assertEqual(encrypt(once), once)

    def test_legacy_plaintext_is_still_readable(self):
        """
        A deployment that upgrades before running the data migration must keep
        signing mail. Refusing to read these rows would break DKIM for every
        existing domain at the moment of upgrade.
        """
        with self.assertLogs("apps.domains.keystore", level="WARNING"):
            self.assertEqual(decrypt(SAMPLE_PEM), SAMPLE_PEM)

    def test_tampered_ciphertext_raises_rather_than_returning_garbage(self):
        sealed = encrypt(SAMPLE_PEM)
        tampered = sealed[:-4] + "AAAA"
        with self.assertRaises(DkimKeyUnavailable):
            decrypt(tampered)

    def test_an_unreadable_value_raises_rather_than_looking_absent(self):
        """
        Returning "" would read as "this domain has no DKIM key" and the domain
        would be provisioned to send unsigned mail.
        """
        sealed = encrypt(SAMPLE_PEM)
        with override_settings(DKIM_ENCRYPTION_KEYS=KEY_B):
            with self.assertRaises(DkimKeyUnavailable):
                decrypt(sealed)

    def test_rotation_reads_with_either_key(self):
        old = encrypt(SAMPLE_PEM)
        with override_settings(DKIM_ENCRYPTION_KEYS=f"{KEY_B},{KEY_A}"):
            # New key encrypts, both are tried on read.
            self.assertEqual(decrypt(old), SAMPLE_PEM)
            fresh = encrypt(SAMPLE_PEM)
            self.assertEqual(decrypt(fresh), SAMPLE_PEM)
        # The freshly written value is not readable with only the old key.
        with self.assertRaises(DkimKeyUnavailable):
            decrypt(fresh)


class UnconfiguredTest(TestCase):
    """
    With no key set, storage degrades to plaintext loudly rather than breaking.

    Raising would mean a deployment that forgot the variable could not add a
    domain at all. The deploy check below is what stops that state reaching
    production.
    """

    @override_settings(DKIM_ENCRYPTION_KEYS="")
    def test_encrypt_falls_back_to_plaintext_and_says_so(self):
        with self.assertLogs("apps.domains.keystore", level="ERROR") as logs:
            stored = encrypt(SAMPLE_PEM)
        self.assertEqual(stored, SAMPLE_PEM)
        self.assertIn("PLAINTEXT", "".join(logs.output))

    @override_settings(DKIM_ENCRYPTION_KEYS="", DEBUG=False)
    def test_the_deploy_check_refuses_this_configuration(self):
        from apps.domains.checks import dkim_encryption_key_configured

        errors = dkim_encryption_key_configured(None)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].id, "domains.E001")

    @override_settings(DKIM_ENCRYPTION_KEYS=KEY_A, DEBUG=False)
    def test_the_deploy_check_passes_when_configured(self):
        from apps.domains.checks import dkim_encryption_key_configured

        self.assertEqual(dkim_encryption_key_configured(None), [])


@override_settings(
    DKIM_ENCRYPTION_KEYS=KEY_A,
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM_CACHE,
)
class ModelAndExposureTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.owner = make_user("dkim@example.test")
        self.tenant = make_tenant(self.owner, name="Dkim", slug="dkim")

    def test_the_accessor_round_trips_through_the_column(self):
        domain = make_domain(self.tenant, "keys.example")
        domain.dkim_private_key_pem = SAMPLE_PEM
        domain.save(update_fields=["dkim_private_key"])

        stored = Domain.objects.get(pk=domain.pk)
        self.assertTrue(is_encrypted(stored.dkim_private_key))
        self.assertEqual(stored.dkim_private_key_pem, SAMPLE_PEM)

    def test_presence_does_not_touch_the_key(self):
        domain = make_domain(self.tenant, "presence.example")
        self.assertFalse(domain.has_dkim_private_key)
        domain.dkim_private_key_pem = SAMPLE_PEM
        self.assertTrue(domain.has_dkim_private_key)

    def test_a_created_domain_stores_no_private_key_at_all(self):
        """
        DEC-007r stage 1 (P4A). This previously asserted that creation stored an
        *encrypted* private key. It now asserts the stronger property: MateMail
        no longer generates one, so there is nothing to encrypt.

        The old key signed nothing — no engine ever held it — so it was pure
        liability. The engine generates the pair at provisioning and keeps the
        private half.
        """
        res = auth_client(self.owner, self.tenant).post(
            "/api/domains/", {"domain": "created.example"}
        )
        self.assertEqual(res.status_code, 201)

        domain = Domain.objects.get(domain="created.example")
        self.assertEqual(
            domain.dkim_private_key, "", "MateMail generated a DKIM private key"
        )
        self.assertFalse(domain.has_dkim_private_key)
        self.assertEqual(
            domain.dkim_public_key, "", "public material must come from the engine"
        )

    def test_the_private_key_is_not_in_the_api_response(self):
        res = auth_client(self.owner, self.tenant).post(
            "/api/domains/", {"domain": "api.example"}
        )
        body = str(res.data)
        self.assertNotIn("PRIVATE KEY", body)
        self.assertNotIn("dkim_private_key", body)

    def test_the_private_key_is_not_in_the_detail_or_list_responses(self):
        make_domain(self.tenant, "listed.example")
        api = auth_client(self.owner, self.tenant)
        for path in ("/api/domains/",):
            self.assertNotIn("dkim_private_key", str(api.get(path).data))

    def test_django_admin_never_renders_the_key(self):
        from apps.domains.admin import DomainAdmin

        self.assertIn("dkim_private_key", DomainAdmin.exclude)
        self.assertIn("has_dkim_private_key", DomainAdmin.readonly_fields)

    def test_the_serializer_has_no_private_key_field(self):
        from apps.domains.serializers import DomainSerializer

        self.assertNotIn("dkim_private_key", DomainSerializer.Meta.fields)


@override_settings(
    DKIM_ENCRYPTION_KEYS=KEY_A,
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM_CACHE,
)
class DataMigrationTest(TestCase):
    """
    The migration that rewrites existing plaintext rows.

    Exercised directly against the real model. This is the one piece of P3c
    that touches key material that already exists, so "nothing is lost" is the
    property worth proving rather than assuming.
    """

    def setUp(self):
        self.owner = make_user("migrate@example.test")
        self.tenant = make_tenant(self.owner, name="Mig", slug="mig")

    def test_plaintext_rows_are_encrypted_and_recoverable(self):
        from importlib import import_module

        migration = import_module(
            "apps.domains.migrations.0005_encrypt_dkim_private_keys"
        )

        domain = make_domain(self.tenant, "legacy.example")
        Domain.objects.filter(pk=domain.pk).update(dkim_private_key=SAMPLE_PEM)

        class FakeApps:
            @staticmethod
            def get_model(app_label, model_name):
                return Domain

        migration.encrypt_existing(FakeApps, None)

        stored = Domain.objects.get(pk=domain.pk)
        self.assertTrue(is_encrypted(stored.dkim_private_key))
        self.assertEqual(stored.dkim_private_key_pem, SAMPLE_PEM)

    def test_the_migration_is_idempotent(self):
        from importlib import import_module

        migration = import_module(
            "apps.domains.migrations.0005_encrypt_dkim_private_keys"
        )

        domain = make_domain(self.tenant, "twice.example")
        Domain.objects.filter(pk=domain.pk).update(dkim_private_key=SAMPLE_PEM)

        class FakeApps:
            @staticmethod
            def get_model(app_label, model_name):
                return Domain

        migration.encrypt_existing(FakeApps, None)
        first = Domain.objects.get(pk=domain.pk).dkim_private_key
        migration.encrypt_existing(FakeApps, None)
        self.assertEqual(Domain.objects.get(pk=domain.pk).dkim_private_key, first)

    def test_the_migration_reverses_to_plaintext(self):
        from importlib import import_module

        migration = import_module(
            "apps.domains.migrations.0005_encrypt_dkim_private_keys"
        )

        domain = make_domain(self.tenant, "reverse.example")
        Domain.objects.filter(pk=domain.pk).update(dkim_private_key=SAMPLE_PEM)

        class FakeApps:
            @staticmethod
            def get_model(app_label, model_name):
                return Domain

        migration.encrypt_existing(FakeApps, None)
        migration.decrypt_existing(FakeApps, None)
        self.assertEqual(
            Domain.objects.get(pk=domain.pk).dkim_private_key, SAMPLE_PEM
        )

    @override_settings(DKIM_ENCRYPTION_KEYS="")
    def test_without_a_key_the_migration_leaves_rows_alone(self):
        """
        Skipping is correct: refusing to migrate would take the application
        down over a key that only matters at rest, and the keystore reads
        plaintext transparently in the meantime.
        """
        from importlib import import_module

        migration = import_module(
            "apps.domains.migrations.0005_encrypt_dkim_private_keys"
        )

        domain = make_domain(self.tenant, "nokey.example")
        Domain.objects.filter(pk=domain.pk).update(dkim_private_key=SAMPLE_PEM)

        class FakeApps:
            @staticmethod
            def get_model(app_label, model_name):
                return Domain

        migration.encrypt_existing(FakeApps, None)
        self.assertEqual(
            Domain.objects.get(pk=domain.pk).dkim_private_key, SAMPLE_PEM
        )
