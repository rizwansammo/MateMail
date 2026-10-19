"""Phase 7C — CI-only Compose MFA gate and key forwarding contract.

No containers are started and no production environment or secrets are read.
The caller supplies synthetic placeholders for existing required Compose fields.
"""
import base64
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KEYS = ("HUB_2FA_FERNET_KEY", "POSTBOX_2FA_FERNET_KEY")
FLAGS = (
    "ORGANIZATION_2FA_POLICY_ENABLED",
    "ORGANIZATION_2FA_ENFORCEMENT_READY",
    "POSTBOX_2FA_ENABLED",
    "SMTP_IMAP_SECURITY_ENABLED",
)
SERVICES = ("backend", "migrate", "celery-worker", "celery-beat")


def resolve(overrides):
    env = os.environ.copy()
    for key in (*KEYS, *FLAGS):
        env.pop(key, None)
    env.update(overrides)
    result = subprocess.run(
        ["docker", "compose", "-f", "docker-compose.yml", "config", "--format", "json"],
        cwd=ROOT, env=env, text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def main():
    defaults = resolve({})
    for service in SERVICES:
        actual = defaults["services"][service]["environment"]
        for key in KEYS:
            assert actual[key] == "", (service, key, "unexpected configured key")
        for key in FLAGS:
            assert str(actual[key]).lower() == "false", (service, key, "enabled by default")

    def synthetic_key():
        return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")

    hub, postbox = synthetic_key(), synthetic_key()
    assert hub != postbox
    activated = resolve({
        "HUB_2FA_FERNET_KEY": hub,
        "POSTBOX_2FA_FERNET_KEY": postbox,
        **{key: "True" for key in FLAGS},
    })
    for service in SERVICES:
        actual = activated["services"][service]["environment"]
        assert actual[KEYS[0]] == hub
        assert actual[KEYS[1]] == postbox
        for flag in FLAGS:
            assert str(actual[flag]).lower() == "true", (service, flag)

    print("PASS MFA flags OFF and keys empty by default across backend processes")
    print("PASS independent ephemeral Hub/PostBox keys + 4 MFA flags forwarded to all services")


if __name__ == "__main__":
    main()
