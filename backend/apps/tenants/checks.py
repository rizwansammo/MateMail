from django.conf import settings
from django.core.checks import Error, Tags, register

_GUARD = "apps.tenants.host_middleware.CustomHostnameHostGuardMiddleware"
_SURFACE_GUARD = "apps.tenants.surface_middleware.CustomHostnameSurfaceGuardMiddleware"


@register(Tags.security)
def custom_hostname_host_guard_check(app_configs, **kwargs):
    """
    Refuse an unsafe dynamic-host deployment.

    Dynamic customer names require Django's static allowlist to be open, so the
    application guard becomes the security boundary. A missing/reordered guard
    with ALLOWED_HOSTS=["*"] would be a host-header vulnerability rather than a
    feature misconfiguration.
    """
    if not getattr(settings, "CUSTOM_HOSTS_DYNAMIC_ENABLED", False):
        return []

    errors = []
    middleware = list(getattr(settings, "MIDDLEWARE", ()))
    if not middleware or middleware[0] != _GUARD:
        errors.append(
            Error(
                "Dynamic custom hosts require CustomHostnameHostGuardMiddleware first.",
                hint=f"Put {_GUARD} at MIDDLEWARE[0].",
                id="tenants.E020",
            )
        )

    if len(middleware) < 2 or middleware[1] != _SURFACE_GUARD:
        errors.append(
            Error(
                "Dynamic custom hosts require CustomHostnameSurfaceGuardMiddleware second.",
                hint=f"Put {_SURFACE_GUARD} at MIDDLEWARE[1].",
                id="tenants.E023",
            )
        )

    if list(getattr(settings, "ALLOWED_HOSTS", ())) != ["*"]:
        errors.append(
            Error(
                "Dynamic custom hosts require Django ALLOWED_HOSTS=['*'].",
                hint=(
                    "Do not enumerate customer names in ALLOWED_HOSTS; the first "
                    "middleware enforces the database-backed allowlist."
                ),
                id="tenants.E021",
            )
        )

    if not getattr(settings, "CUSTOM_HOST_FIXED_HOSTS", ()):
        errors.append(
            Error(
                "CUSTOM_HOST_FIXED_HOSTS must not be empty in dynamic-host mode.",
                id="tenants.E022",
            )
        )

    return errors


@register(Tags.security, deploy=True)
def mfa_deployment_guards(app_configs, **kwargs):
    """Refuse partial MFA rollout without ever inspecting real customer secrets.

    These are configuration-level checks, *not* proof of Dovecot reachability,
    a completed backup, or production approval. Normal OFF-by-default builds
    remain valid and are never required to provide unused MFA keys.
    """
    from cryptography.fernet import Fernet

    postbox = bool(getattr(settings, "POSTBOX_2FA_ENABLED", False))
    clients = bool(getattr(settings, "SMTP_IMAP_SECURITY_ENABLED", False))
    policy = bool(getattr(settings, "ORGANIZATION_2FA_POLICY_ENABLED", False))
    require = bool(getattr(settings, "ORGANIZATION_2FA_ENFORCEMENT_READY", False))
    errors = []

    if postbox != clients:
        errors.append(Error(
            "PostBox 2FA and dedicated SMTP/IMAP security must be enabled together.",
            hint="Keep both flags OFF, or release both after Native client policy acceptance.",
            id="tenants.E030",
        ))

    if (postbox or clients or require) and str(getattr(settings, "MAIL_ENGINE_ADAPTER", "")).lower() != "native":
        errors.append(Error(
            "Mailbox MFA requires the Native mail engine adapter.",
            hint="Keep the MFA flags OFF until MAIL_ENGINE_ADAPTER=native is ready.",
            id="tenants.E031",
        ))

    if require and not (postbox and clients):
        errors.append(Error(
            "Require MFA cannot run without both mailbox MFA and protected SMTP/IMAP.",
            hint="Leave Require enforcement OFF until all mail-client release gates pass.",
            id="tenants.E037",
        ))

    if require and not policy:
        errors.append(Error(
            "Require MFA enforcement cannot run while organization policy is disabled.",
            hint="Do not set ORGANIZATION_2FA_ENFORCEMENT_READY before policy acceptance.",
            id="tenants.E032",
        ))

    # An encryption key can only be checked for syntax/uniqueness here. A
    # successful check does NOT confirm persistence across future restarts.
    if postbox or clients or require:
        key = getattr(settings, "POSTBOX_2FA_FERNET_KEY", "")
        try:
            Fernet(key.encode("ascii") if isinstance(key, str) else key)
        except (ValueError, TypeError, AttributeError, UnicodeError):
            errors.append(Error(
                "PostBox MFA requires a persistent valid Fernet encryption key.",
                hint="Provision POSTBOX_2FA_FERNET_KEY from a persistent secret source.",
                id="tenants.E033",
            ))

    if require:
        key = getattr(settings, "HUB_2FA_FERNET_KEY", "")
        try:
            Fernet(key.encode("ascii") if isinstance(key, str) else key)
        except (ValueError, TypeError, AttributeError, UnicodeError):
            errors.append(Error(
                "Require MFA needs a persistent valid Hub Fernet encryption key.",
                hint="Provision HUB_2FA_FERNET_KEY before controlled rollout.",
                id="tenants.E034",
            ))
        if key and key == getattr(settings, "POSTBOX_2FA_FERNET_KEY", ""):
            errors.append(Error(
                "Hub and PostBox MFA must not share the same encryption key.",
                hint="Provide independently generated encryption keys.",
                id="tenants.E035",
            ))
        from .security_policy import can_activate_require
        if not can_activate_require():
            errors.append(Error(
                "Organization Require MFA is still behind its final release gate.",
                hint="Complete the independent release acceptance and approved activation first.",
                id="tenants.E036",
            ))
    return errors
