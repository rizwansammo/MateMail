"""Deployment checks for the Native Mail Engine."""
from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, register

_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _adapter_name() -> str:
    return str(getattr(settings, "MAIL_ENGINE_ADAPTER", "stub")).lower().strip()


@register(Tags.security, deploy=True)
def mail_engine_adapter_is_known(app_configs, **kwargs):
    name = _adapter_name()
    if name in ("stub", "native"):
        return []
    return [
        Error(
            f"MAIL_ENGINE_ADAPTER={name!r} is not a known adapter.",
            hint="Valid values: 'stub', 'native'.",
            id="mail_engine.E010",
        )
    ]


@register(Tags.security, deploy=True)
def native_engine_configured(app_configs, **kwargs):
    if _adapter_name() != "native":
        return []

    errors = []
    url = (getattr(settings, "NATIVE_ENGINE_API_URL", "") or "").strip()
    secret = (getattr(settings, "NATIVE_ENGINE_API_SECRET", "") or "").strip()

    if not url:
        errors.append(Error(
            "MAIL_ENGINE_ADAPTER is 'native' but NATIVE_ENGINE_API_URL is empty.",
            hint=(
                "Set it to the Native API on matemail_engine_link, for example "
                "http://matemail-native-api:8451."
            ),
            id="mail_engine.E011",
        ))
    if not secret:
        errors.append(Error(
            "MAIL_ENGINE_ADAPTER is 'native' but NATIVE_ENGINE_API_SECRET is empty.",
            hint="Set it from the Native Engine's NATIVE_API_SECRET.",
            id="mail_engine.E012",
        ))

    if url:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in _LOOPBACK:
            errors.append(Error(
                f"NATIVE_ENGINE_API_URL points at {host!r}, which is this container.",
                hint="Use the Native API service name on matemail_engine_link.",
                id="mail_engine.E013",
            ))
        if parsed.scheme not in ("http", "https"):
            errors.append(Error(
                f"NATIVE_ENGINE_API_URL has scheme {parsed.scheme!r}.",
                hint="Use http:// on the private engine network.",
                id="mail_engine.E014",
            ))
    return errors
