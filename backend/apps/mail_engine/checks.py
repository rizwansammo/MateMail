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


def _adapter_name() -> str:
    return str(getattr(settings, "MAIL_ENGINE_ADAPTER", "stub")).lower().strip()


def _using_real_engine() -> bool:
    """mailcow specifically — these checks assert mailcow's requirements."""
    return _adapter_name() == "mailcow"


@register(Tags.security, deploy=True)
def mail_engine_adapter_is_known(app_configs, **kwargs):
    """
    An unrecognised adapter name must never reach a deploy.

    The factory refuses it at runtime, but that refusal happens on the first
    customer action. Catching it in `check --deploy` moves the failure to the
    pipeline, where it costs nothing.
    """
    name = _adapter_name()
    if name in ("stub", "mailcow", "native"):
        return []
    return [
        Error(
            f"MAIL_ENGINE_ADAPTER={name!r} is not a known adapter.",
            hint="Valid values: 'stub', 'mailcow', 'native'.",
            id="mail_engine.E010",
        )
    ]


@register(Tags.security, deploy=True)
def native_engine_configured(app_configs, **kwargs):
    """
    Refuse a Native deployment that cannot reach the Native API.

    Same reasoning as the mailcow checks: an adapter with no URL constructs
    perfectly well and fails on the first customer action, by which point a
    domain is half-provisioned. The difference is the transport — the Native API
    lives on an INTERNAL Docker network with no published ports and no
    certificate until NE0.9, so HTTPS is not required here and demanding it
    would only push someone toward a self-signed certificate nobody verifies.
    """
    if _adapter_name() != "native":
        return []

    errors = []
    url = (getattr(settings, "NATIVE_ENGINE_API_URL", "") or "").strip()
    secret = (getattr(settings, "NATIVE_ENGINE_API_SECRET", "") or "").strip()

    if not url:
        errors.append(Error(
            "MAIL_ENGINE_ADAPTER is 'native' but NATIVE_ENGINE_API_URL is empty.",
            hint=("Set it to the engine API on the private network, e.g. "
                  "http://matemail-native-api:8451 — a service name, never an IP "
                  "or a container id, both of which change."),
            id="mail_engine.E011",
        ))
    if not secret:
        errors.append(Error(
            "MAIL_ENGINE_ADAPTER is 'native' but NATIVE_ENGINE_API_SECRET is empty.",
            hint=("Set it from the engine's NATIVE_API_SECRET. It belongs in "
                  "/opt/MateMail/.env and is never committed."),
            id="mail_engine.E012",
        ))

    if url:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in _LOOPBACK:
            errors.append(Error(
                f"NATIVE_ENGINE_API_URL points at {host!r}, which is this "
                f"container, not the engine.",
                hint=("MateMail and the Native Engine run in separate containers "
                      "and meet on the matemail_engine_link network. Use the "
                      "engine's service name."),
                id="mail_engine.E013",
            ))
        if parsed.scheme not in ("http", "https"):
            errors.append(Error(
                f"NATIVE_ENGINE_API_URL has scheme {parsed.scheme!r}.",
                hint="Use http:// on the private engine network.",
                id="mail_engine.E014",
            ))

    # A deployment that names the Native engine while still holding mailcow
    # credentials is not an error — mailcow stays configured on purpose as the
    # rollback path (NE5) — so nothing is asserted about MAIL_ENGINE_API_*.
    return errors


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
