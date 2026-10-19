"""Encrypt existing Hub OTP secrets without losing live enrolments.

Requires a persistent independent HUB_2FA_FERNET_KEY when rows exist. On an
invalid/missing key migration aborts atomically rather than weaken protection.
The key must remain stable across deploys and must be backed up securely.
"""
from django.conf import settings
from django.db import migrations, models


def encrypt_existing(apps, schema_editor):
    from cryptography.fernet import Fernet
    from apps.accounts.totp_crypto import PREFIX
    Table = apps.get_model("accounts", "TwoFactorSetup")
    qs = Table.objects.using(schema_editor.connection.alias)
    if not qs.exists():
        return
    key = getattr(settings, "HUB_2FA_FERNET_KEY", "")
    if not key:
        raise RuntimeError("HUB_2FA_FERNET_KEY missing: refusing to migrate existing OTP secrets")
    try:
        cipher = Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("HUB_2FA_FERNET_KEY invalid") from exc
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    for row in qs.iterator(chunk_size=200):
        value = row.totp_secret
        if value.startswith(PREFIX):
            # Do not encrypt twice. Verify the provided key decrypts it.
            cipher.decrypt(value[len(PREFIX):].encode("ascii"))
            continue
        if len(value) != 32 or not set(value).issubset(allowed):
            raise RuntimeError("Unrecognized legacy TOTP secret; migration aborted")
        row.totp_secret = PREFIX + cipher.encrypt(value.encode("ascii")).decode("ascii")
        row.save(using=schema_editor.connection.alias, update_fields=["totp_secret"])


def decrypt_for_rollback(apps, schema_editor):
    """Reverse encryption BEFORE shrinking the column back to varchar(64).

    This intentionally restores the legacy plaintext form only if an operator
    explicitly reverses the migration as part of a controlled rollback.
    Missing/wrong keys abort the transaction rather than losing 2FA secrets.
    """
    from cryptography.fernet import Fernet
    from apps.accounts.totp_crypto import PREFIX
    Table = apps.get_model("accounts", "TwoFactorSetup")
    qs = Table.objects.using(schema_editor.connection.alias)
    if not qs.exists():
        return
    key = getattr(settings, "HUB_2FA_FERNET_KEY", "")
    if not key:
        raise RuntimeError("Hub TOTP rollback requires original encryption key")
    try:
        cipher = Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Hub TOTP rollback key is invalid") from exc
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    for row in qs.iterator(chunk_size=200):
        value = row.totp_secret
        if value.startswith(PREFIX):
            value = cipher.decrypt(value[len(PREFIX):].encode("ascii")).decode("ascii")
        if len(value) != 32 or not set(value).issubset(allowed):
            raise RuntimeError("Hub TOTP rollback detected invalid secret")
        row.totp_secret = value
        row.save(using=schema_editor.connection.alias, update_fields=["totp_secret"])


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_platformemailcode")]
    operations = [
        migrations.AlterField(
            model_name="twofactorsetup", name="totp_secret", field=models.TextField(),
        ),
        migrations.RunPython(encrypt_existing, decrypt_for_rollback),
    ]
