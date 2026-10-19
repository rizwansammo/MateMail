"""Independent synthetic Hub/PostBox MFA Fernet continuity across processes."""
import json
import os
import sys
from pathlib import Path
import django

# When executed as scripts/...py, Python adds /app/scripts, not /app, to
# sys.path. The settings package and app modules live in the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from cryptography.fernet import Fernet
from django.test import override_settings
from django.core.exceptions import ImproperlyConfigured
from apps.accounts.totp_crypto import encrypt_secret, decrypt_secret
from apps.postbox.two_factor_core import encrypt_totp_secret, decrypt_totp_secret

FIXTURE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def rejects_wrong_key(decrypt, ciphertext, key_name, replacement):
    try:
        with override_settings(**{key_name: replacement}):
            decrypt(ciphertext)
    except (ValueError, ImproperlyConfigured):
        return
    raise AssertionError("MFA secret accepted a different or missing key")


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("create", "verify"):
        raise SystemExit("usage: phase7c_key_continuity.py create|verify <fixture.json>")
    hub = os.environ.get("HUB_2FA_FERNET_KEY", "")
    postbox = os.environ.get("POSTBOX_2FA_FERNET_KEY", "")
    if not hub or not postbox or hub == postbox:
        raise AssertionError("Distinct nonempty CI-only MFA keys required")
    Fernet(hub.encode("ascii"))
    Fernet(postbox.encode("ascii"))
    destination = Path(sys.argv[2])
    if sys.argv[1] == "create":
        payload = {
            "hub": encrypt_secret(FIXTURE),
            "postbox": encrypt_totp_secret(FIXTURE),
        }
        fd = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        assert FIXTURE not in destination.read_text(encoding="utf-8")
        print("PASS synthetic MFA encrypted fixtures generated in first process")
        return
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert decrypt_secret(payload["hub"]) == FIXTURE
    assert decrypt_totp_secret(payload["postbox"]) == FIXTURE
    rejects_wrong_key(decrypt_secret, payload["hub"], "HUB_2FA_FERNET_KEY", postbox)
    rejects_wrong_key(decrypt_totp_secret, payload["postbox"], "POSTBOX_2FA_FERNET_KEY", hub)
    rejects_wrong_key(decrypt_secret, payload["hub"], "HUB_2FA_FERNET_KEY", "")
    rejects_wrong_key(decrypt_totp_secret, payload["postbox"], "POSTBOX_2FA_FERNET_KEY", "")
    print("PASS Hub and PostBox decrypt independently after fresh interpreter start")
    print("PASS key swap and missing key fail closed")


if __name__ == "__main__":
    main()
