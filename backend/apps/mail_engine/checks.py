"""
Deployment checks for the Mail Engine connection.

The stub adapter needs no configuration at all, which is what makes local
development and CI possible without an engine. That convenience is also the
risk: switching MAIL_ENGINE_ADAPTER to the real engine and forgetting the URL or
the API key produces an adapter that constructs fine and fails on the first
customer action, at which point a domain sits half-provisioned.

These checks make that misconfiguration impossible to deploy. They run under
`manage.py check --deploy`, which gates the pipeline, and they are scoped to the
real adapter so nothing here can break a stub-based CI job.

No check reads or reports a credential value. They assert presence and shape.
"""
from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

#: Hosts that mean "this container", which the engine never is: MateMail's
#: services run in their own containers and reach the engine across a Docker
#: network. A loopback URL here is the same mistake EMAIL_HOST=localhost was.
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _using_real_engine() -> bool:
    return str(getattr(settings, "MAIL_ENGINE_ADAPTER", "stub")).lower() == "mailcow"


@register(Tags.security, deploy=True)
def mail_engine_configured(app_configs, **kwargs):
    """Refuse a real-engine deployment that cannot actually reach the engine."""
    if not _using_real_engine():
        return []

    errors = []
    url = (getattr(settings, "MAIL_ENGINE_API_URL", "") or "").strip()
    key = (getattr(settings, "MAIL_ENGINE_API_KEY", "") or "").strip()

    if not url:
        errors.append(
            Error(
                "MAIL_ENGINE_ADAPTER is set to the real engine but "
                "MAIL_ENGINE_API_URL is empty.",
                hint=(
                    "Set MAIL_ENGINE_API_URL to the engine's private API base, "
                    "e.g. https://mx.matemail.online:8453 — the hostname, not an "
                    "IP address, so the certificate validates."
                ),
                id="mail_engine.E001",
            )
        )

    if not key:
        errors.append(
            Error(
                "MAIL_ENGINE_ADAPTER is set to the real engine but "
                "MAIL_ENGINE_API_KEY is empty.",
                hint=(
                    "Set MAIL_ENGINE_API_KEY from the credential issued by the "
                    "engine. Never commit it; it belongs in /opt/MateMail/.env."
                ),
                id="mail_engine.E002",
            )
        )

    if url:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        if parsed.scheme != "https":
            errors.append(
                Error(
                    f"MAIL_ENGINE_API_URL uses {parsed.scheme or 'no'} scheme; the "
                    "engine API must be reached over TLS.",
                    hint=(
                        "Use https://. The API key is sent as a request header on "
                        "every call, so plain HTTP puts it on the wire in clear — "
                        "on a Docker bridge that every container on this host can "
                        "reach. The engine also redirects HTTP to HTTPS, so plain "
                        "HTTP does not work in any case."
                    ),
                    id="mail_engine.E003",
                )
            )

        if host in _LOOPBACK:
            errors.append(
                Warning(
                    f"MAIL_ENGINE_API_URL points at {host}, which is this "
                    "container, not the Mail Engine.",
                    hint=(
                        "MateMail runs in its own container and reaches the engine "
                        "across a Docker network. Use the engine's hostname so "
                        "certificate verification succeeds — an IP address or a "
                        "loopback address cannot match the certificate."
                    ),
                    id="mail_engine.W001",
                )
            )

    return errors
