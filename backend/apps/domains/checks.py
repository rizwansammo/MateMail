"""
Deployment checks for DKIM key storage.

`apps.domains.keystore.encrypt` deliberately falls back to plaintext when no
encryption key is configured, so that a missing environment variable cannot
stop a workspace adding a domain. That fallback is only defensible if the
misconfiguration is impossible to ship unnoticed — which is what this check is
for. `manage.py check --deploy` runs in the deployment pipeline, and an Error
here fails it.
"""
from django.conf import settings
from django.core.checks import Error, Tags, register


@register(Tags.security, deploy=True)
def dkim_encryption_key_configured(app_configs, **kwargs):
    from .keystore import encryption_configured

    if settings.DEBUG or encryption_configured():
        return []

    return [
        Error(
            "DKIM_ENCRYPTION_KEY is not set, so DKIM private keys would be "
            "stored in plaintext.",
            hint=(
                "Generate one with:\n"
                '  python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"\n'
                "Set it in the environment as DKIM_ENCRYPTION_KEY. It must be "
                "separate from DJANGO_SECRET_KEY (see DEC-007r). Then run "
                "`manage.py migrate domains` to encrypt existing rows."
            ),
            id="domains.E001",
        )
    ]
