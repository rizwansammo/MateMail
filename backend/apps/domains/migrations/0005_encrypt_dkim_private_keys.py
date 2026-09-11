"""
Encrypt DKIM private keys that are already stored as plaintext.

INTERIM measure — DEC-007r is unchanged and P4 removes this column entirely.
Until then, a DKIM private key is a domain's authority to sign mail as itself,
and it was sitting in a TextField readable by anything with database access:
a backup, a replica, a snapshot, an accidental dumpdata.

Safety properties, in order of how much they matter:

* **Nothing is discarded.** A row is only rewritten after its ciphertext has
  been decrypted and compared byte-for-byte with the original. If that check
  ever failed the migration would stop, with the plaintext still in place.
* **Idempotent.** Rows already carrying the `dkimv1:` prefix are skipped, so
  re-running is harmless.
* **Reversible.** The reverse operation decrypts back to plaintext, so a
  rollback does not strand the data in a form the previous code cannot read.
* **Skips rather than fails when no key is configured.** A deployment that has
  not yet set DKIM_ENCRYPTION_KEY keeps working — the keystore reads legacy
  plaintext transparently and logs a warning on every access. Refusing to
  migrate would take the application down over a key that only matters at rest.
"""
from django.db import migrations


def encrypt_existing(apps, schema_editor):
    from apps.domains.keystore import (
        decrypt,
        encrypt,
        encryption_configured,
        is_encrypted,
    )

    Domain = apps.get_model("domains", "Domain")

    if not encryption_configured():
        print(
            "\n  DKIM_ENCRYPTION_KEY is not set — leaving existing DKIM private "
            "keys as plaintext.\n"
            "  Set it and re-run `manage.py migrate domains` to encrypt them."
        )
        return

    rows = Domain.objects.exclude(dkim_private_key="").only("id", "dkim_private_key")
    migrated = 0
    for domain in rows.iterator():
        if is_encrypted(domain.dkim_private_key):
            continue

        original = domain.dkim_private_key
        sealed = encrypt(original)

        # Prove the value survives a round trip before overwriting the only
        # copy. A DKIM key that cannot be recovered means the domain silently
        # sends unsigned mail, which is discovered by recipients, not by us.
        if decrypt(sealed) != original:
            raise RuntimeError(
                f"Refusing to encrypt DKIM key for domain {domain.pk}: the "
                f"encrypted value did not decrypt back to the original."
            )

        domain.dkim_private_key = sealed
        domain.save(update_fields=["dkim_private_key"])
        migrated += 1

    if migrated:
        print(f"\n  Encrypted {migrated} DKIM private key(s) at rest.")


def decrypt_existing(apps, schema_editor):
    """Reverse: restore plaintext so older code can still read the column."""
    from apps.domains.keystore import decrypt, encryption_configured, is_encrypted

    Domain = apps.get_model("domains", "Domain")

    if not encryption_configured():
        raise RuntimeError(
            "Cannot reverse this migration without DKIM_ENCRYPTION_KEY — the "
            "stored keys would be unreadable afterwards."
        )

    rows = Domain.objects.exclude(dkim_private_key="").only("id", "dkim_private_key")
    for domain in rows.iterator():
        if not is_encrypted(domain.dkim_private_key):
            continue
        domain.dkim_private_key = decrypt(domain.dkim_private_key)
        domain.save(update_fields=["dkim_private_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("domains", "0004_backfill_verification_tokens"),
    ]

    operations = [
        migrations.RunPython(encrypt_existing, decrypt_existing),
    ]
